from __future__ import annotations

import json

import keyring

from asr_client.models import AppConfig
from asr_client.storage.config import ApiKeyStore, ConfigStore, ScenarioStore


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
    config = AppConfig(
        data_dir=str(tmp_path), chunk_seconds=99999, remember_key=True
    )
    store.save(config)
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert "api_key" not in raw
    assert raw["_schema_version"] == 2
    loaded = store.load()
    assert loaded.chunk_seconds == 10 * 60
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


def test_legacy_second_based_chunk_value_is_migrated_and_capped(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"chunk_seconds": 60}), encoding="utf-8")
    loaded = ConfigStore(path).load()
    assert loaded.chunk_seconds == 10 * 60


def test_scenarios_save_apply_replace_and_delete(tmp_path) -> None:
    store = ScenarioStore(tmp_path / "scenarios.json")
    current = AppConfig(
        model="fun-asr-realtime",
        llm_model="qwen-plus",
        transcription_prompt="JustSpeak、百炼",
        polish_prompt="整理成会议纪要：{text}",
        chunk_seconds=7 * 60,
        data_dir=str(tmp_path / "data"),
    )
    first = store.save("会议", current)
    assert len(store.list()) == 1
    updated = AppConfig(
        model="qwen-audio-3.0-asr-flash-streaming",
        llm_model="qwen-max",
        transcription_prompt="Aurora、Qwen-Audio",
        polish_prompt="保留行动项",
        chunk_seconds=9 * 60,
    )
    second = store.save("会议", updated)
    assert second["id"] == first["id"]
    assert len(store.list()) == 1
    applied = store.apply_to(str(first["id"]), current)
    assert applied is not None
    assert applied.llm_model == "qwen-max"
    assert applied.transcription_prompt == "Aurora、Qwen-Audio"
    assert applied.chunk_seconds == 9 * 60
    assert applied.data_dir == current.data_dir
    assert store.delete(str(first["id"]))
    assert store.list() == []
