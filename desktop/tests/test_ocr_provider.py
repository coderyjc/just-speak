from __future__ import annotations

import json

from asr_client.models import AppConfig
from asr_client.providers.dashscope_ocr import (
    DashScopeOcrProvider,
    _ocr_base_url,
    _plain_ocr_text,
    _response_text,
)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_ocr_url_and_response_shapes() -> None:
    assert _ocr_base_url("ocr.example/v1/chat/completions") == (
        "https://ocr.example/v1"
    )
    assert _ocr_base_url(
        "wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
    ) == "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    assert _response_text(
        {"choices": [{"message": {"content": [{"text": "第一段"}, {"text": "第二段"}]}}]}
    ) == "第一段第二段"


def test_ocr_provider_sends_base64_image_and_prompt(monkeypatch) -> None:
    captured = {}

    def urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response(
            {"id": "ocr-1", "choices": [{"message": {"content": "屏幕文字"}}]}
        )

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    provider = DashScopeOcrProvider(
        AppConfig(
            workspace_id="workspace",
            ocr_model="qwen3.5-ocr",
            ocr_prompt="只输出文字",
        ),
        "sk-test",
    )

    result = provider.extract_text("data:image/png;base64,AAAA")

    assert result == "屏幕文字"
    assert captured["url"] == (
        "https://workspace.cn-beijing.maas.aliyuncs.com/"
        "compatible-mode/v1/chat/completions"
    )
    assert captured["authorization"] == "Bearer sk-test"
    content = captured["payload"]["messages"][0]["content"]
    assert content[0]["image_url"]["url"] == "data:image/png;base64,AAAA"
    assert content[1]["type"] == "text"
    assert content[1]["text"].startswith("只输出文字")
    assert "禁止输出 HTML" in content[1]["text"]


def test_ocr_html_layout_is_converted_to_plain_text() -> None:
    source = (
        "<html><body><p>chat.deepseek.com/a/chat</p>"
        "<div><img/></div><table><tr><td>日期</td><td>信息</td>"
        "<td>工作</td></tr><tr><td>周一</td><td>会议</td><td>整理</td>"
        "</tr></table></body></html>"
    )

    result = _plain_ocr_text(source)

    assert "<" not in result
    assert ">" not in result
    assert "chat.deepseek.com/a/chat" in result
    assert "日期\t信息\t工作" in result
    assert "周一\t会议\t整理" in result

    escaped = _plain_ocr_text(
        "&lt;div&gt;可见文字&lt;/div&gt;&lt;style&gt;.x{}&lt;/style&gt;"
    )
    assert escaped == "可见文字"
