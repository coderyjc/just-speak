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
import asr_client.ui.main_window as main_window_module
from asr_client.ui.main_window import MainWindow
from asr_client.ui.pages import RealtimePage, SettingsPage


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_realtime_page_switches_between_recording_and_paused_actions() -> None:
    app = _app()
    page = RealtimePage()
    page.device.addItem("测试麦克风", 0)
    page.set_shortcut_hints("空格", "S")

    dot_position = page.orb.pos()
    dot_size = page.orb.size()
    page.set_recording_state("recording")
    assert page.start.property("mode") == "pause"
    assert page.start.toolTip() == "暂停录音（空格）"
    assert page.stop.isEnabled()
    assert not page.device.isEnabled()
    assert page.orb.pos() == dot_position
    assert page.orb.size() == dot_size
    assert page.orb.graphicsEffect() is None

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


def test_screen_selector_shares_the_original_device_width() -> None:
    app = _app()
    page = RealtimePage()
    page.device.addItem("测试麦克风", 0)
    page.screen.addItem("屏幕 1：1920 × 1080", "DISPLAY1")
    page.resize(760, 520)
    page.show()
    app.processEvents()

    page.set_information_enhancement_enabled(False)
    app.processEvents()
    single_width = page.device.width()
    assert not page.screen.isVisible()

    page.set_information_enhancement_enabled(True)
    app.processEvents()
    assert page.screen.isVisible()
    assert abs(page.device.width() - page.screen.width()) <= 1
    assert (
        page.device.width()
        + page.screen.width()
        + page.input_selector_layout.spacing()
        == page.input_selectors.contentsRect().width()
    )
    assert page.device.width() + page.screen.width() < single_width

    page.set_recording_state("recording")
    assert not page.screen.isEnabled()
    page.set_recording_state("idle")
    assert page.screen.isEnabled()
    page.close()
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
        information_enhancement_enabled=True,
        ocr_model="qwen3.5-ocr-test",
        ocr_base_url="https://ocr.example/v1",
        ocr_prompt="只读取可见文字",
        ocr_context_max_chars=260,
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
    assert values.information_enhancement_enabled
    assert values.ocr_model == "qwen3.5-ocr-test"
    assert values.ocr_base_url == "https://ocr.example/v1"
    assert values.ocr_prompt == "只读取可见文字"
    assert values.ocr_context_max_chars == 260
    assert page.ocr_model.isEnabled()

    page.toggle_shortcut.setKeySequence(QKeySequence("F8"))
    page.stop_shortcut_enabled.setChecked(True)
    values = page.values()
    assert values.realtime_toggle_shortcut == "F8"
    assert values.realtime_stop_shortcut_enabled
    page.information_enhancement.setChecked(False)
    assert not page.ocr_model.isEnabled()
    page.deleteLater()
    app.processEvents()


def test_matching_enabled_shortcuts_are_rejected() -> None:
    assert MainWindow._shortcut_conflict(
        AppConfig(
            realtime_toggle_shortcut="F8",
            realtime_stop_shortcut="F8",
        )
    )


