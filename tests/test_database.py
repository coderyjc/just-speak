from __future__ import annotations

from asr_client.models import AudioChunk, SessionStatus, TranscriptEvent, UnitStatus
from asr_client.storage.database import Database


def test_segments_are_idempotent_and_temporary_can_be_finalized(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    task_dir = tmp_path / "task"
    session = db.create_session("file", "sample", task_dir, {})
    event = TranscriptEvent("临时", 0, None, False, "sentence-1")
    db.upsert_segment(session, event)
    db.upsert_segment(
        session, TranscriptEvent("最终", 0, 16000, True, "sentence-1")
    )
    db.upsert_segment(
        session, TranscriptEvent("重复回调", 0, 16000, False, "sentence-1")
    )
    # A late temporary callback cannot revert finality, while the stable identity keeps one row.
    with db._lock:
        rows = db._connection.execute(
            "SELECT text, is_final FROM segments WHERE session_id=?", (session,)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["is_final"] == 1
    assert rows[0]["text"] == "最终"
    db.close()


def test_pending_placeholder_and_restart_recovery(tmp_path) -> None:
    path = tmp_path / "test.sqlite3"
    db = Database(path)
    session = db.create_session("file", "sample", tmp_path / "task", {})
    chunk = AudioChunk("u1", tmp_path / "one.wav", 0, 32_000)
    db.add_units(session, "file_chunk", [chunk])
    db.set_unit_status("u1", UnitStatus.RUNNING)
    db.set_session_status(session, SessionStatus.RUNNING)
    db.close()

    reopened = Database(path)
    row = reopened.get_session(session)
    assert row["status"] == SessionStatus.INCOMPLETE
    assert reopened.all_units(session)[0]["status"] == UnitStatus.PENDING
    assert "00:00:00–00:00:02 待转写" in reopened.transcript(session)
    reopened.close()


def test_database_backup_preserves_history(tmp_path) -> None:
    source = Database(tmp_path / "source.sqlite3")
    session = source.create_session("file", "sample", tmp_path / "task", {})
    destination_path = tmp_path / "moved" / "justspeak.sqlite3"
    source.backup_to(destination_path)
    destination = Database(destination_path)
    assert destination.get_session(session)["title"] == "sample"
    destination.close()
    source.close()
