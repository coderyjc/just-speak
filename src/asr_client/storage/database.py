from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from asr_client.models import AudioChunk, SAMPLE_RATE, SessionStatus, TranscriptEvent, UnitStatus


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    task_dir TEXT NOT NULL,
    source_path TEXT,
    config_json TEXT NOT NULL,
    saved_samples INTEGER NOT NULL DEFAULT 0,
    confirmed_samples INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    edited_text TEXT
);
CREATE TABLE IF NOT EXISTS units (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    start_sample INTEGER NOT NULL,
    end_sample INTEGER NOT NULL,
    overlap_before_samples INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    request_id TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    UNIQUE(session_id, kind, ordinal)
);
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    source_key TEXT NOT NULL,
    start_sample INTEGER,
    end_sample INTEGER,
    text TEXT NOT NULL,
    is_final INTEGER NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    raw_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, source_key)
);
CREATE TABLE IF NOT EXISTS text_stages (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    label TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    raw_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(session_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_units_session ON units(session_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_segments_timeline ON segments(session_id, start_sample, id);
CREATE INDEX IF NOT EXISTS idx_text_stages_session ON text_stages(session_id, ordinal);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Thread-safe, single-entry SQLite repository."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._connection.commit()
        self.recover_interrupted()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def backup_to(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, sqlite3.connect(path) as target:
            self._connection.backup(target)

    def recover_interrupted(self) -> None:
        with self.transaction() as db:
            db.execute(
                """UPDATE sessions SET edited_text=COALESCE(
                    edited_text,
                    (SELECT text FROM text_stages
                     WHERE text_stages.session_id=sessions.id
                       AND text_stages.status='completed'
                     ORDER BY ordinal DESC LIMIT 1)
                ) WHERE status IN (?, ?, ?)""",
                (
                    SessionStatus.RUNNING,
                    SessionStatus.PREPARING,
                    SessionStatus.RECOVERING,
                ),
            )
            db.execute(
                """UPDATE text_stages SET status='failed',
                error='应用退出前文本处理未完成，已保留上一阶段结果',
                updated_at=? WHERE status='running'""",
                (utc_now(),),
            )
            db.execute(
                "UPDATE units SET status=? WHERE status=?",
                (UnitStatus.PENDING, UnitStatus.RUNNING),
            )
            db.execute(
                "UPDATE sessions SET status=?, error=? WHERE status IN (?, ?, ?)",
                (
                    SessionStatus.INCOMPLETE,
                    "应用上次退出时任务仍在运行，可点击继续",
                    SessionStatus.RUNNING,
                    SessionStatus.PREPARING,
                    SessionStatus.RECOVERING,
                ),
            )

    def create_session(
        self,
        kind: str,
        title: str,
        task_dir: Path,
        config: dict[str, Any],
        source_path: str = "",
    ) -> str:
        session_id = uuid.uuid4().hex
        now = utc_now()
        with self.transaction() as db:
            db.execute(
                """INSERT INTO sessions
                (id, kind, title, created_at, updated_at, status, task_dir,
                 source_path, config_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    kind,
                    title,
                    now,
                    now,
                    SessionStatus.CREATED,
                    str(task_dir),
                    source_path,
                    json.dumps(config, ensure_ascii=False),
                ),
            )
        return session_id

    def set_session_status(
        self, session_id: str, status: SessionStatus | str, error: str = ""
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE sessions SET status=?, error=?, updated_at=? WHERE id=?",
                (str(status), error, utc_now(), session_id),
            )

    def update_samples(
        self, session_id: str, saved: int, confirmed: int | None = None
    ) -> None:
        with self.transaction() as db:
            if confirmed is None:
                db.execute(
                    "UPDATE sessions SET saved_samples=?, updated_at=? WHERE id=?",
                    (saved, utc_now(), session_id),
                )
            else:
                db.execute(
                    """UPDATE sessions SET saved_samples=?, confirmed_samples=?,
                    updated_at=? WHERE id=?""",
                    (saved, confirmed, utc_now(), session_id),
                )

    def add_units(self, session_id: str, kind: str, chunks: list[AudioChunk]) -> None:
        with self.transaction() as db:
            for ordinal, chunk in enumerate(chunks):
                db.execute(
                    """INSERT OR IGNORE INTO units
                    (id, session_id, kind, ordinal, start_sample, end_sample,
                     overlap_before_samples, path, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk.stable_id,
                        session_id,
                        kind,
                        ordinal,
                        chunk.start_sample,
                        chunk.end_sample,
                        chunk.overlap_before_samples,
                        str(chunk.path),
                        UnitStatus.PENDING,
                    ),
                )

    def pending_units(self, session_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    """SELECT * FROM units WHERE session_id=? AND status != ?
                    ORDER BY ordinal""",
                    (session_id, UnitStatus.COMPLETED),
                ).fetchall()
            )

    def all_units(self, session_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    "SELECT * FROM units WHERE session_id=? ORDER BY ordinal",
                    (session_id,),
                ).fetchall()
            )

    def set_unit_status(
        self,
        unit_id: str,
        status: UnitStatus | str,
        error: str = "",
        request_id: str = "",
        increment_attempt: bool = False,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                """UPDATE units SET status=?, last_error=?, request_id=?,
                attempts=attempts+? WHERE id=?""",
                (str(status), error, request_id, int(increment_attempt), unit_id),
            )

    def upsert_segment(self, session_id: str, event: TranscriptEvent) -> None:
        raw_json = None
        if event.raw is not None:
            try:
                raw_json = json.dumps(event.raw, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                raw_json = str(event.raw)
        with self.transaction() as db:
            db.execute(
                """INSERT INTO segments
                (session_id, source_key, start_sample, end_sample, text, is_final,
                 request_id, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, source_key) DO UPDATE SET
                  start_sample=CASE WHEN segments.is_final=1 AND excluded.is_final=0
                    THEN segments.start_sample ELSE excluded.start_sample END,
                  end_sample=CASE WHEN segments.is_final=1 AND excluded.is_final=0
                    THEN segments.end_sample ELSE excluded.end_sample END,
                  text=CASE WHEN segments.is_final=1 AND excluded.is_final=0
                    THEN segments.text ELSE excluded.text END,
                  is_final=MAX(segments.is_final, excluded.is_final),
                  request_id=CASE WHEN segments.is_final=1 AND excluded.is_final=0
                    THEN segments.request_id ELSE excluded.request_id END,
                  raw_json=CASE WHEN segments.is_final=1 AND excluded.is_final=0
                    THEN segments.raw_json ELSE excluded.raw_json END""",
                (
                    session_id,
                    event.source_key,
                    event.start_sample,
                    event.end_sample,
                    event.text,
                    int(event.is_final),
                    event.request_id,
                    raw_json,
                    utc_now(),
                ),
            )

    def replace_range(self, session_id: str, start_sample: int, end_sample: int) -> None:
        with self.transaction() as db:
            db.execute(
                """DELETE FROM segments WHERE session_id=? AND is_final=1
                AND start_sample IS NOT NULL AND end_sample IS NOT NULL
                AND start_sample < ? AND end_sample > ?""",
                (session_id, end_sample, start_sample),
            )

    def transcript(self, session_id: str, include_pending: bool = True) -> str:
        with self._lock:
            session = self._connection.execute(
                "SELECT edited_text FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session and session["edited_text"] is not None:
                return session["edited_text"]
            rows = self._connection.execute(
                """SELECT text FROM segments WHERE session_id=? AND is_final=1
                ORDER BY COALESCE(start_sample, 9223372036854775807), id""",
                (session_id,),
            ).fetchall()
            parts = [row["text"].strip() for row in rows if row["text"].strip()]
            if include_pending:
                pending = self._connection.execute(
                    """SELECT start_sample, end_sample FROM units
                    WHERE session_id=? AND status != ? ORDER BY start_sample""",
                    (session_id, UnitStatus.COMPLETED),
                ).fetchall()
                parts.extend(
                    f"[{self.format_time(row['start_sample'])}–{self.format_time(row['end_sample'])} 待转写]"
                    for row in pending
                )
            return "\n".join(parts)

    def save_edited_text(self, session_id: str, text: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE sessions SET edited_text=?, updated_at=? WHERE id=?",
                (text, utc_now(), session_id),
            )

    def save_text_stage(
        self,
        session_id: str,
        stage: str,
        ordinal: int,
        label: str,
        text: str = "",
        status: str = "completed",
        model: str = "",
        prompt: str = "",
        request_id: str = "",
        error: str = "",
        raw: Any = None,
    ) -> None:
        raw_json = None
        if raw is not None:
            try:
                raw_json = json.dumps(raw, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                raw_json = str(raw)
        with self.transaction() as db:
            db.execute(
                """INSERT INTO text_stages
                (session_id, stage, ordinal, label, text, status, model, prompt,
                 request_id, error, raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, stage) DO UPDATE SET
                  ordinal=excluded.ordinal,
                  label=excluded.label,
                  text=excluded.text,
                  status=excluded.status,
                  model=excluded.model,
                  prompt=excluded.prompt,
                  request_id=excluded.request_id,
                  error=excluded.error,
                  raw_json=excluded.raw_json,
                  updated_at=excluded.updated_at""",
                (
                    session_id,
                    stage,
                    ordinal,
                    label,
                    text,
                    status,
                    model,
                    prompt,
                    request_id,
                    error,
                    raw_json,
                    utc_now(),
                ),
            )

    def text_stages(self, session_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    "SELECT * FROM text_stages WHERE session_id=? ORDER BY ordinal",
                    (session_id,),
                ).fetchall()
            )

    def list_sessions(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    "SELECT * FROM sessions ORDER BY created_at DESC"
                ).fetchall()
            )

    def get_session(self, session_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()

    def delete_session(self, session_id: str) -> bool:
        with self.transaction() as db:
            cursor = db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            return cursor.rowcount > 0

    def incomplete_sessions(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    """SELECT * FROM sessions WHERE status IN (?, ?, ?, ?)
                    ORDER BY created_at""",
                    (
                        SessionStatus.CREATED,
                        SessionStatus.PAUSED,
                        SessionStatus.INCOMPLETE,
                        SessionStatus.FAILED,
                    ),
                ).fetchall()
            )

    @staticmethod
    def format_time(samples: int) -> str:
        total = max(0, int(samples)) // SAMPLE_RATE
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
