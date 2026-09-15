from __future__ import annotations

import threading
import re
import wave
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol

from asr_client.models import SAMPLE_RATE, TextModelResult, TranscriptEvent


class ErrorKind(StrEnum):
    TEMPORARY = "temporary"
    AUTHENTICATION = "authentication"
    QUOTA = "quota"
    CONFIGURATION = "configuration"
    LOCAL = "local"


class AsrError(RuntimeError):
    def __init__(
        self, message: str, kind: ErrorKind = ErrorKind.TEMPORARY, request_id: str = ""
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        return self.kind == ErrorKind.TEMPORARY

    def retry_delay(self, attempt: int) -> float:
        match = re.search(
            r"retry[- ]?after[^0-9]*(\d+(?:\.\d+)?)", str(self), re.IGNORECASE
        )
        if match:
            return max(0.1, min(60.0, float(match.group(1))))
        return float(2 ** max(0, attempt - 1))


EventSink = Callable[[TranscriptEvent], None]


class StreamingSession(Protocol):
    request_id: str

    def send(self, pcm: bytes) -> None: ...

    def stop(self) -> None: ...

    def abort(self) -> None: ...


class AsrProvider(Protocol):
    def transcribe_file(self, path: Path, source_prefix: str) -> list[TranscriptEvent]: ...

    def start_stream(self, sink: EventSink) -> StreamingSession: ...


class TextProvider(Protocol):
    def complete(self, prompt_template: str, text: str) -> TextModelResult: ...


class _MockStream:
    def __init__(self, sink: EventSink) -> None:
        self.sink = sink
        self.request_id = "mock-stream"
        self.samples = 0
        self.emitted_seconds = 0
        self.closed = False
        self.lock = threading.Lock()

    def send(self, pcm: bytes) -> None:
        with self.lock:
            if self.closed:
                raise AsrError("模拟流已经关闭", ErrorKind.LOCAL)
            self.samples += len(pcm) // 2
            elapsed = self.samples // SAMPLE_RATE
            if elapsed > self.emitted_seconds:
                self.emitted_seconds = elapsed
                start = max(0, (elapsed - 1) * SAMPLE_RATE)
                self.sink(
                    TranscriptEvent(
                        text=f"模拟实时识别 {elapsed} 秒",
                        start_sample=start,
                        end_sample=self.samples if elapsed % 3 == 0 else None,
                        is_final=elapsed % 3 == 0,
                        source_key=f"mock-live-{(elapsed - 1) // 3}",
                        request_id=self.request_id,
                    )
                )

    def stop(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.samples:
                group = max(0, (max(1, self.emitted_seconds) - 1) // 3)
                self.sink(
                    TranscriptEvent(
                        text=f"模拟实时识别，共 {self.samples / SAMPLE_RATE:.1f} 秒",
                        start_sample=group * 3 * SAMPLE_RATE,
                        end_sample=self.samples,
                        is_final=True,
                        source_key=f"mock-live-{group}",
                        request_id=self.request_id,
                    )
                )

    def abort(self) -> None:
        self.closed = True


class MockAsrProvider:
    """Offline deterministic backend used when an API key is unavailable."""

    is_mock = True

    def transcribe_file(self, path: Path, source_prefix: str) -> list[TranscriptEvent]:
        with wave.open(str(path), "rb") as audio:
            samples = audio.getnframes()
        return [
            TranscriptEvent(
                text=f"[模拟转写：音频 {samples / SAMPLE_RATE:.1f} 秒]",
                start_sample=0,
                end_sample=samples,
                is_final=True,
                source_key=f"{source_prefix}:0",
                request_id="mock-file",
                raw={"mode": "mock", "samples": samples},
            )
        ]

    def start_stream(self, sink: EventSink) -> StreamingSession:
        return _MockStream(sink)
