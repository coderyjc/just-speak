from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication

from asr_client.models import AppConfig
from asr_client.storage.config import ConfigStore
from asr_client.storage.database import Database
from asr_client.ui.main_window import MainWindow
from asr_client.ui.pages import RealtimePage, SettingsPage


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_realtime_page_switches_between_recording_and_paused_actions() -> None:
    app = _app()
    page = RealtimePage()
    page.device.addItem("测试麦克风", 0)
    page.set_shortcut_hints("空格", "S")

    page.set_recording_state("recording")
    assert page.start.property("mode") == "pause"
    assert page.start.toolTip() == "暂停录音（空格）"
    assert page.stop.isEnabled()
    assert not page.device.isEnabled()

    page.level.setValue(70)
    page.set_recording_state("paused")
    assert page.start.property("mode") == "start"
    assert page.start.toolTip() == "继续录音（空格）"
    assert page.start.isEnabled()
    assert page.stop.isEnabled()
    assert page.level.value() == 0
    page.set_level(0.8)
    assert page.level.value() == 0

    page.set_recording_state("stopping")
    assert not page.start.isEnabled()
    assert not page.stop.isEnabled()
    page.deleteLater()
    app.processEvents()


def test_shortcut_settings_can_be_changed_and_disabled() -> None:
    app = _app()
    config = AppConfig(
        data_dir="data",
        realtime_toggle_shortcut="Ctrl+Space",
        realtime_toggle_shortcut_enabled=True,
        realtime_stop_shortcut="Ctrl+S",
        realtime_stop_shortcut_enabled=False,
        history_limit=320,
    )
    page = SettingsPage(config, "", [])
    values = page.values()
    assert values.realtime_toggle_shortcut == "Ctrl+Space"
    assert values.realtime_toggle_shortcut_enabled
    assert values.realtime_stop_shortcut == "Ctrl+S"
    assert not values.realtime_stop_shortcut_enabled
    assert values.history_limit == 320
    assert page.history_limit.minimum() == 10
    assert page.history_limit.maximum() == 600
    assert not page.stop_shortcut.isEnabled()

    page.toggle_shortcut.setKeySequence(QKeySequence("F8"))
    page.stop_shortcut_enabled.setChecked(True)
    values = page.values()
    assert values.realtime_toggle_shortcut == "F8"
    assert values.realtime_stop_shortcut_enabled
    page.deleteLater()
    app.processEvents()


def test_matching_enabled_shortcuts_are_rejected() -> None:
    assert MainWindow._shortcut_conflict(
        AppConfig(
            realtime_toggle_shortcut="F8",
            realtime_stop_shortcut="F8",
        )
    )
    assert not MainWindow._shortcut_conflict(
        AppConfig(
            realtime_toggle_shortcut="F8",
            realtime_stop_shortcut="F8",
            realtime_stop_shortcut_enabled=False,
        )
    )


def test_shortcut_event_filter_is_scoped_to_realtime_page_and_editor(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    config_store = ConfigStore(tmp_path / "config.json")
    config_store.save(AppConfig(data_dir=str(tmp_path)))
    database = Database(tmp_path / "db.sqlite3")

    class MemoryKeyStore:
        def get(self) -> str:
            return ""

        def set(self, key: str, remember: bool) -> None:
            pass

    def add_microphone(window: MainWindow) -> None:
        window.realtime_page.device.addItem("测试麦克风", 0)

    monkeypatch.setattr(MainWindow, "refresh_microphones", add_microphone)
    window = MainWindow(database, config_store, MemoryKeyStore())
    window.show()
    app.processEvents()
    calls = []
    monkeypatch.setattr(window, "toggle_realtime", lambda: calls.append("toggle"))

    space = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Space,
        Qt.KeyboardModifier.NoModifier,
    )
    assert not window.eventFilter(window, space)
    assert calls == []

    window.navigation.setCurrentRow(1)
    window.realtime_page.device.setFocus()
    app.processEvents()
    assert window.eventFilter(window, space)
    assert calls == ["toggle"]

    window.realtime_page.transcript.setReadOnly(False)
    window.realtime_page.transcript.setFocus()
    app.processEvents()
    assert not window.eventFilter(window, space)
    assert calls == ["toggle"]
    window.close()
    database.close()


def test_window_prunes_oldest_history_and_verified_task_directory(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    database = Database(data_dir / "justspeak.sqlite3")
    base_time = datetime(2026, 9, 1, tzinfo=timezone.utc)
    session_ids = []
    task_dirs = []
    for index in range(11):
        session_id = database.create_session(
            "realtime", f"record-{index}", data_dir / "tasks" / "pending", {}
        )
        task_dir = data_dir / "tasks" / session_id
        task_dir.mkdir(parents=True)
        (task_dir / "transcript.txt").write_text("保留统计", encoding="utf-8")
        occurred_at = (base_time + timedelta(days=index)).isoformat(
            timespec="seconds"
        )
        with database.transaction() as connection:
            connection.execute(
                "UPDATE sessions SET task_dir=?, created_at=? WHERE id=?",
                (str(task_dir), occurred_at, session_id),
            )
            connection.execute(
                "UPDATE usage_ledger SET occurred_at=?, char_count=4 WHERE session_id=?",
                (occurred_at, session_id),
            )
        session_ids.append(session_id)
        task_dirs.append(task_dir)

    config_store = ConfigStore(tmp_path / "config.json")
    config_store.save(AppConfig(data_dir=str(data_dir), history_limit=10))

    class MemoryKeyStore:
        def get(self) -> str:
            return ""

        def set(self, key: str, remember: bool) -> None:
            pass

    monkeypatch.setattr(MainWindow, "refresh_microphones", lambda self: None)
    window = MainWindow(database, config_store, MemoryKeyStore())
    app.processEvents()

    assert len(database.list_sessions()) == 10
    assert database.get_session(session_ids[0]) is None
    assert not task_dirs[0].exists()
    assert database.usage_statistics()["total_chars"] == 44

    window.close()
    database.close()
