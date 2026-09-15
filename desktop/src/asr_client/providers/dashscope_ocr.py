from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from asr_client.models import AppConfig
from asr_client.providers.base import AsrError, ErrorKind


OCR_SOCKET_TIMEOUT_SECONDS = 55
PURE_TEXT_REQUIREMENT = (
    "输出格式要求：只返回可见的纯文本；禁止输出 HTML、XML、Markdown、"
    "代码块、JSON、坐标、CSS 样式及任何标签。"
)


class _OcrHtmlTextExtractor(HTMLParser):
    _BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "div", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main",
        "nav", "ol", "p", "section", "table", "tr", "ul",
    }
    _HIDDEN_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden_depth = 0

    def _break(self) -> None:
        if self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._HIDDEN_TAGS:
            self._hidden_depth += 1
            return
        if self._hidden_depth:
            return
        if tag == "br" or tag in self._BLOCK_TAGS:
            self._break()
        elif tag in {"td", "th"} and self.parts and self.parts[-1] != "\n":
            self.parts.append("\t")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() in self._HIDDEN_TAGS:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._HIDDEN_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
            return
        if self._hidden_depth:
            return
        if tag in self._BLOCK_TAGS:
            self._break()

    def handle_data(self, data: str) -> None:
        if data and not self._hidden_depth:
            self.parts.append(data)


def _plain_ocr_text(value: str) -> str:
    """Convert OCR layout markup to readable text before it reaches ASR."""
    text = value.strip()
    fenced = re.fullmatch(
        r"```(?:html|xml|markdown|md|text)?\s*(.*?)\s*```", text, re.I | re.S
    )
    if fenced:
        text = fenced.group(1).strip()
    text = unescape(text)
    if re.search(r"\\?</?[a-zA-Z][^>]*>", text):
        text = re.sub(r"\\(?=[<>])", "", text)
        parser = _OcrHtmlTextExtractor()
        parser.feed(text)
        parser.close()
        text = "".join(parser.parts)
    lines = []
    for line in text.splitlines():
        normalized = re.sub(r"[ \r\f\v]+", " ", line).strip()
        normalized = re.sub(r" *\t *", "\t", normalized)
        if normalized:
            lines.append(normalized)
    return "\n".join(lines).strip()


def _ocr_base_url(value: str) -> str:
    source = value.strip()
    if "://" not in source:
        source = f"https://{source}"
    parsed = urlsplit(source)
    scheme = {"wss": "https", "ws": "http"}.get(parsed.scheme, parsed.scheme)
    path = parsed.path.rstrip("/")
    if parsed.hostname and parsed.hostname.endswith("aliyuncs.com"):
        path = "/compatible-mode/v1"
    elif path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _response_text(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "").strip()
            for item in content
            if isinstance(item, dict)
        ).strip()
    return ""


class DashScopeOcrProvider:
    def __init__(self, config: AppConfig, api_key: str) -> None:
        if not api_key.strip():
            raise AsrError("信息增强需要百炼 API Key", ErrorKind.AUTHENTICATION)
        if not config.ocr_model.strip():
            raise AsrError("请先填写 OCR Model ID", ErrorKind.CONFIGURATION)
        self.config = AppConfig(**config.snapshot())
        self.api_key = api_key.strip()

    def extract_text(self, image_data_url: str) -> str:
        prompt = self.config.ocr_prompt.strip()
        if not prompt:
            raise AsrError("OCR 提取指令不能为空", ErrorKind.CONFIGURATION)
        payload = {
            "model": self.config.ocr_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url},
                            "min_pixels": 32 * 32 * 3,
                            "max_pixels": 32 * 32 * 8192,
                        },
                        {
                            "type": "text",
                            "text": f"{prompt}\n\n{PURE_TEXT_REQUIREMENT}",
                        },
                    ],
                }
            ],
            "stream": False,
        }
        url = f"{_ocr_base_url(self.config.ocr_endpoint()).rstrip('/')}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Connection": "close",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=OCR_SOCKET_TIMEOUT_SECONDS
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            raise AsrError(
                f"OCR HTTP {exc.code}: {detail}",
                ErrorKind.CONFIGURATION if 400 <= exc.code < 500 else ErrorKind.TEMPORARY,
            ) from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise AsrError(f"OCR 请求失败：{exc}", ErrorKind.TEMPORARY) from exc
        output = _plain_ocr_text(_response_text(result))
        if not output:
            raise AsrError("OCR 请求成功，但响应中没有可用文字", ErrorKind.TEMPORARY)
        return output
