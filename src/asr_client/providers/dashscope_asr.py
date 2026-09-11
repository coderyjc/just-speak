from __future__ import annotations

import base64
import json
import mimetypes
import threading
import urllib.error
import urllib.request
import wave
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from asr_client.models import (
    MAX_TRANSCRIPTION_PROMPT_LENGTH,
    AppConfig,
    SAMPLE_RATE,
    TranscriptEvent,
)
from asr_client.providers.base import AsrError, ErrorKind, EventSink, StreamingSession


_SDK_GLOBAL_LOCK = threading.Lock()


def _model_mode(model: str) -> str:
    lowered = model.strip().lower()
    # Some deployments expose this model through multimodal-generation with an
    # input_text context message followed by input_audio.
    if lowered.startswith("qwen3-asr-flash-filetrans"):
        return "flash-http"
    if "filetrans" in lowered or lowered in {"fun-asr", "fun-asr-mtl"}:
        return "public-url-only"
    if lowered.startswith("qwen3-asr-flash") and "realtime" in lowered:
        return "qwen3-realtime"
    if lowered.startswith("qwen3-asr-flash") and "realtime" not in lowered:
        return "qwen3-http"
    if lowered.startswith("qwen-audio-3.0-asr-flash") and "streaming" not in lowered:
        return "flash-http"
    if lowered.startswith("fun-asr-flash") and "realtime" not in lowered:
        return "flash-http"
    return "recognition"


def _with_scheme(value: str, scheme: str) -> str:
    value = value.strip()
    if "://" not in value:
        return f"{scheme}://{value}"
    return value


def _http_base_url(value: str) -> str:
    parsed = urlsplit(_with_scheme(value, "https"))
    scheme = "https" if parsed.scheme in {"wss", "ws"} else parsed.scheme
    path = parsed.path.rstrip("/")
    if parsed.hostname and parsed.hostname.endswith("aliyuncs.com"):
        path = "/api/v1"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _websocket_url(value: str) -> str:
    parsed = urlsplit(_with_scheme(value, "wss"))
    scheme = "wss" if parsed.scheme in {"https", "http"} else parsed.scheme
    path = parsed.path.rstrip("/")
    if not path or "/api/v1" in path or "/compatible-mode/" in path:
        path = "/api-ws/v1/inference"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    for method in ("to_dict", "as_dict"):
        converter = getattr(value, method, None)
        if callable(converter):
            try:
                return converter()
            except Exception:
                pass
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(value)


def _error_from(value: Any) -> AsrError:
    message = str(getattr(value, "message", None) or value or "云端识别失败")
    request_id = str(getattr(value, "request_id", "") or "")
    lowered = message.lower()
    if any(word in lowered for word in ("invalid api", "invalidapikey", "unauthorized", "401", "authentication", "accessdenied")):
        kind = ErrorKind.AUTHENTICATION
    elif any(word in lowered for word in ("quota", "quotaexceeded", "balance", "insufficient", "billing")):
        kind = ErrorKind.QUOTA
    elif any(word in lowered for word in ("model", "parameter", "invalidparameter", "bad request", "400")):
        kind = ErrorKind.CONFIGURATION
    else:
        kind = ErrorKind.TEMPORARY
    return AsrError(message, kind, request_id)


def _events_from_result(
    result: Any, source_prefix: str, require_final: bool = False
) -> list[TranscriptEvent]:
    getter = getattr(result, "get_sentence", None)
    sentences = getter() if callable(getter) else result
    if isinstance(sentences, dict):
        sentences = [sentences]
    if not isinstance(sentences, list):
        return []
    request_getter = getattr(result, "get_request_id", None)
    request_id = (
        str(request_getter() or "")
        if callable(request_getter)
        else str(getattr(result, "request_id", "") or "")
    )
    events: list[TranscriptEvent] = []
    for index, sentence in enumerate(sentences):
        if not isinstance(sentence, dict):
            sentence = _plain(sentence)
        if not isinstance(sentence, dict):
            continue
        text = str(sentence.get("text") or "").strip()
        if not text:
            continue
        begin_ms = sentence.get("begin_time")
        end_ms = sentence.get("end_time")
        start_sample = int(float(begin_ms) * SAMPLE_RATE / 1000) if begin_ms is not None else None
        end_sample = int(float(end_ms) * SAMPLE_RATE / 1000) if end_ms is not None else None
        identity = sentence.get("sentence_id")
        if identity is None:
            identity = f"{start_sample if start_sample is not None else index}"
        events.append(
            TranscriptEvent(
                text=text,
                start_sample=start_sample,
                end_sample=end_sample,
                is_final=require_final or end_ms is not None,
                source_key=f"{source_prefix}:{identity}",
                request_id=request_id,
                raw=_plain(sentence),
            )
        )
    return events


def _nested(value: Any, *keys: Any) -> Any:
    current = value
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return None
    return current


