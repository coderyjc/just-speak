from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def test_prune_sessions_keeps_limit_and_cumulative_statistics(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    base_time = datetime(2026, 9, 1, tzinfo=timezone.utc)
    session_ids = []
    for index in range(12):
        session_id = db.create_session(
            "realtime", f"record-{index}", tmp_path / "tasks" / f"record-{index}", {}
        )
        session_ids.append(session_id)
        db.save_edited_text(session_id, f"第{index}条", track_usage=True)
        occurred_at = (base_time + timedelta(days=index)).isoformat(timespec="seconds")
        with db.transaction() as connection:
            connection.execute(
                "UPDATE sessions SET created_at=? WHERE id=?",
                (occurred_at, session_id),
            )
            connection.execute(
                "UPDATE usage_ledger SET occurred_at=? WHERE session_id=?",
                (occurred_at, session_id),
            )

    stats_before = db.usage_statistics(
        datetime(2026, 9, 20, tzinfo=timezone.utc)
    )
    victims = db.prune_sessions(10, {session_ids[0]})

    assert len(victims) == 2
    assert db.get_session(session_ids[0]) is not None
    assert len(db.list_sessions()) == 10
    assert db.usage_statistics(
        datetime(2026, 9, 20, tzinfo=timezone.utc)
    ) == stats_before
    db.close()


def test_usage_statistics_uses_final_text_and_non_overlapping_duration(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    cst = timezone(timedelta(hours=8))
    now = datetime(2026, 9, 11, 12, 0, tzinfo=cst)
    older = now - timedelta(days=8)

    current_realtime = db.create_session(
        "realtime", "current", tmp_path / "current", {}
    )
    db.upsert_segment(
        current_realtime,
        TranscriptEvent("更长的原始识别文本", 0, 120 * 16_000, True, "current"),
    )
    db.save_edited_text(current_realtime, "修复文本", track_usage=True)
    db.update_samples(current_realtime, 120 * 16_000, 120 * 16_000)

    older_realtime = db.create_session(
        "realtime", "older", tmp_path / "older", {}
    )
    db.save_edited_text(older_realtime, "旧记录", track_usage=True)
    db.update_samples(older_realtime, 60 * 16_000, 60 * 16_000)

    file_session = db.create_session("file", "file", tmp_path / "file", {})
    db.save_edited_text(file_session, "文件 转写内容", track_usage=True)
    db.add_units(
        file_session,
        "file_chunk",
        [
            AudioChunk("chunk-1", tmp_path / "one.wav", 0, 180 * 16_000),
            AudioChunk(
                "chunk-2",
                tmp_path / "two.wav",
                170 * 16_000,
                300 * 16_000,
                overlap_before_samples=10 * 16_000,
            ),
        ],
    )
    db.set_unit_status("chunk-1", UnitStatus.COMPLETED)
    db.set_unit_status("chunk-2", UnitStatus.COMPLETED)
    with db.transaction() as connection:
        connection.execute(
            "UPDATE sessions SET created_at=? WHERE id IN (?, ?)",
            (
                now.astimezone(timezone.utc).isoformat(timespec="seconds"),
                current_realtime,
                file_session,
            ),
        )
        connection.execute(
            "UPDATE usage_ledger SET occurred_at=? WHERE session_id IN (?, ?)",
            (
                now.astimezone(timezone.utc).isoformat(timespec="seconds"),
                current_realtime,
                file_session,
            ),
        )
        connection.execute(
            "UPDATE sessions SET created_at=? WHERE id=?",
            (
                older.astimezone(timezone.utc).isoformat(timespec="seconds"),
                older_realtime,
            ),
        )
        connection.execute(
            "UPDATE usage_ledger SET occurred_at=? WHERE session_id=?",
            (
                older.astimezone(timezone.utc).isoformat(timespec="seconds"),
                older_realtime,
            ),
        )

    stats = db.usage_statistics(now)

    assert stats["total_chars"] == 13
    assert stats["realtime_chars"] == 7
    assert stats["file_chars"] == 6
    assert stats["realtime_seconds"] == 180
    assert stats["file_seconds"] == 300
    assert stats["total_seconds"] == 480
    assert stats["cost_yuan"] == 0.1584
    assert stats["token_count"] is None
    assert stats["longest_chars"] == 6
    assert stats["peak_date"] == "2026-09-11"
    assert stats["peak_chars"] == 10
    assert stats["realtime_count"] == 2
    assert stats["realtime_average_chars"] == 4
    assert stats["realtime_last_7_days"] == 1
    assert stats["active_days"] == 2
    assert stats["daily_chars"] == {"2026-09-03": 3, "2026-09-11": 10}
    db.save_edited_text(current_realtime, "手动修改后的历史稿")
    assert db.usage_statistics(now) == stats
    assert db.delete_session(current_realtime)
    assert db.delete_session(older_realtime)
    assert db.delete_session(file_session)
    assert db.list_sessions() == []
    assert db.usage_statistics(now) == stats
    db.close()


def test_usage_token_events_are_optional_idempotent_and_delete_safe(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    session = db.create_session("realtime", "tokens", tmp_path / "tokens", {})

    assert not db.record_token_usage(session, {"output": {}}, "asr:no-usage")
    assert db.usage_statistics()["token_count"] is None
    assert db.record_token_usage(
        session, {"usage": {"total_tokens": 120}}, "asr:request-1"
    )
    assert not db.record_token_usage(
        session, {"usage": {"total_tokens": 120}}, "asr:request-1"
    )
    assert db.record_token_usage(
        session,
        {"output": {"usage": {"input_tokens": 20, "output_tokens": 5}}},
        "llm:request-2",
    )
    assert db.record_token_usage(
        session, {"prompt_tokens": 3, "completion_tokens": 2}, "llm:request-3"
    )
    assert db.usage_statistics()["token_count"] == 150

    assert db.delete_session(session)
    assert db.usage_statistics()["token_count"] == 150
    db.close()


def test_legacy_history_is_snapshotted_into_usage_ledger_once(tmp_path) -> None:
    path = tmp_path / "test.sqlite3"
    db = Database(path)
    session = db.create_session("realtime", "legacy", tmp_path / "legacy", {})
    db.save_edited_text(session, "旧有最终稿")
    db.update_samples(session, 90 * 16_000, 90 * 16_000)
    with db.transaction() as connection:
        connection.execute(
            "DELETE FROM usage_ledger WHERE session_id=?", (session,)
        )
    db.close()

    reopened = Database(path)
    stats = reopened.usage_statistics()
    assert stats["realtime_chars"] == 5
    assert stats["realtime_seconds"] == 90
    reopened.close()


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
