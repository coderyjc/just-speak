from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from asr_client.models import AppConfig
from asr_client.models import MAX_OCR_CONTEXT_LENGTH, MIN_OCR_CONTEXT_LENGTH


APP_NAME = "JustSpeak"
KEYRING_SERVICE = "JustSpeak-ASR"
KEYRING_ACCOUNT = "dashscope-api-key"
CONFIG_SCHEMA_VERSION = 2
MIN_CHUNK_SECONDS = 60
MAX_CHUNK_SECONDS = 10 * 60
MIN_HISTORY_LIMIT = 10
MAX_HISTORY_LIMIT = 600
SCENARIO_FIELDS = (
    "region",
    "workspace_id",
    "base_url",
    "model",
    "llm_model",
    "transcription_prompt",
    "information_enhancement_enabled",
    "ocr_model",
    "ocr_base_url",
    "ocr_prompt",
    "ocr_context_max_chars",
    "polish_prompt",
    "language",
    "chunk_seconds",
)


def _normalized_chunk_seconds(value: object) -> int:
    try:
        seconds = round(int(value) / 60) * 60
    except (TypeError, ValueError):
        seconds = AppConfig().chunk_seconds
    return max(MIN_CHUNK_SECONDS, min(MAX_CHUNK_SECONDS, seconds))


def _normalized_history_limit(value: object) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = AppConfig().history_limit
    return max(MIN_HISTORY_LIMIT, min(MAX_HISTORY_LIMIT, limit))


def _normalized_ocr_context_length(value: object) -> int:
    try:
        length = int(value)
    except (TypeError, ValueError):
        length = AppConfig().ocr_context_max_chars
    return max(MIN_OCR_CONTEXT_LENGTH, min(MAX_OCR_CONTEXT_LENGTH, length))


def app_config_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / APP_NAME


def default_data_dir() -> Path:
    d_drive = Path("D:/")
    if d_drive.exists():
        return d_drive / "ASR-Client" / "data"
    return app_config_dir() / "data"


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_config_dir() / "config.json"

    def load(self) -> AppConfig:
        config = AppConfig(data_dir=str(default_data_dir()))
        if not self.path.exists():
            return config
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return config
            allowed = {name for name in asdict(config)}
            values = {k: v for k, v in raw.items() if k in allowed}
            # Version 1 exposed the same numeric control in seconds (30-120).
            # Preserve the chosen number while migrating the control to minutes.
            if int(raw.get("_schema_version", 1)) < CONFIG_SCHEMA_VERSION:
                if "chunk_seconds" in values:
                    legacy_value = int(values["chunk_seconds"])
                    if legacy_value <= 180:
                        values["chunk_seconds"] = legacy_value * 60
            loaded = AppConfig(**values)
            if not loaded.base_url and loaded.websocket_url:
                loaded.base_url = loaded.websocket_url
                loaded.websocket_url = ""
            if not loaded.data_dir:
                loaded.data_dir = str(default_data_dir())
            loaded.chunk_seconds = _normalized_chunk_seconds(loaded.chunk_seconds)
            loaded.history_limit = _normalized_history_limit(loaded.history_limit)
            loaded.ocr_context_max_chars = _normalized_ocr_context_length(
                loaded.ocr_context_max_chars
            )
            return loaded
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return config

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(config)
        payload["_schema_version"] = CONFIG_SCHEMA_VERSION
        payload["chunk_seconds"] = _normalized_chunk_seconds(config.chunk_seconds)
        payload["history_limit"] = _normalized_history_limit(config.history_limit)
        payload["ocr_context_max_chars"] = _normalized_ocr_context_length(
            config.ocr_context_max_chars
        )
        payload.pop("remember_key", None)
        payload["remember_key"] = bool(config.remember_key)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, self.path)


class ScenarioStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_config_dir() / "scenarios.json"

    def list(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return []
            return [item for item in payload if self._valid(item)]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []

    def get(self, scenario_id: str) -> dict[str, object] | None:
        return next(
            (item for item in self.list() if item.get("id") == scenario_id), None
        )

    def save(self, name: str, config: AppConfig) -> dict[str, object]:
        name = name.strip()
        if not name:
            raise ValueError("场景名称不能为空")
        scenarios = self.list()
        existing = next((item for item in scenarios if item.get("name") == name), None)
        values = asdict(config)
        item: dict[str, object] = {
            "id": existing.get("id") if existing else uuid.uuid4().hex,
            "name": name,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config": {field: values[field] for field in SCENARIO_FIELDS},
        }
        if existing:
            scenarios[scenarios.index(existing)] = item
        else:
            scenarios.append(item)
        self._write(scenarios)
        return item

    def delete(self, scenario_id: str) -> bool:
        scenarios = self.list()
        remaining = [item for item in scenarios if item.get("id") != scenario_id]
        if len(remaining) == len(scenarios):
            return False
        self._write(remaining)
        return True

    def apply_to(self, scenario_id: str, current: AppConfig) -> AppConfig | None:
        item = self.get(scenario_id)
        if item is None:
            return None
        values = asdict(current)
        scenario_config = item.get("config")
        if isinstance(scenario_config, dict):
            for field in SCENARIO_FIELDS:
                if field in scenario_config:
                    values[field] = scenario_config[field]
        return AppConfig(**values)

    def _write(self, scenarios: list[dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(scenarios, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, self.path)

    @staticmethod
    def _valid(item: object) -> bool:
        return (
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("config"), dict)
        )


class ApiKeyStore:
    def __init__(self) -> None:
        self._session_key = ""

    def get(self) -> str:
        if self._session_key:
            return self._session_key
        try:
            import keyring

            return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or ""
        except Exception:
            return ""

    def set(self, key: str, remember: bool) -> None:
        key = key.strip()
        self._session_key = key
        if not remember:
            try:
                import keyring
                from keyring.errors import PasswordDeleteError

                try:
                    keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
                except PasswordDeleteError:
                    pass
            except Exception:
                pass
            return
        try:
            import keyring

            if key:
                keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, key)
            else:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        except Exception as exc:
            raise RuntimeError("Windows 凭据存储不可用，密钥仅在本次会话中保留") from exc