def _text_from_http_result(value: Any) -> str:
    payload = _plain(value)
    candidates = (
        _nested(payload, "output", "text"),
        _nested(payload, "output", "output", "sentence", "text"),
        _nested(payload, "output", "choices", 0, "message", "content", 0, "text"),
        _nested(payload, "choices", 0, "message", "content", 0, "text"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _request_id(value: Any) -> str:
    payload = _plain(value)
    if isinstance(payload, dict):
        return str(payload.get("request_id") or payload.get("requestId") or "")
    return str(getattr(value, "request_id", "") or "")


def _audio_data_uri(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "audio/wav"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _context_messages(prompt: str) -> list[dict[str, Any]]:
    text = prompt.strip()[:MAX_TRANSCRIPTION_PROMPT_LENGTH]
    if not text:
        return []
    return [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }
    ]


def _multimodal_messages(audio_data: str, prompt: str) -> list[dict[str, Any]]:
    messages = _context_messages(prompt)
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {"data": audio_data},
                }
            ],
        }
    )
    return messages


def _single_text_event(
    text: str, path: Path, source_prefix: str, request_id: str, raw: Any
) -> list[TranscriptEvent]:
    try:
        with wave.open(str(path), "rb") as audio:
            end_sample = audio.getnframes()
    except (OSError, wave.Error):
        end_sample = None
    return [
        TranscriptEvent(
            text=text,
            start_sample=0,
            end_sample=end_sample,
            is_final=True,
            source_key=f"{source_prefix}:0",
            request_id=request_id,
            raw=_plain(raw),
        )
    ]


class _DashStream(StreamingSession):
    def __init__(self, recognition: Any, callback: Any) -> None:
        self._recognition = recognition
        self._callback = callback
        self.request_id = str(recognition.get_last_request_id() or "")
        self._released = False

    def send(self, pcm: bytes) -> None:
        if self._callback.error is not None:
            raise self._callback.error
        try:
            self._recognition.send_audio_frame(pcm)
        except Exception as exc:
            raise _error_from(exc) from exc

    def stop(self) -> None:
        try:
            self._recognition.stop()
            if self._callback.error is not None:
                raise self._callback.error
        except AsrError:
            raise
        except Exception as exc:
            raise _error_from(exc) from exc
        finally:
            self._release()

    def abort(self) -> None:
        close = getattr(self._recognition, "stop", None)
        if callable(close):
            worker = threading.Thread(target=self._quiet_stop, args=(close,), daemon=True)
            worker.start()
            worker.join(timeout=2.0)
        self._release()

    @staticmethod
    def _quiet_stop(close: Any) -> None:
        try:
            close()
        except Exception:
            pass

    def _release(self) -> None:
        if not self._released:
            self._released = True
            _SDK_GLOBAL_LOCK.release()