def test_realtime_enhancement_captures_the_selected_screen(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    config_store = ConfigStore(tmp_path / "config.json")
    config_store.save(
        AppConfig(
            data_dir=str(tmp_path),
            information_enhancement_enabled=True,
        )
    )
    database = Database(tmp_path / "db.sqlite3")

    class MemoryKeyStore:
        def get(self) -> str:
            return "sk-test"

        def set(self, key: str, remember: bool) -> None:
            pass

    def add_inputs(window: MainWindow) -> None:
        window.realtime_page.device.addItem("测试麦克风", 0)
        window.realtime_page.screen.addItem("屏幕 1：1920 × 1080", "DISPLAY1")
        window.realtime_page.screen.addItem("屏幕 2：2560 × 1440", "DISPLAY2")
        window.realtime_page.screen.setCurrentIndex(1)

    class OcrProvider:
        def __init__(self, config, api_key):
            pass

        def extract_text(self, image_data_url: str) -> str:
            assert image_data_url == "data:image/png;base64,AAAA"
            return "所选屏幕文字"

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self) -> None:
            self.target()

    captured = []
    launched = []
    monkeypatch.setattr(MainWindow, "refresh_microphones", add_inputs)
    monkeypatch.setattr(
        main_window_module,
        "capture_screen_data_url",
        lambda screen_name: captured.append(screen_name)
        or "data:image/png;base64,AAAA",
    )
    monkeypatch.setattr(main_window_module, "DashScopeOcrProvider", OcrProvider)
    monkeypatch.setattr(main_window_module.threading, "Thread", ImmediateThread)
    window = MainWindow(database, config_store, MemoryKeyStore())
    assert window.realtime_page.pipeline.buttons["context"].isChecked()
    assert window.realtime_page._stage_states["context"] == "pending"
    window._launch_realtime = lambda config, context_chars=0, enhancement_error="", **kwargs: (
        launched.append((config, context_chars, enhancement_error, kwargs))
    )

    window.start_realtime()
    app.processEvents()

    assert captured == ["DISPLAY2"]
    assert launched[0][1] == len("所选屏幕文字")
    assert "屏幕文字：所选屏幕文字" in launched[0][0].transcription_prompt
    assert launched[0][3]["screen_context_text"] == "所选屏幕文字"
    assert window.realtime_page.transcript.toPlainText() == "所选屏幕文字"
    window.close()
    database.close()
    assert not MainWindow._shortcut_conflict(
        AppConfig(
            realtime_toggle_shortcut="F8",
            realtime_stop_shortcut="F8",
            realtime_stop_shortcut_enabled=False,
        )
    )


def test_realtime_pipeline_moves_from_ready_context_to_asr_after_ocr() -> None:
    app = _app()
    page = RealtimePage()
    page.set_information_enhancement_enabled(True)

    assert page.pipeline.buttons["context"].isChecked()
    assert page._stage_states["context"] == "pending"
    assert "点击开始录音" in page.transcript.placeholderText()

    page.reset_pipeline(context_enabled=True, started=True)
    assert page.pipeline.buttons["context"].isChecked()
    assert page._stage_states["context"] == "running"

    page.set_stage_state("context", "completed", "屏幕上下文")
    page.set_stage_state("asr", "running")

    assert page.transcript.toPlainText() == ""
    assert page.pipeline.buttons["asr"].isChecked()

    page.set_stage_text("asr", "第一句实时转写")

    assert page.transcript.toPlainText() == "第一句实时转写"
    assert page.pipeline.buttons["asr"].isChecked()
    assert page.pipeline.buttons["asr"].graphicsEffect() is not None
    assert page.transcript.isReadOnly()

    page.set_stage_state("asr", "completed", "实时转写完成")
    page.set_stage_state("clarity", "running", "实时转写完成")
    assert page.pipeline.buttons["clarity"].isChecked()
    page.set_stage_state("clarity", "completed", "文本清洗完成")
    page.set_stage_state("polish", "running", "文本清洗完成")
    assert page.pipeline.buttons["polish"].isChecked()
    page.set_stage_state("polish", "completed", "定向修复完成")
    page.set_recording_state("idle")
    page.finish_pipeline()
    assert page.pipeline.buttons["polish"].isChecked()

    page.reset_for_next_round()
    assert page.pipeline.buttons["context"].isChecked()
    assert page._stage_states["context"] == "pending"
    page.deleteLater()
    app.processEvents()


def test_latest_ocr_context_limit_is_used_before_auto_save(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    config_store = ConfigStore(tmp_path / "config.json")
    config_store.save(
        AppConfig(
            data_dir=str(tmp_path),
            information_enhancement_enabled=True,
            ocr_context_max_chars=300,
        )
    )
    database = Database(tmp_path / "db.sqlite3")

    class MemoryKeyStore:
        def get(self) -> str:
            return "sk-test"

        def set(self, key: str, remember: bool) -> None:
            pass

    class OcrProvider:
        def __init__(self, config, api_key):
            pass

        def extract_text(self, image_data_url: str) -> str:
            return "屏" * 200

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self) -> None:
            self.target()

    def add_inputs(window: MainWindow) -> None:
        window.realtime_page.device.addItem("测试麦克风", 0)
        window.realtime_page.screen.addItem("屏幕 1：1920 × 1080", "DISPLAY1")

    monkeypatch.setattr(MainWindow, "refresh_microphones", add_inputs)
    monkeypatch.setattr(
        main_window_module,
        "capture_screen_data_url",
        lambda screen_name: "data:image/png;base64,AAAA",
    )
    monkeypatch.setattr(main_window_module, "DashScopeOcrProvider", OcrProvider)
    monkeypatch.setattr(main_window_module.threading, "Thread", ImmediateThread)
    window = MainWindow(database, config_store, MemoryKeyStore())
    launched = []
    window._launch_realtime = lambda config, **kwargs: launched.append((config, kwargs))

    window.settings_page.ocr_context_max_chars.setValue(80)
    window.start_realtime()
    app.processEvents()

    assert launched[0][0].ocr_context_max_chars == 80
    assert launched[0][0].transcription_prompt == f"屏幕文字：{'屏' * 80}"
    assert len(launched[0][1]["screen_context_text"]) == 80
    assert len(window.realtime_page.transcript.toPlainText()) == 80
    window.close()
    database.close()


