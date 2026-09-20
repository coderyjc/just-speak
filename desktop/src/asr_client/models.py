from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
MAX_TRANSCRIPTION_PROMPT_LENGTH = 400
MIN_OCR_CONTEXT_LENGTH = 50
MAX_OCR_CONTEXT_LENGTH = 400

DEFAULT_OCR_PROMPT = (
    "请只输出图像中可见的纯文本，按自然阅读顺序保留必要换行。"
    "不要输出 HTML、XML、Markdown、代码块、JSON、坐标、样式或任何标签。"
)

DEFAULT_POLISH_PROMPT = """请在保留原意、事实、语气和专有名词的前提下，对文本进行定向修复：
1. 调整表达顺序，使论述连贯、重点清楚；
2. 修复明显的语病、指代不清和断裂句；
3. 保留有意义的口语风格，不扩写原文中没有的信息；
4. 只输出修复后的完整文本，不附加解释。"""


class SessionStatus(StrEnum):
    CREATED = "created"
    PREPARING = "preparing"
    RUNNING = "running"
    PAUSED = "paused"
    RECOVERING = "recovering"
    INCOMPLETE = "incomplete"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class UnitStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class AppConfig:
    region: str = "beijing"
    workspace_id: str = ""
    base_url: str = ""
    # Kept for loading configuration files created before Base URL supported HTTP.
    websocket_url: str = ""
    model: str = "qwen-audio-3.0-asr-flash-streaming"
    llm_model: str = ""
    transcription_prompt: str = ""
    information_enhancement_enabled: bool = False
    ocr_model: str = "qwen3.5-ocr"
    ocr_base_url: str = ""
    ocr_prompt: str = DEFAULT_OCR_PROMPT
    ocr_context_max_chars: int = 300
    polish_prompt: str = DEFAULT_POLISH_PROMPT
    language: str = "zh"
    microphone: int | None = None
    data_dir: str = ""
    ffmpeg_path: str = ""
    chunk_seconds: int = 10 * 60
    history_limit: int = 50
    realtime_toggle_shortcut: str = "Space"
    realtime_toggle_shortcut_enabled: bool = True
    realtime_stop_shortcut: str = "S"
    realtime_stop_shortcut_enabled: bool = True
    mini_mode_shortcut: str = "RightAlt"
    remember_key: bool = False

    def endpoint(self) -> str:
        if self.base_url.strip():
            return self.base_url.strip()
        if self.websocket_url.strip():
            return self.websocket_url.strip()
        uses_http = self.uses_http_api()
        if self.region == "singapore":
            if self.workspace_id.strip():
                host = f"{self.workspace_id.strip()}.ap-southeast-1.maas.aliyuncs.com"
            else:
                host = "dashscope-intl.aliyuncs.com"
        elif self.workspace_id.strip():
            host = f"{self.workspace_id.strip()}.cn-beijing.maas.aliyuncs.com"
        else:
            host = "dashscope.aliyuncs.com"
        if uses_http:
            return f"https://{host}/api/v1"
        return f"wss://{host}/api-ws/v1/inference"

    def uses_http_api(self) -> bool:
        model = self.model.strip().lower()
        if not model:
            return False
        if "filetrans" in model:
            return True
        if model.startswith("qwen3-asr-flash"):
            return "realtime" not in model
        if model.startswith("qwen-audio-3.0-asr-flash"):
            return "streaming" not in model
        if model.startswith("fun-asr"):
            return "realtime" not in model
        return False

    def supports_realtime(self) -> bool:
        return not self.uses_http_api()

    def ocr_endpoint(self) -> str:
        if self.ocr_base_url.strip():
            return self.ocr_base_url.strip()
        if self.region == "singapore":
            host = (
                f"{self.workspace_id.strip()}.ap-southeast-1.maas.aliyuncs.com"
                if self.workspace_id.strip()
                else "dashscope-intl.aliyuncs.com"
            )
        else:
            host = (
                f"{self.workspace_id.strip()}.cn-beijing.maas.aliyuncs.com"
                if self.workspace_id.strip()
                else "dashscope.aliyuncs.com"
            )
        return f"https://{host}/compatible-mode/v1"

    def language_hints(self) -> list[str] | None:
        return None if self.language == "auto" else [self.language]

    def snapshot(self) -> dict[str, Any]:
        data = asdict(self)
        data["base_url"] = self.endpoint()
        data["websocket_url"] = ""
        return data


@dataclass(slots=True)
class AudioTrack:
    index: int
    codec: str
    sample_rate: int | None
    channels: int | None
    language: str = ""

    @property
    def label(self) -> str:
        details = [f"音轨 {self.index}", self.codec]
        if self.sample_rate:
            details.append(f"{self.sample_rate} Hz")
        if self.channels:
            details.append(f"{self.channels} 声道")
        if self.language:
            details.append(self.language)
        return " · ".join(details)


@dataclass(slots=True)
class AudioInfo:
    path: Path
    duration_seconds: float
    tracks: list[AudioTrack]


@dataclass(slots=True)
class AudioChunk:
    stable_id: str
    path: Path
    start_sample: int
    end_sample: int
    overlap_before_samples: int = 0


@dataclass(slots=True)
class TranscriptEvent:
    text: str
    start_sample: int | None
    end_sample: int | None
    is_final: bool
    source_key: str
    request_id: str = ""
    raw: dict[str, Any] | list[Any] | str | None = None


@dataclass(slots=True)
class JobUpdate:
    kind: str
    message: str = ""
    progress: int | None = None
    payload: Any = None


@dataclass(slots=True)
class TextModelResult:
    text: str
    request_id: str = ""
    raw: dict[str, Any] | list[Any] | str | None = None