class DashScopeAsrProvider:
    is_mock = False

    def __init__(self, config: AppConfig, api_key: str) -> None:
        if not api_key.strip():
            raise AsrError("请先填写百炼 API Key", ErrorKind.AUTHENTICATION)
        self.config = AppConfig(**config.snapshot())
        self.api_key = api_key.strip()

    @property
    def supports_streaming(self) -> bool:
        return _model_mode(self.config.model) == "recognition"

    def _configure_recognition(self) -> tuple[Any, Any, Any]:
        try:
            import dashscope
            from dashscope.audio.asr import Recognition, RecognitionCallback
        except ImportError as exc:
            raise AsrError("缺少 dashscope 依赖，请先运行 setup.ps1", ErrorKind.LOCAL) from exc
        dashscope.api_key = self.api_key
        dashscope.base_websocket_api_url = _websocket_url(self.config.endpoint())
        return dashscope, Recognition, RecognitionCallback

    def _configure_http(self) -> tuple[Any, Any]:
        try:
            import dashscope
            from dashscope import MultiModalConversation
        except ImportError as exc:
            raise AsrError("缺少 dashscope 依赖，请先运行 setup.ps1", ErrorKind.LOCAL) from exc
        dashscope.api_key = self.api_key
        dashscope.base_http_api_url = _http_base_url(self.config.endpoint())
        return dashscope, MultiModalConversation

    def _options(self, audio_format: str) -> dict[str, Any]:
        values: dict[str, Any] = {
            "model": self.config.model,
            "format": audio_format,
            "sample_rate": SAMPLE_RATE,
            "heartbeat": True,
            "semantic_punctuation_enabled": False,
        }
        hints = self.config.language_hints()
        if hints:
            values["language_hints"] = hints
        return values

    def transcribe_file(self, path: Path, source_prefix: str) -> list[TranscriptEvent]:
        with _SDK_GLOBAL_LOCK:
            mode = _model_mode(self.config.model)
            if mode == "recognition":
                return self._transcribe_with_recognition(path, source_prefix)
            if mode == "qwen3-http":
                return self._transcribe_qwen3(path, source_prefix)
            if mode == "flash-http":
                return self._transcribe_flash(path, source_prefix)
            raise AsrError(
                f"模型 {self.config.model} 只接受公网音频 URL，当前任务使用本地音频片段。"
                "请选择 qwen3-asr-flash、qwen-audio-3.0-asr-flash，"
                "或改用支持实时二进制音频的模型。",
                ErrorKind.CONFIGURATION,
            )

    def _transcribe_with_recognition(
        self, path: Path, source_prefix: str
    ) -> list[TranscriptEvent]:
        _, Recognition, _ = self._configure_recognition()
        try:
            recognition = Recognition(callback=None, **self._options("wav"))
            context = _context_messages(self.config.transcription_prompt)
            if context:
                result = recognition.call(
                    str(path), raw_input={"context": context}
                )
            else:
                result = recognition.call(str(path))
        except Exception as exc:
            raise _error_from(exc) from exc
        status = getattr(result, "status_code", HTTPStatus.OK)
        if status != HTTPStatus.OK:
            raise _error_from(result)
        events = _events_from_result(result, source_prefix, require_final=True)
        if not events:
            raise AsrError(
                "云端请求成功，但没有返回可用文字",
                ErrorKind.TEMPORARY,
                str(recognition.get_last_request_id() or ""),
            )
        return events

    def _transcribe_qwen3(
        self, path: Path, source_prefix: str
    ) -> list[TranscriptEvent]:
        _, conversation = self._configure_http()
        options: dict[str, Any] = {"enable_itn": False}
        if self.config.language != "auto":
            options["language"] = self.config.language
        try:
            messages: list[dict[str, Any]] = []
            prompt = self.config.transcription_prompt.strip()[
                :MAX_TRANSCRIPTION_PROMPT_LENGTH
            ]
            if prompt:
                messages.append(
                    {"role": "user", "content": [{"text": prompt}]}
                )
            messages.append(
                {
                    "role": "user",
                    "content": [{"audio": _audio_data_uri(path)}],
                }
            )
            response = conversation.call(
                api_key=self.api_key,
                model=self.config.model,
                messages=messages,
                result_format="message",
                asr_options=options,
            )
        except Exception as exc:
            raise _error_from(exc) from exc
        status = getattr(response, "status_code", HTTPStatus.OK)
        if status != HTTPStatus.OK:
            raise _error_from(response)
        text = _text_from_http_result(response)
        if not text:
            raise AsrError(
                "云端请求成功，但响应中没有可用文字",
                ErrorKind.TEMPORARY,
                _request_id(response),
            )
        return _single_text_event(
            text, path, source_prefix, _request_id(response), response
        )

    def _transcribe_flash(
        self, path: Path, source_prefix: str
    ) -> list[TranscriptEvent]:
        http_base = _http_base_url(self.config.endpoint()).rstrip("/")
        url = f"{http_base}/services/aigc/multimodal-generation/generation"
        parameters: dict[str, Any]
        if self.config.model.strip().lower().startswith("qwen3-asr-flash"):
            parameters = {}
        else:
            parameters = {
                "format": path.suffix.lstrip(".") or "wav",
                "sample_rate": str(SAMPLE_RATE),
            }
        payload = {
            "model": self.config.model,
            "input": {
                "messages": _multimodal_messages(
                    _audio_data_uri(path), self.config.transcription_prompt
                )
            },
            "parameters": parameters,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-DashScope-SSE": "disable",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            raise _error_from(f"HTTP {exc.code}: {detail}") from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise _error_from(exc) from exc
        text = _text_from_http_result(result)
        if not text:
            raise AsrError(
                "云端请求成功，但响应中没有可用文字",
                ErrorKind.TEMPORARY,
                _request_id(result),
            )
        return _single_text_event(text, path, source_prefix, _request_id(result), result)

    def start_stream(self, sink: EventSink) -> StreamingSession:
        if not self.supports_streaming:
            if _model_mode(self.config.model) == "qwen3-realtime":
                detail = (
                    "该模型使用 Qwen Realtime 协议，当前版本尚未接入。"
                    "请先选择 qwen-audio-3.0-asr-flash-streaming。"
                )
            else:
                detail = (
                    "该模型使用 HTTP 文件识别，无法建立实时音频流。"
                    "实时录音请选择 qwen-audio-3.0-asr-flash-streaming "
                    "或 fun-asr-realtime。"
                )
            raise AsrError(
                f"模型 {self.config.model}：{detail}",
                ErrorKind.CONFIGURATION,
            )
        _SDK_GLOBAL_LOCK.acquire()
        try:
            _, Recognition, RecognitionCallback = self._configure_recognition()

            class Callback(RecognitionCallback):
                def __init__(self) -> None:
                    super().__init__()
                    self.error: AsrError | None = None

                def on_open(self) -> None:
                    return None

                def on_event(self, result: Any) -> None:
                    for event in _events_from_result(result, "live"):
                        sink(event)

                def on_complete(self) -> None:
                    return None

                def on_error(self, result: Any) -> None:
                    self.error = _error_from(result)

                def on_close(self) -> None:
                    return None

            callback = Callback()
            recognition = Recognition(callback=callback, **self._options("pcm"))
            recognition.start()
            return _DashStream(recognition, callback)
        except Exception as exc:
            _SDK_GLOBAL_LOCK.release()
            if isinstance(exc, AsrError):
                raise
            raise _error_from(exc) from exc
