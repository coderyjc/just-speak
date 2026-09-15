from __future__ import annotations

from asr_client.models import AppConfig
from asr_client.providers.dashscope_asr import (
    DashScopeAsrProvider,
    _context_messages,
    _events_from_result,
    _error_from,
    _http_base_url,
    _model_mode,
    _multimodal_messages,
    _text_from_http_result,
    _websocket_url,
)
from asr_client.providers.base import AsrError, ErrorKind


class Result:
    def get_sentence(self):
        return {"text": "你好", "begin_time": 125, "end_time": 875}

    def get_request_id(self):
        return "request-1"


def test_dashscope_result_timestamps_become_samples() -> None:
    event = _events_from_result(Result(), "test")[0]
    assert event.start_sample == 2000
    assert event.end_sample == 14000
    assert event.is_final
    assert event.request_id == "request-1"


def test_retry_after_hint_is_respected() -> None:
    assert AsrError("rate limited; retry-after: 7.5 seconds").retry_delay(2) == 7.5


def test_no_input_audio_error_stops_retries_with_actionable_hint() -> None:
    class Response:
        code = "NO_INPUT_AUDIO_ERROR"
        message = "No valid speech was detected"
        request_id = "request-no-audio"

    error = _error_from(Response())
    assert error.kind == ErrorKind.CONFIGURATION
    assert not error.retryable
    assert error.request_id == "request-no-audio"
    assert "自动识别" in str(error)


def test_model_mode_selects_the_matching_dashscope_api() -> None:
    assert _model_mode("fun-asr-realtime") == "recognition"
    assert _model_mode("qwen-audio-3.0-asr-flash-streaming") == "recognition"
    assert _model_mode("qwen3-asr-flash") == "qwen3-http"
    assert _model_mode("qwen-audio-3.0-asr-flash") == "flash-http"
    assert _model_mode("qwen3-asr-flash-filetrans") == "flash-http"
    assert _model_mode("fun-asr") == "public-url-only"
    assert _model_mode("qwen3-asr-flash-realtime") == "qwen3-realtime"


def test_dashscope_url_normalization() -> None:
    host = "workspace.cn-beijing.maas.aliyuncs.com"
    assert _http_base_url(f"wss://{host}/api-ws/v1/inference") == (
        f"https://{host}/api/v1"
    )
    assert _websocket_url(f"https://{host}/api/v1") == (
        f"wss://{host}/api-ws/v1/inference"
    )


def test_http_response_text_shapes_are_supported() -> None:
    assert _text_from_http_result({"output": {"text": "第一段"}}) == "第一段"
    assert _text_from_http_result(
        {"output": {"choices": [{"message": {"content": [{"text": "第二段"}]}}]}}
    ) == "第二段"


def test_context_message_precedes_audio_and_is_limited_to_400_chars() -> None:
    messages = _multimodal_messages("data:audio/wav;base64,AAAA", "术" * 401)
    assert messages[0] == {
        "role": "user",
        "content": [{"type": "input_text", "text": "术" * 400}],
    }
    assert messages[1]["content"][0]["type"] == "input_audio"
    assert _context_messages("   ") == []


def test_recognition_file_call_receives_context(monkeypatch, tmp_path) -> None:
    calls = []

    class Recognition:
        def __init__(self, **kwargs):
            pass

        def call(self, path, **kwargs):
            calls.append((path, kwargs))
            return Result()

    provider = DashScopeAsrProvider(
        AppConfig(
            model="qwen-audio-3.0-asr-flash-streaming",
            transcription_prompt="JustSpeak、Aurora",
        ),
        "sk-test",
    )
    monkeypatch.setattr(
        provider,
        "_configure_recognition",
        lambda: (None, Recognition, object),
    )
    events = provider.transcribe_file(tmp_path / "chunk.wav", "unit")
    assert events[0].text == "你好"
    assert calls[0][1]["raw_input"] == {
        "context": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "JustSpeak、Aurora"}
                ],
            }
        ]
    }


def test_recognition_stream_start_receives_context(monkeypatch) -> None:
    calls = []

    class RecognitionCallback:
        pass

    class Recognition:
        def __init__(self, callback, **kwargs):
            self.callback = callback

        def start(self, **kwargs):
            calls.append(kwargs)

        def get_last_request_id(self):
            return "stream-context"

        def stop(self):
            pass

    provider = DashScopeAsrProvider(
        AppConfig(transcription_prompt="项目 Aurora、客户万里"), "sk-test"
    )
    monkeypatch.setattr(
        provider,
        "_configure_recognition",
        lambda: (None, Recognition, RecognitionCallback),
    )
    stream = provider.start_stream(lambda event: None)
    stream.stop()

    assert calls == [
        {
            "raw_input": {
                "context": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "项目 Aurora、客户万里",
                            }
                        ],
                    }
                ]
            }
        }
    ]
