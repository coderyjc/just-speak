from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QKeyEvent
from PySide6.QtWidgets import QApplication
import pytest

from asr_client.models import AppConfig, JobUpdate
from asr_client.storage.config import ConfigStore
from asr_client.storage.database import Database
from asr_client.ui.main_window import MainWindow
from asr_client.ui.mini_window import (
    VK_LMENU,
    VK_LCONTROL,
    VK_RMENU,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    GlobalAltHotkey,
    MiniShortcutEdit,
    MiniWindow,
    mini_shortcut_display,
)
from asr_client.ui.widgets import SidebarMiniButton
from asr_client.ui.theme import APP_STYLESHEET


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class MemoryKeyStore:
    def get(self) -> str:
        return ""

    def set(self, key: str, remember: bool) -> None:
        pass


def _main_window(tmp_path, monkeypatch) -> tuple[MainWindow, Database]:
    config_store = ConfigStore(tmp_path / "config.json")
    config_store.save(AppConfig(data_dir=str(tmp_path)))
    database = Database(tmp_path / "db.sqlite3")

    def add_microphone(window: MainWindow) -> None:
        window.realtime_page.device.addItem("测试麦克风", 0)

    monkeypatch.setattr(MainWindow, "refresh_microphones", add_microphone)
    return MainWindow(database, config_store, MemoryKeyStore()), database


