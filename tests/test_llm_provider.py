from __future__ import annotations

import json

from asr_client.models import AppConfig
from asr_client.providers.dashscope_llm import (
    LLM_SOCKET_TIMEOUT_SECONDS,
    DashScopeLlmProvider,
    _llm_base_url,
    _response_text,
)


def test_llm_url_is_derived_from_asr_workspace_url() -> None:
    host = "workspace.cn-beijing.maas.aliyuncs.com"
    assert _llm_base_url(f"wss://{host}/api-ws/v1/inference") == (
        f"https://{host}/compatible-mode/v1"
    )
    assert _llm_base_url(f"https://{host}/api/v1") == (
        f"https://{host}/compatible-mode/v1"
    )


def test_llm_response_supports_string_and_content_parts() -> None:
    assert _response_text({"choices": [{"message": {"content": "完成"}}]}) == "完成"
    assert _response_text(
        {
            "choices": [
                {"message": {"content": [{"text": "第一"}, {"text": "第二"}]}}
            ]
        }
    ) == "第一第二"


def test_llm_provider_posts_openai_compatible_request(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"id": "request-1", "choices": [{"message": {"content": "已修复"}}]}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = AppConfig(
        workspace_id="workspace",
        llm_model="qwen-plus",
        model="qwen-audio-3.0-asr-flash-streaming",
    )
    result = DashScopeLlmProvider(config, "secret").complete("清晰化", "原文")
    assert captured["url"].endswith("/compatible-mode/v1/chat/completions")
    assert captured["body"]["model"] == "qwen-plus"
    assert captured["body"]["messages"][1]["content"].endswith(
        "<transcript>\n原文\n</transcript>"
    )
    assert captured["body"]["stream"] is False
    assert captured["body"]["enable_thinking"] is False
    assert captured["timeout"] == LLM_SOCKET_TIMEOUT_SECONDS
    assert result.text == "已修复"
    assert result.request_id == "request-1"
