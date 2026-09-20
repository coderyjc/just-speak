from __future__ import annotations

import threading
import time
import wave

from asr_client.jobs.realtime_job import GapRecoveryJob, RealtimeJob
from asr_client.models import (
    AppConfig,
    AudioChunk,
    SAMPLE_RATE,
    SessionStatus,
    TextModelResult,
)
from asr_client.providers.base import MockAsrProvider
from asr_client.storage.database import Database


class SimulatedRealtimeJob(RealtimeJob):
    def _capture(self) -> None:
        self._input_rate = SAMPLE_RATE
        packet = b"\x10\x00" * (SAMPLE_RATE // 10)
        for _ in range(31):
            self._audio_queue.put(packet)


class SimulatedTextProvider:
    def __init__(self) -> None:
        self.calls = []

    def complete(self, prompt_template: str, text: str) -> TextModelResult:
        self.calls.append((prompt_template, text))
        output = "清晰后的文本" if len(self.calls) == 1 else "定向修复后的文本"
        return TextModelResult(output, f"llm-{len(self.calls)}", {"ok": True})


class HangingPolishProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.release = threading.Event()

    def complete(self, prompt_template: str, text: str) -> TextModelResult:
        self.calls += 1
        if self.calls == 1:
            return TextModelResult("清晰后的保底文本", "llm-clarity", {"ok": True})
        self.release.wait(5)
        return TextModelResult("不应等待到这里", "llm-polish", {"ok": True})


class CountingStreamProvider:
    def __init__(self) -> None:
        self.backend = MockAsrProvider()
        self.streams = []

    def start_stream(self, sink):
        stream = self.backend.start_stream(sink)
        self.streams.append(stream)
        return stream

    def transcribe_file(self, path, source_prefix):
        return self.backend.transcribe_file(path, source_prefix)


class PausableSimulatedRealtimeJob(RealtimeJob):
    def _capture(self) -> None:
        self._input_rate = SAMPLE_RATE
        packet = b"\x10\x00" * (SAMPLE_RATE // 10)
        while not self._stop_requested.is_set():
            if self._paused.is_set() or self._stream_restart_requested.is_set():
                self._stop_requested.wait(0.01)
                continue
            self._audio_queue.put(packet)
            self._stop_requested.wait(0.02)


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_simulated_realtime_pipeline_persists_audio_and_text(tmp_path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    config = AppConfig(data_dir=str(tmp_path))
    updates = []
    job = SimulatedRealtimeJob(
        db, MockAsrProvider(), config, updates.append, stop_timeout=1
    )
    job.run()
    row = db.get_session(job.session_id)
    assert row["status"] == SessionStatus.COMPLETED
    assert row["saved_samples"] == 31 * (SAMPLE_RATE // 10)
    assert job.wav_path.exists()
    assert "模拟实时识别" in db.transcript(job.session_id)
    assert any(update.kind == "completed" for update in updates)
    db.close()


def test_abort_skips_the_text_pipeline_and_marks_the_session_cancelled(tmp_path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    updates = []
    job = SimulatedRealtimeJob(
        db,
        MockAsrProvider(),
        AppConfig(data_dir=str(tmp_path), llm_model="qwen-plus"),
        updates.append,
        stop_timeout=1,
        text_provider=SimulatedTextProvider(),
    )

    job.abort()
    job.run()

    assert db.get_session(job.session_id)["status"] == SessionStatus.CANCELLED
    assert any(update.kind == "cancelled" for update in updates)
    assert not any(update.kind == "pipeline" for update in updates)
    db.close()


def test_pause_keeps_job_open_and_resume_starts_a_new_cloud_stream(tmp_path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    provider = CountingStreamProvider()
    updates = []
    job = PausableSimulatedRealtimeJob(
        db,
        provider,
        AppConfig(data_dir=str(tmp_path)),
        updates.append,
        stop_timeout=1,
    )
    worker = threading.Thread(target=job.run, daemon=True)
    worker.start()
    assert _wait_until(lambda: len(provider.streams) == 1)

    assert job.pause()
    assert _wait_until(lambda: provider.streams[0].closed)
    assert worker.is_alive()
    assert db.get_session(job.session_id)["status"] == SessionStatus.RUNNING
    time.sleep(0.08)
    paused_samples = job._durable_samples
    time.sleep(0.08)
    assert job._durable_samples == paused_samples
    assert not any(update.kind == "pipeline" for update in updates)

    assert job.resume()
    assert _wait_until(lambda: len(provider.streams) == 2)
    assert db.get_session(job.session_id)["status"] == SessionStatus.RUNNING
    assert provider.streams[0] is not provider.streams[1]

    job.stop()
    worker.join(timeout=4)
    assert not worker.is_alive()
    assert db.get_session(job.session_id)["status"] == SessionStatus.COMPLETED
    assert any(update.kind == "pipeline" for update in updates)
    db.close()


def test_gap_recovery_resumes_persisted_unit(tmp_path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    task_dir = tmp_path / "gap-task"
    task_dir.mkdir()
    gap = task_dir / "gap.wav"
    with wave.open(str(gap), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(b"\x00\x00" * SAMPLE_RATE)
    session = db.create_session(
        "realtime", "待恢复录音", task_dir, AppConfig(data_dir=str(tmp_path)).snapshot()
    )
    db.add_units(
        session,
        "gap",
        [AudioChunk(f"{session}-gap-0", gap, SAMPLE_RATE, 2 * SAMPLE_RATE)],
    )
    db.set_session_status(session, SessionStatus.INCOMPLETE)
    updates = []
    job = GapRecoveryJob(db, MockAsrProvider(), session, updates.append)
    job.run()
    assert db.get_session(session)["status"] == SessionStatus.COMPLETED
    assert "模拟转写" in db.transcript(session)
    assert any(update.kind == "completed" for update in updates)
    db.close()


def test_realtime_pipeline_calls_llm_twice_and_persists_each_stage(tmp_path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    config = AppConfig(
        data_dir=str(tmp_path),
        llm_model="qwen-plus",
        polish_prompt="按目标风格修复",
    )
    text_provider = SimulatedTextProvider()
    updates = []
    job = SimulatedRealtimeJob(
        db,
        MockAsrProvider(),
        config,
        updates.append,
        stop_timeout=1,
        text_provider=text_provider,
    )
    job.run()
    assert len(text_provider.calls) == 2
    assert text_provider.calls[1][1] == "清晰后的文本"
    assert db.transcript(job.session_id) == "定向修复后的文本"
    stages = db.text_stages(job.session_id)
    assert [row["stage"] for row in stages] == ["asr", "clarity", "polish"]
    assert all(row["status"] == "completed" for row in stages)
    assert (job.task_dir / "stages" / "01-realtime.txt").exists()
    assert (job.task_dir / "stages" / "02-clarity.txt").read_text(
        encoding="utf-8"
    ) == "清晰后的文本"
    assert (job.task_dir / "stages" / "03-polish.txt").read_text(
        encoding="utf-8"
    ) == "定向修复后的文本"
    assert any(update.kind == "pipeline" for update in updates)
    db.close()


def test_polish_timeout_keeps_clarity_result_and_completes_job(tmp_path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    provider = HangingPolishProvider()
    updates = []
    job = SimulatedRealtimeJob(
        db,
        MockAsrProvider(),
        AppConfig(data_dir=str(tmp_path), llm_model="qwen3.5-flash"),
        updates.append,
        stop_timeout=1,
        text_provider=provider,
        text_stage_timeout=0.05,
    )
    job.run()
    row = db.get_session(job.session_id)
    assert row["status"] == SessionStatus.COMPLETED
    assert "定向修复" in row["error"]
    assert db.transcript(job.session_id) == "清晰后的保底文本"
    stages = {stage["stage"]: stage for stage in db.text_stages(job.session_id)}
    assert stages["clarity"]["status"] == "completed"
    assert stages["polish"]["status"] == "failed"
    assert any(
        update.kind == "completed" and "部分文本处理未成功" in update.message
        for update in updates
    )
    provider.release.set()
    db.close()