def test_screen_context_stage_is_saved_with_realtime_session(
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

    class PassiveThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return False

    def add_microphone(window: MainWindow) -> None:
        window.realtime_page.device.addItem("测试麦克风", 0)

    monkeypatch.setattr(MainWindow, "refresh_microphones", add_microphone)
    monkeypatch.setattr(main_window_module.threading, "Thread", PassiveThread)
    window = MainWindow(database, config_store, MemoryKeyStore())
    config = AppConfig(
        data_dir=str(tmp_path),
        microphone=0,
        information_enhancement_enabled=True,
    )

    window._launch_realtime(
        config,
        context_chars=6,
        screen_context_text="屏幕上下文",
        pipeline_prepared=True,
    )

    stages = database.text_stages(window._realtime_session_id)
    assert len(stages) == 1
    assert stages[0]["stage"] == "context"
    assert stages[0]["ordinal"] == -1
    assert stages[0]["text"] == "屏幕上下文"
    assert stages[0]["label"] == "屏幕上下文"
    assert window.realtime_page.pipeline.buttons["asr"].isChecked()
    window.close()
    database.close()


def test_completed_polish_space_action_copies_final_text_and_resets(
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
    starts = []
    monkeypatch.setattr(window, "start_realtime", lambda: starts.append("start"))
    page = window.realtime_page
    page.set_information_enhancement_enabled(True)
    page.set_stage_state("context", "completed", "屏幕文字")
    page.set_stage_state("asr", "completed", "原始文本")
    page.set_stage_state("clarity", "completed", "清洗文本")
    page.set_stage_state("polish", "completed", "最终定向修复文本")
    page.set_recording_state("idle")
    page.finish_pipeline()
    page.pipeline.buttons["context"].click()
    assert page.transcript.toPlainText() == "屏幕文字"
    assert page.transcript.isReadOnly()

    window.toggle_realtime()

    assert QApplication.clipboard().text() == "最终定向修复文本"
    assert page.transcript.toPlainText() == ""
    assert not page.can_copy_and_reset()
    assert starts == []
    assert "再按空格" in page.page_status.text()
    assert page.pipeline.buttons["context"].isChecked()
    assert page._stage_states["context"] == "pending"

    window.toggle_realtime()
    assert starts == ["start"]
    window.close()
    database.close()


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
    assert window.navigation._selection_animation.state() == (
        window.navigation._selection_animation.State.Running
    )
    assert window.pages.currentWidget().graphicsEffect() is not None
    assert window.eventFilter(window, space)
    assert calls == ["toggle"]

    window.realtime_page.transcript.setReadOnly(False)
    window.realtime_page.transcript.setFocus()
    app.processEvents()
    assert not window.eventFilter(window, space)
    assert calls == ["toggle"]
    window.close()
    database.close()


def test_realtime_history_item_title_includes_character_count(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    config_store = ConfigStore(tmp_path / "config.json")
    config_store.save(AppConfig(data_dir=str(tmp_path)))
    database = Database(tmp_path / "db.sqlite3")
    session = database.create_session(
        "realtime", "实时录音", tmp_path / "task", {}
    )
    database.save_edited_text(session, "一二 三四", track_usage=True)

    class MemoryKeyStore:
        def get(self) -> str:
            return ""

        def set(self, key: str, remember: bool) -> None:
            pass

    monkeypatch.setattr(MainWindow, "refresh_microphones", lambda window: None)
    window = MainWindow(database, config_store, MemoryKeyStore())

    assert window.history_page.sessions.item(0).text().startswith("实时录音·4字\n")
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
