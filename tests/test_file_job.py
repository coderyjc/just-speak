from __future__ import annotations

import wave

from asr_client.jobs.file_job import FileTranscriptionJob
from asr_client.models import AppConfig, AudioChunk, SAMPLE_RATE, SessionStatus
from asr_client.providers.base import AsrError, ErrorKind, MockAsrProvider
from asr_client.storage.database import Database


def make_wav(path, seconds: int = 1) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(b"\x00\x00" * SAMPLE_RATE * seconds)


def prepared_job(tmp_path, provider):
    db = Database(tmp_path / "db.sqlite3")
    config = AppConfig(data_dir=str(tmp_path))
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    wav = task_dir / "audio.wav"
    make_wav(wav)
    chunk_path = task_dir / "chunk.wav"
    make_wav(chunk_path)
    session = db.create_session(
        "file", "audio.wav", task_dir, config.snapshot(), str(wav)
    )
    db.add_units(
        session,
        "file_chunk",
        [AudioChunk("unit-1", chunk_path, 0, SAMPLE_RATE)],
    )
    updates = []
    job = FileTranscriptionJob(
        db,
        provider,
        config,
        wav,
        0,
        updates.append,
        session_id=session,
        sleep=lambda _: None,
    )
    return db, job, updates


def test_prepared_file_job_resumes_and_completes(tmp_path) -> None:
    db, job, updates = prepared_job(tmp_path, MockAsrProvider())
    job.run()
    assert db.get_session(job.session_id)["status"] == SessionStatus.COMPLETED
    assert "模拟转写" in db.transcript(job.session_id)
    assert (job.task_dir / "transcript.txt").exists()
    assert any(update.kind == "completed" for update in updates)
    db.close()


class FlakyProvider(MockAsrProvider):
    def __init__(self, kind: ErrorKind, failures: int) -> None:
        self.kind = kind
        self.failures = failures
        self.calls = 0

    def transcribe_file(self, path, source_prefix):
        self.calls += 1
        if self.calls <= self.failures:
            raise AsrError("injected", self.kind)
        return super().transcribe_file(path, source_prefix)


def test_temporary_failures_retry_at_most_three_times(tmp_path) -> None:
    provider = FlakyProvider(ErrorKind.TEMPORARY, 3)
    db, job, _ = prepared_job(tmp_path, provider)
    job.run()
    assert provider.calls == 4
    assert db.get_session(job.session_id)["status"] == SessionStatus.COMPLETED
    db.close()


def test_configuration_failure_does_not_retry(tmp_path) -> None:
    provider = FlakyProvider(ErrorKind.CONFIGURATION, 10)
    db, job, _ = prepared_job(tmp_path, provider)
    job.run()
    assert provider.calls == 1
    assert db.get_session(job.session_id)["status"] == SessionStatus.INCOMPLETE
    db.close()

