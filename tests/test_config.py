from __future__ import annotations

import json

import keyring

from asr_client.models import AppConfig
from asr_client.storage.config import ApiKeyStore, ConfigStore


def test_endpoint_generation() -> None:
    assert AppConfig(region="beijing").endpoint() == (
        "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    )
    assert AppConfig(region="singapore", workspace_id="ws1").endpoint() == (
        "wss://ws1.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference"
    )
    assert AppConfig(websocket_url="wss://custom.example/ws").endpoint() == (
        "wss://custom.example/ws"
    )
    assert AppConfig(
        region="beijing", workspace_id="ws2", model="qwen3-asr-flash"
    ).endpoint() == "https://ws2.cn-beijing.maas.aliyuncs.com/api/v1"
    assert AppConfig(
        base_url="https://custom.example/api/v1", model="qwen3-asr-flash"
    ).endpoint() == "https://custom.example/api/v1"


def test_model_protocol_capabilities() -> None:
    assert AppConfig(model="fun-asr-realtime").supports_realtime()
    assert AppConfig(model="qwen-audio-3.0-asr-flash-streaming").supports_realtime()
    assert AppConfig(model="fun-asr").uses_http_api()
    assert AppConfig(model="qwen3-asr-flash").uses_http_api()


def test_config_round_trip_contains_no_secret(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    config = AppConfig(data_dir=str(tmp_path), chunk_seconds=999, remember_key=True)
    store.save(config)
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert "api_key" not in raw
    loaded = store.load()
    assert loaded.chunk_seconds == 120
    assert loaded.remember_key is True


def test_disabling_remember_removes_stored_credential(monkeypatch) -> None:
    deleted = []
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda service, account: deleted.append((service, account)),
    )
    store = ApiKeyStore()
    store.set("session-secret", remember=False)
    assert store.get() == "session-secret"
    assert deleted == [("JustSpeak-ASR", "dashscope-api-key")]


def test_legacy_websocket_url_is_migrated(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"websocket_url": "wss://legacy.example/ws"}), encoding="utf-8"
    )
    loaded = ConfigStore(path).load()
    assert loaded.base_url == "wss://legacy.example/ws"
    assert loaded.websocket_url == ""
