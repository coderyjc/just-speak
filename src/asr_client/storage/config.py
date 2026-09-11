from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from asr_client.models import AppConfig


APP_NAME = "JustSpeak"
KEYRING_SERVICE = "JustSpeak-ASR"
KEYRING_ACCOUNT = "dashscope-api-key"


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
            allowed = {name for name in asdict(config)}
            values = {k: v for k, v in raw.items() if k in allowed}
            loaded = AppConfig(**values)
            if not loaded.base_url and loaded.websocket_url:
                loaded.base_url = loaded.websocket_url
                loaded.websocket_url = ""
            if not loaded.data_dir:
                loaded.data_dir = str(default_data_dir())
            loaded.chunk_seconds = max(30, min(120, int(loaded.chunk_seconds)))
            return loaded
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return config

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(config)
        payload.pop("remember_key", None)
        payload["remember_key"] = bool(config.remember_key)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, self.path)


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
