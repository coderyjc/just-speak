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


def test_text_stages_are_updated_and_removed_with_session(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    session = db.create_session("realtime", "sample", tmp_path / "task", {})
    db.save_text_stage(session, "asr", 0, "实时录音", "原始文字")
    db.save_text_stage(session, "clarity", 1, "文本清晰", "清晰文字")
    db.save_text_stage(
        session, "clarity", 1, "文本清晰", "更新文字", request_id="llm-1"
    )
    stages = db.text_stages(session)
    assert [row["stage"] for row in stages] == ["asr", "clarity"]
    assert stages[1]["text"] == "更新文字"
    assert stages[1]["request_id"] == "llm-1"
    assert db.delete_session(session)
    assert db.text_stages(session) == []
    db.close()


def test_recovery_fails_running_text_stage_and_keeps_latest_completed_text(
    tmp_path,
) -> None:
    path = tmp_path / "test.sqlite3"
    db = Database(path)
    session = db.create_session("realtime", "录音", tmp_path / "task", {})
    db.save_text_stage(session, "asr", 0, "实时录音", "原始文字")
    db.save_text_stage(session, "clarity", 1, "文本清晰", "清晰文字")
    db.save_text_stage(
        session, "polish", 2, "定向修复", "清晰文字", status="running"
    )
    db.set_session_status(session, SessionStatus.RUNNING)
    db.close()

    recovered = Database(path)
    assert recovered.get_session(session)["status"] == SessionStatus.INCOMPLETE
    assert recovered.transcript(session) == "清晰文字"
    stages = {row["stage"]: row for row in recovered.text_stages(session)}
    assert stages["polish"]["status"] == "failed"
    assert "上一阶段结果" in stages["polish"]["error"]
    recovered.close()
