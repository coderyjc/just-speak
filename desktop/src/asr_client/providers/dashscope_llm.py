from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from asr_client.models import AppConfig, TextModelResult
from asr_client.providers.base import AsrError, ErrorKind


CLEAR_TEXT_PROMPT = """你是严谨的中文口语转写编辑器。请清洗输入文本：
1. 删除“嗯、啊、呃、然后、就是说”等没有语义贡献的口水词和无意义重复；
2. 修复语音识别造成的明显错别字、断句和标点；
3. 保留全部事实、观点、数字、专有名词和原有语气；
4. 不总结、不扩写、不解释，只输出清洗后的完整文本。"""
LLM_SOCKET_TIMEOUT_SECONDS = 55


def _llm_base_url(value: str) -> str:
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
        parts = [
            str(item.get("text") or "").strip()
            for item in content
            if isinstance(item, dict)
        ]
        return "".join(part for part in parts if part)
    return ""


class DashScopeLlmProvider:
    def __init__(self, config: AppConfig, api_key: str) -> None:
        if not api_key.strip():
            raise AsrError("请先填写百炼 API Key", ErrorKind.AUTHENTICATION)
        if not config.llm_model.strip():
            raise AsrError("请先填写 LLM Model ID", ErrorKind.CONFIGURATION)
        self.config = AppConfig(**config.snapshot())
        self.api_key = api_key.strip()

    def complete(self, prompt_template: str, text: str) -> TextModelResult:
        prompt_template = prompt_template.strip()
        if not prompt_template:
            raise AsrError("文本处理 Prompt 不能为空", ErrorKind.CONFIGURATION)
        if "{text}" in prompt_template:
            messages = [
                {"role": "user", "content": prompt_template.replace("{text}", text)}
            ]
        else:
            messages = [
                {"role": "system", "content": prompt_template},
                {
                    "role": "user",
                    "content": f"请处理以下转写文本：\n<transcript>\n{text}\n</transcript>",
                },
            ]
        payload = {
            "model": self.config.llm_model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False,
            "enable_thinking": False,
        }
        url = f"{_llm_base_url(self.config.endpoint()).rstrip('/')}/chat/completions"
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
                request, timeout=LLM_SOCKET_TIMEOUT_SECONDS
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            raise AsrError(
                f"LLM HTTP {exc.code}: {detail}",
                ErrorKind.CONFIGURATION if 400 <= exc.code < 500 else ErrorKind.TEMPORARY,
            ) from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise AsrError(f"LLM 请求失败：{exc}", ErrorKind.TEMPORARY) from exc
        output = _response_text(result)
        if not output:
            raise AsrError(
                "LLM 请求成功，但响应中没有可用文字", ErrorKind.TEMPORARY
            )
        return TextModelResult(
            text=output,
            request_id=str(result.get("id") or result.get("request_id") or ""),
            raw=result,
        )
