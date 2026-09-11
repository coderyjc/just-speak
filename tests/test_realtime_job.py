from __future__ import annotations

import wave

from asr_client.jobs.realtime_job import GapRecoveryJob, RealtimeJob
from asr_client.models import AppConfig, AudioChunk, SAMPLE_RATE, SessionStatus
from asr_client.providers.base import MockAsrProvider
from asr_client.storage.database import Database


class SimulatedRealtimeJob(RealtimeJob):
    def _capture(self) -> None:
        self._input_rate = SAMPLE_RATE
        packet = b"\x10\x00" * (SAMPLE_RATE // 10)
        for _ in range(31):
            self._audio_queue.put(packet)


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
