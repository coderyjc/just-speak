from __future__ import annotations

from asr_client.providers.dashscope_asr import (
    _events_from_result,
    _http_base_url,
    _model_mode,
    _text_from_http_result,
    _websocket_url,
)
from asr_client.providers.base import AsrError


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


def test_model_mode_selects_the_matching_dashscope_api() -> None:
    assert _model_mode("fun-asr-realtime") == "recognition"
    assert _model_mode("qwen-audio-3.0-asr-flash-streaming") == "recognition"
    assert _model_mode("qwen3-asr-flash") == "qwen3-http"
    assert _model_mode("qwen-audio-3.0-asr-flash") == "flash-http"
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
