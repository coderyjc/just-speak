from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from asr_client.audio.chunker import write_chunks
from asr_client.audio.ffmpeg import convert_to_standard_wav, probe_audio
from asr_client.jobs.common import atomic_write_text
from asr_client.models import (
    AppConfig,
    JobUpdate,
    SAMPLE_RATE,
    SessionStatus,
    TranscriptEvent,
    UnitStatus,
)
from asr_client.providers.base import AsrError, AsrProvider
from asr_client.storage.database import Database


UpdateSink = Callable[[JobUpdate], None]


class FileTranscriptionJob:
    def __init__(
        self,
        database: Database,
        provider: AsrProvider,
        config: AppConfig,
        source: Path,
        track_index: int,
        update: UpdateSink | None = None,
        session_id: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.database = database
        self.provider = provider
        self.config = AppConfig(**config.snapshot())
        self.source = source
        self.track_index = track_index
        self.update = update or (lambda _: None)
        self.sleep = sleep
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._pause.set()
        data_root = Path(self.config.data_dir)
        if session_id:
            self.session_id = session_id
            row = database.get_session(session_id)
            if row is None:
                raise ValueError("待继续的任务不存在")
            self.task_dir = Path(row["task_dir"])
        else:
            provisional = data_root / "tasks" / "pending"
            config_snapshot = self.config.snapshot()
            config_snapshot["track_index"] = track_index
            self.session_id = database.create_session(
                "file",
                source.name,
                provisional,
                config_snapshot,
                str(source),
            )
            self.task_dir = data_root / "tasks" / self.session_id
            self.task_dir.mkdir(parents=True, exist_ok=True)
            with database.transaction() as db:
                db.execute(
                    "UPDATE sessions SET task_dir=? WHERE id=?",
                    (str(self.task_dir), self.session_id),
                )

    @property
    def standard_wav(self) -> Path:
        return self.task_dir / "audio.wav"

    def pause(self) -> None:
        self._pause.clear()
        self.update(JobUpdate("status", "将在当前片段完成后暂停"))

    def resume(self) -> None:
        self._pause.set()
        self.update(JobUpdate("status", "继续处理"))

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.set()
        self.update(JobUpdate("status", "将在当前片段完成后取消"))

    def run(self) -> None:
        try:
            self._prepare()
            self.database.set_session_status(self.session_id, SessionStatus.RUNNING)
            units = self.database.all_units(self.session_id)
            total = len(units)
            completed = sum(row["status"] == UnitStatus.COMPLETED for row in units)
            for row in units:
                if row["status"] == UnitStatus.COMPLETED:
                    continue
                while not self._pause.wait(0.2):
                    if self._cancel.is_set():
                        break
                    self.database.set_session_status(self.session_id, SessionStatus.PAUSED)
                if self._cancel.is_set():
                    self.database.set_session_status(self.session_id, SessionStatus.CANCELLED)
                    self._publish_transcript()
                    self.update(JobUpdate("cancelled", "任务已取消，已完成结果仍然保留"))
                    return
                self.database.set_session_status(self.session_id, SessionStatus.RUNNING)
                self._process_unit(row)
                completed += 1
                self.update(
                    JobUpdate(
                        "progress",
                        f"已完成 {completed}/{total} 个片段",
                        int(completed * 100 / max(1, total)),
                    )
                )
                self._publish_transcript()
            self.database.set_session_status(self.session_id, SessionStatus.COMPLETED)
            self._publish_transcript()
            self.update(JobUpdate("completed", "文件转写完成", 100, self.session_id))
        except Exception as exc:
            self.database.set_session_status(
                self.session_id, SessionStatus.INCOMPLETE, str(exc)
            )
            self._publish_transcript()
            self.update(JobUpdate("error", str(exc), payload=exc))

    def _prepare(self) -> None:
        existing = self.database.all_units(self.session_id)
        if existing and self.standard_wav.exists():
            self.update(JobUpdate("status", "已读取断点，继续未完成片段"))
            return
        self.database.set_session_status(self.session_id, SessionStatus.PREPARING)
        self.update(JobUpdate("status", "正在检查媒体文件"))
        info = probe_audio(self.source, self.config.ffmpeg_path)
        if not any(track.index == self.track_index for track in info.tracks):
            raise ValueError("选择的音轨不存在")
        self.update(JobUpdate("status", "正在提取并转换音轨"))
        convert_to_standard_wav(
            self.source, self.standard_wav, self.track_index, self.config.ffmpeg_path
        )
        self.update(JobUpdate("status", "正在生成分段清单"))
        chunks = write_chunks(
            self.standard_wav,
            self.task_dir / "chunks",
            target_seconds=self.config.chunk_seconds,
        )
        self.database.add_units(self.session_id, "file_chunk", chunks)
        manifest = [
            {
                "id": chunk.stable_id,
                "path": str(chunk.path),
                "start_sample": chunk.start_sample,
                "end_sample": chunk.end_sample,
                "overlap_before_samples": chunk.overlap_before_samples,
            }
            for chunk in chunks
        ]
        atomic_write_text(
            self.task_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    def _process_unit(self, row: object) -> None:
        unit_id = row["id"]  # type: ignore[index]
        path = Path(row["path"])  # type: ignore[index]
        start = int(row["start_sample"])  # type: ignore[index]
        last_error: Exception | None = None
        for attempt in range(1, 5):
            self.database.set_unit_status(
                unit_id, UnitStatus.RUNNING, increment_attempt=True
            )
            self.update(JobUpdate("status", f"正在识别片段 {row['ordinal'] + 1}，第 {attempt} 次尝试"))  # type: ignore[index]
            try:
                events = self.provider.transcribe_file(path, f"unit-{unit_id}")
                self._store_unit_events(unit_id, start, events)
                request_id = events[-1].request_id if events else ""
                self.database.set_unit_status(
                    unit_id, UnitStatus.COMPLETED, request_id=request_id
                )
                atomic_write_text(
                    self.task_dir / "responses" / f"{unit_id}.json",
                    json.dumps(
                        [event.raw for event in events],
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                )
                return
            except AsrError as exc:
                last_error = exc
                self.database.set_unit_status(
                    unit_id,
                    UnitStatus.FAILED,
                    str(exc),
                    request_id=exc.request_id,
                )
                if not exc.retryable or attempt >= 4:
                    raise
                delay = exc.retry_delay(attempt)
                self.update(JobUpdate("status", f"临时错误，{delay:g} 秒后重试"))
                self.sleep(delay)
            except Exception as exc:
                last_error = exc
                self.database.set_unit_status(unit_id, UnitStatus.FAILED, str(exc))
                raise
        if last_error:
            raise last_error

    def _store_unit_events(
        self, unit_id: str, offset: int, events: list[TranscriptEvent]
    ) -> None:
        for index, event in enumerate(events):
            event.start_sample = offset + (event.start_sample or 0)
            event.end_sample = offset + event.end_sample if event.end_sample is not None else None
            event.source_key = f"{unit_id}:{event.source_key}:{index}"
            if self._is_exact_overlap_duplicate(event):
                continue
            self.database.upsert_segment(self.session_id, event)

    def _is_exact_overlap_duplicate(self, event: TranscriptEvent) -> bool:
        if event.start_sample is None or event.end_sample is None:
            return False
        with self.database._lock:
            match = self.database._connection.execute(
                """SELECT 1 FROM segments WHERE session_id=? AND is_final=1
                AND text=? AND start_sample < ? AND end_sample > ? LIMIT 1""",
                (self.session_id, event.text, event.end_sample, event.start_sample),
            ).fetchone()
        return match is not None

    def _publish_transcript(self) -> None:
        text = self.database.transcript(self.session_id)
        atomic_write_text(self.task_dir / "transcript.txt", text)
        self.update(JobUpdate("transcript", payload=text))


def extract_audio_only(
    source: Path,
    destination: Path,
    track_index: int,
    ffmpeg_path: str = "",
) -> Path:
    probe_audio(source, ffmpeg_path)
    return convert_to_standard_wav(source, destination, track_index, ffmpeg_path)
