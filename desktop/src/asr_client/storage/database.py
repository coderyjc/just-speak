from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from asr_client.models import AudioChunk, SAMPLE_RATE, SessionStatus, TranscriptEvent, UnitStatus


DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


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
CREATE TABLE IF NOT EXISTS usage_ledger (
    session_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    char_count INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage_token_events (
    session_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    PRIMARY KEY(session_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_units_session ON units(session_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_segments_timeline ON segments(session_id, start_sample, id);
CREATE INDEX IF NOT EXISTS idx_text_stages_session ON text_stages(session_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_usage_ledger_occurred ON usage_ledger(occurred_at);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value: object) -> int | None:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _tokens_from_usage(usage: object) -> int | None:
    if not isinstance(usage, dict):
        return None
    for key in ("total_tokens", "total_token"):
        total = _number(usage.get(key))
        if total is not None:
            return total
    input_tokens = next(
        (
            value
            for key in ("input_tokens", "prompt_tokens")
            if (value := _number(usage.get(key))) is not None
        ),
        None,
    )
    output_tokens = next(
        (
            value
            for key in ("output_tokens", "completion_tokens")
            if (value := _number(usage.get(key))) is not None
        ),
        None,
    )
    if input_tokens is None and output_tokens is None:
        return None
    return (input_tokens or 0) + (output_tokens or 0)


def _extract_token_count(payload: object) -> int | None:
    if isinstance(payload, dict):
        direct = _tokens_from_usage(payload.get("usage"))
        if direct is None:
            direct = _tokens_from_usage(payload)
        if direct is not None:
            return direct
        for value in payload.values():
            nested = _extract_token_count(value)
            if nested is not None:
                return nested
    elif isinstance(payload, list):
        for value in payload:
            nested = _extract_token_count(value)
            if nested is not None:
                return nested
    return None


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
        self._migrate_usage_ledger()

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
            db.execute(
                """INSERT OR IGNORE INTO usage_ledger
                (session_id, kind, occurred_at) VALUES (?, ?, ?)""",
                (session_id, kind, now),
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
        self._set_usage_duration(session_id, max(0, saved) / SAMPLE_RATE)

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
        usage_session: str | None = None
        with self.transaction() as db:
            unit = db.execute(
                """SELECT units.session_id, sessions.kind FROM units
                JOIN sessions ON sessions.id=units.session_id WHERE units.id=?""",
                (unit_id,),
            ).fetchone()
            db.execute(
                """UPDATE units SET status=?, last_error=?, request_id=?,
                attempts=attempts+? WHERE id=?""",
                (str(status), error, request_id, int(increment_attempt), unit_id),
            )
            if (
                unit is not None
                and str(unit["kind"]) == "file"
                and str(status) == str(UnitStatus.COMPLETED)
            ):
                usage_session = str(unit["session_id"])
        if usage_session:
            self._sync_file_usage_duration(usage_session)

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
        if event.is_final:
            self._sync_usage_char_count(session_id)
        if event.raw is not None:
            self.record_token_usage(
                session_id,
                event.raw,
                f"asr:{event.request_id}" if event.request_id else "asr",
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

    def save_edited_text(
        self, session_id: str, text: str, track_usage: bool = False
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE sessions SET edited_text=?, updated_at=? WHERE id=?",
                (text, utc_now(), session_id),
            )
        if track_usage:
            self._set_usage_char_count(session_id, text)

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
        if raw is not None:
            raw_key = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
            digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:20]
            event_key = f"llm:{stage}:{request_id or digest}"
            self.record_token_usage(session_id, raw, event_key)

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
                    """SELECT sessions.*,
                              COALESCE(usage_ledger.char_count, 0) AS char_count
                       FROM sessions
                       LEFT JOIN usage_ledger ON usage_ledger.session_id=sessions.id
                       ORDER BY sessions.created_at DESC, sessions.id DESC"""
                ).fetchall()
            )

    def prune_sessions(
        self, max_count: int, protected_ids: set[str] | None = None
    ) -> list[sqlite3.Row]:
        """Remove the oldest history rows while retaining cumulative usage data."""

        limit = max(0, int(max_count))
        protected = {str(value) for value in (protected_ids or set()) if value}
        with self.transaction() as db:
            rows = list(
                db.execute(
                    "SELECT * FROM sessions ORDER BY created_at DESC, id DESC"
                ).fetchall()
            )
            remove_count = max(0, len(rows) - limit)
            victims = [
                row
                for row in reversed(rows)
                if str(row["id"]) not in protected
            ][:remove_count]
            db.executemany(
                "DELETE FROM sessions WHERE id=?",
                ((str(row["id"]),) for row in victims),
            )
        return victims

    def _ensure_usage_entry(self, session_id: str) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT kind, created_at FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is not None:
                db.execute(
                    """INSERT OR IGNORE INTO usage_ledger
                    (session_id, kind, occurred_at) VALUES (?, ?, ?)""",
                    (session_id, row["kind"], row["created_at"]),
                )

    def _set_usage_duration(self, session_id: str, seconds: float) -> None:
        self._ensure_usage_entry(session_id)
        with self.transaction() as db:
            db.execute(
                "UPDATE usage_ledger SET duration_seconds=? WHERE session_id=?",
                (max(0.0, float(seconds)), session_id),
            )

    def _set_usage_char_count(self, session_id: str, text: str) -> None:
        self._ensure_usage_entry(session_id)
        count = sum(1 for character in text if not character.isspace())
        with self.transaction() as db:
            db.execute(
                "UPDATE usage_ledger SET char_count=? WHERE session_id=?",
                (count, session_id),
            )

    def _sync_usage_char_count(self, session_id: str) -> None:
        self._set_usage_char_count(
            session_id, self.transcript(session_id, include_pending=False)
        )

    def _sync_file_usage_duration(self, session_id: str) -> None:
        with self._lock:
            row = self._connection.execute(
                """SELECT COALESCE(SUM(MAX(
                    end_sample - start_sample - overlap_before_samples, 0
                )), 0) AS samples
                FROM units WHERE session_id=? AND status=?""",
                (session_id, UnitStatus.COMPLETED),
            ).fetchone()
            samples = int(row["samples"] or 0) if row is not None else 0
            if samples == 0:
                fallback = self._connection.execute(
                    """SELECT COALESCE(MAX(end_sample), 0) AS samples
                    FROM segments WHERE session_id=? AND is_final=1""",
                    (session_id,),
                ).fetchone()
                samples = int(fallback["samples"] or 0) if fallback else 0
        self._set_usage_duration(session_id, samples / SAMPLE_RATE)

    def record_token_usage(
        self, session_id: str, payload: object, event_key: str = ""
    ) -> bool:
        token_count = _extract_token_count(payload)
        if token_count is None:
            return False
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]
        key = event_key.strip()
        if not key or key in {"asr", "llm"}:
            key = f"{key or 'usage'}:{digest}"
        self._ensure_usage_entry(session_id)
        with self.transaction() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO usage_token_events
                (session_id, event_key, token_count) VALUES (?, ?, ?)""",
                (session_id, key, token_count),
            )
            return cursor.rowcount > 0

    def _migrate_usage_ledger(self) -> None:
        """Snapshot legacy histories once; later deletions retain these entries."""

        with self._lock:
            legacy = list(
                self._connection.execute(
                    """SELECT sessions.* FROM sessions
                    LEFT JOIN usage_ledger ON usage_ledger.session_id=sessions.id
                    WHERE usage_ledger.session_id IS NULL"""
                ).fetchall()
            )
        for row in legacy:
            session_id = str(row["id"])
            self._ensure_usage_entry(session_id)
            self._sync_usage_char_count(session_id)
            if str(row["kind"]) == "realtime":
                samples = max(
                    int(row["saved_samples"] or 0),
                    int(row["confirmed_samples"] or 0),
                )
                self._set_usage_duration(session_id, samples / SAMPLE_RATE)
            elif str(row["kind"]) == "file":
                self._sync_file_usage_duration(session_id)

        with self._lock:
            segment_usage = list(
                self._connection.execute(
                    """SELECT session_id, request_id, raw_json FROM segments
                    WHERE raw_json IS NOT NULL AND raw_json != ''"""
                ).fetchall()
            )
            stage_usage = list(
                self._connection.execute(
                    """SELECT session_id, stage, request_id, raw_json FROM text_stages
                    WHERE raw_json IS NOT NULL AND raw_json != ''"""
                ).fetchall()
            )
        for row in segment_usage:
            try:
                payload = json.loads(str(row["raw_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            request_id = str(row["request_id"] or "")
            self.record_token_usage(
                str(row["session_id"]),
                payload,
                f"asr:{request_id}" if request_id else "asr",
            )
        for row in stage_usage:
            try:
                payload = json.loads(str(row["raw_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            request_id = str(row["request_id"] or "")
            if request_id:
                event_key = f"llm:{row['stage']}:{request_id}"
            else:
                serialized = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, default=str
                )
                digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]
                event_key = f"llm:{row['stage']}:{digest}"
            self.record_token_usage(str(row["session_id"]), payload, event_key)

    def usage_statistics(self, now: datetime | None = None) -> dict[str, Any]:
        """Read the append-safe usage ledger, independent of history deletion."""

        reference = now or datetime.now(DISPLAY_TIMEZONE)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=DISPLAY_TIMEZONE)
        today = reference.astimezone(DISPLAY_TIMEZONE).date()
        recent_start = today - timedelta(days=6)
        with self._lock:
            rows = list(
                self._connection.execute(
                    "SELECT * FROM usage_ledger ORDER BY occurred_at"
                ).fetchall()
            )
            token_row = self._connection.execute(
                """SELECT COUNT(*) AS events,
                COALESCE(SUM(token_count), 0) AS tokens FROM usage_token_events"""
            ).fetchone()

        daily_chars: dict[str, int] = {}
        active_dates: set[str] = set()
        realtime_chars = 0
        realtime_seconds = 0.0
        realtime_count = 0
        realtime_last_7_days = 0
        file_chars = 0
        file_seconds = 0.0
        file_count = 0
        longest_chars = 0

        for row in rows:
            try:
                created = datetime.fromisoformat(str(row["occurred_at"]))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                local_date = created.astimezone(DISPLAY_TIMEZONE).date()
            except (TypeError, ValueError):
                continue
            date_key = local_date.isoformat()
            active_dates.add(date_key)
            chars = int(row["char_count"] or 0)
            if chars:
                daily_chars[date_key] = daily_chars.get(date_key, 0) + chars
                longest_chars = max(longest_chars, chars)

            kind = str(row["kind"])
            seconds = max(0.0, float(row["duration_seconds"] or 0))
            if kind == "realtime":
                realtime_seconds += seconds
                if chars:
                    realtime_chars += chars
                    realtime_count += 1
                    if recent_start <= local_date <= today:
                        realtime_last_7_days += 1
            elif kind == "file":
                file_seconds += seconds
                if chars:
                    file_chars += chars
                    file_count += 1

        if daily_chars:
            peak_date, peak_chars = max(
                daily_chars.items(), key=lambda item: (item[1], item[0])
            )
        else:
            peak_date, peak_chars = "", 0
        total_seconds = realtime_seconds + file_seconds
        token_count = (
            int(token_row["tokens"] or 0)
            if token_row is not None and int(token_row["events"] or 0) > 0
            else None
        )
        return {
            "total_chars": realtime_chars + file_chars,
            "realtime_chars": realtime_chars,
            "file_chars": file_chars,
            "realtime_seconds": realtime_seconds,
            "file_seconds": file_seconds,
            "total_seconds": total_seconds,
            "cost_yuan": round(total_seconds * 0.00033, 6),
            "token_count": token_count,
            "realtime_count": realtime_count,
            "file_count": file_count,
            "realtime_average_chars": (
                int(realtime_chars / realtime_count + 0.5) if realtime_count else 0
            ),
            "realtime_last_7_days": realtime_last_7_days,
            "longest_chars": longest_chars,
            "peak_date": peak_date,
            "peak_chars": peak_chars,
            "active_days": len(active_dates),
            "daily_chars": daily_chars,
            "as_of_date": today.isoformat(),
        }

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