def test_mini_window_uses_single_line_stage_labels_and_local_keys() -> None:
    app = _app()
    window = MiniWindow()
    window.show()
    app.processEvents()
    pause_requests = []
    cancel_requests = []
    home_requests = []
    window.pause_toggle_requested.connect(lambda: pause_requests.append(True))
    window.cancel_requested.connect(lambda: cancel_requests.append(True))
    window.home_requested.connect(lambda: home_requests.append(True))

    expected = {
        "context": "读取屏幕",
        "recording": "正在录音",
        "clarity": "文本清洗",
        "polish": "定向修复",
        "completed": "完成",
        "copied": "已复制到剪贴板",
    }
    for state, label in expected.items():
        window.set_status(state)
        assert window.status_label.text() == label

    window.set_status("recording")
    window.set_audio_level(0.8)
    window.shell._tick()
    assert window.shell.is_recording
    assert window.shell._timer.isActive()
    assert window.shell.display_level > 0
    window.set_status("paused")
    assert not window.shell.is_recording
    assert not window.shell._timer.isActive()
    assert window.shell.display_level == 0

    window.keyPressEvent(
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    window.keyPressEvent(
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert pause_requests == [True]
    assert cancel_requests == [True]
    window.home_button.click()
    assert home_requests == [True]
    assert window.home_button.accessibleName() == "返回主页"
    assert abs(window.status_label.geometry().center().y() - window.home_button.geometry().center().y()) <= 1
    assert abs(window.indicator.geometry().center().y() - window.home_button.geometry().center().y()) <= 1
    window.close_for_shutdown()
    window.deleteLater()
    app.processEvents()


def test_mini_shortcut_editor_captures_exact_right_alt() -> None:
    app = _app()
    editor = MiniShortcutEdit("F9")
    changes = []
    editor.shortcutChanged.connect(changes.append)
    editor.show()
    editor.begin_capture()
    editor.keyPressEvent(
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Alt,
            Qt.KeyboardModifier.AltModifier,
            0,
            VK_RMENU,
            0,
        )
    )
    editor.keyReleaseEvent(
        QKeyEvent(
            QEvent.Type.KeyRelease,
            Qt.Key.Key_Alt,
            Qt.KeyboardModifier.NoModifier,
            0,
            VK_RMENU,
            0,
        )
    )

    assert editor.shortcut() == "RightAlt"
    assert editor.text() == mini_shortcut_display("RightAlt")
    assert changes == ["RightAlt"]
    editor.deleteLater()
    app.processEvents()


def test_sidebar_mini_button_has_rounded_hover_animation() -> None:
    app = _app()
    previous_style = app.styleSheet()
    app.setStyleSheet(APP_STYLESHEET)
    button = SidebarMiniButton()
    button.show()
    app.processEvents()
    assert button.text() == "Mini"
    assert button.height() == 44

    button.enterEvent(QEnterEvent(QPointF(), QPointF(), QPointF()))
    assert button._hover_animation.state() == button._hover_animation.State.Running
    button.leaveEvent(QEvent(QEvent.Type.Leave))
    assert button._hover_animation.endValue() == 0.0
    button.deleteLater()
    app.processEvents()
    app.setStyleSheet(previous_style)


def test_main_and_mini_windows_are_mutually_exclusive(tmp_path, monkeypatch) -> None:
    app = _app()
    window, database = _main_window(tmp_path, monkeypatch)
    hotkey_states = []
    monkeypatch.setattr(
        window._mini_hotkey,
        "set_enabled",
        lambda enabled: hotkey_states.append(enabled) or enabled,
    )
    window.show()
    app.processEvents()

    window.navigation.setCurrentRow(2)
    window.mini_mode_button.click()
    app.processEvents()
    assert window._mini_mode
    assert not window.isVisible()
    assert window.mini_window.isVisible()
    assert hotkey_states == [True]

    window.mini_window.home_button.click()
    app.processEvents()
    assert not window._mini_mode
    assert window.isVisible()
    assert not window.mini_window.isVisible()
    assert window.navigation.currentRow() == 0
    assert hotkey_states == [True, False]

    window.close()
    database.close()


def test_right_alt_resets_a_completed_round_before_starting_the_next(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    window, database = _main_window(tmp_path, monkeypatch)
    starts = []
    monkeypatch.setattr(window, "start_realtime", lambda: starts.append(True))
    window._mini_mode = True

    window._handle_mini_global_hotkey()
    assert starts == [True]

    window._mini_round_complete = True
    window.mini_window.set_status("completed")
    window._handle_mini_global_hotkey()
    assert starts == [True]
    assert window.mini_window.status == "ready"

    window._handle_mini_global_hotkey()
    assert starts == [True, True]

    window._mini_mode = False
    window.close()
    database.close()
    app.processEvents()


def test_right_alt_stops_an_active_mini_recording(tmp_path, monkeypatch) -> None:
    app = _app()
    window, database = _main_window(tmp_path, monkeypatch)

    class ActiveJob:
        is_paused = False
        is_stopping = False

        def stop(self) -> None:
            self.is_stopping = True

    job = ActiveJob()
    window._mini_mode = True
    window._realtime_job = job
    window._mini_hotkey._enabled = True

    assert not window._mini_hotkey.process_key_event(VK_LMENU, WM_SYSKEYDOWN)
    window._mini_hotkey.process_key_event(VK_LMENU, WM_SYSKEYUP)
    assert not job.is_stopping
    assert window._mini_hotkey.process_key_event(VK_RMENU, WM_SYSKEYDOWN)
    assert job.is_stopping
    assert window.realtime_page._recording_state == "stopping"
    assert window.mini_window.status == "clarity"

    window._realtime_job = None
    window._mini_mode = False
    window.close()
    database.close()
    app.processEvents()


def test_completed_mini_round_copies_final_text_automatically(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    window, database = _main_window(tmp_path, monkeypatch)
    window._mini_mode = True
    window.realtime_page.reset_pipeline(False, started=True)
    window.realtime_page.set_stage_state("asr", "completed", "自动复制文本")

    window.on_job_update(
        "realtime", JobUpdate("completed", "录音与转写已完成", 100)
    )

    assert QApplication.clipboard().text() == "自动复制文本"
    assert window._mini_round_complete
    assert window.mini_window.status == "copied"
    assert window.mini_window.status_label.text() == "已复制到剪贴板"

    window._mini_mode = False
    window.close()
    database.close()
    app.processEvents()


def test_escape_aborts_an_active_main_window_recording(tmp_path, monkeypatch) -> None:
    app = _app()
    window, database = _main_window(tmp_path, monkeypatch)

    class ActiveJob:
        is_paused = False
        is_stopping = False

        def __init__(self) -> None:
            self.aborted = 0

        def abort(self) -> None:
            self.aborted += 1

        def stop(self) -> None:
            pass

    job = ActiveJob()
    window._realtime_job = job
    window.navigation.setCurrentRow(1)
    window.show()
    window.realtime_page.device.setFocus()
    app.processEvents()
    escape = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier,
    )

    assert window.eventFilter(window, escape)
    assert job.aborted == 1
    assert window._abort_reset_pending

    window._realtime_job = None
    window._abort_reset_pending = False
    window.close()
    database.close()


def test_global_hotkey_defaults_to_right_alt_and_supports_custom_shortcuts() -> None:
    app = _app()
    window = MiniWindow()
    hotkey = GlobalAltHotkey(window)
    calls = []
    hotkey.activated.connect(lambda key: calls.append(key))
    hotkey._enabled = True

    assert not hotkey.process_key_event(VK_LMENU, WM_SYSKEYDOWN)
    assert not hotkey.process_key_event(VK_LMENU, WM_SYSKEYUP)
    assert hotkey.process_key_event(VK_RMENU, WM_SYSKEYDOWN)
    assert hotkey.process_key_event(VK_RMENU, WM_SYSKEYDOWN)
    assert hotkey.process_key_event(VK_RMENU, WM_SYSKEYUP)
    assert calls == [VK_RMENU]

    hotkey.set_shortcut("LeftAlt")
    assert hotkey.process_key_event(VK_LMENU, WM_SYSKEYDOWN)
    assert hotkey.process_key_event(VK_LMENU, WM_SYSKEYUP)
    assert calls == [VK_RMENU, VK_LMENU]

    hotkey.set_shortcut("Ctrl+F8")
    assert not hotkey.process_key_event(VK_LCONTROL, WM_KEYDOWN)
    assert hotkey.process_key_event(0x77, WM_KEYDOWN)
    assert hotkey.process_key_event(0x77, WM_KEYUP)
    assert not hotkey.process_key_event(VK_LCONTROL, WM_KEYUP)
    assert calls == [VK_RMENU, VK_LMENU, 0x77]
    assert not hotkey.process_key_event(0x41, WM_SYSKEYDOWN)

    hotkey.close_registration()
    hotkey.deleteLater()
    window.close_for_shutdown()
    window.deleteLater()
    app.processEvents()


def test_windows_global_alt_hook_starts_and_stops_cleanly() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows global keyboard hook")
    app = _app()
    window = MiniWindow()
    hotkey = GlobalAltHotkey(window)

    assert hotkey.set_enabled(True)
    assert hotkey.is_registered
    assert hotkey._thread is not None and hotkey._thread.is_alive()
    assert not hotkey.set_enabled(False)
    assert not hotkey.is_registered
    assert hotkey._thread is None

    hotkey.deleteLater()
    window.close_for_shutdown()
    window.deleteLater()
    app.processEvents()
