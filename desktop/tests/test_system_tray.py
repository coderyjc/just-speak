from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

from asr_client.models import AppConfig
from asr_client.storage.config import ConfigStore
from asr_client.storage.database import Database
from asr_client.ui.main_window import MainWindow
from asr_client.ui.system_tray import SystemTrayController


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class FakeWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.raised = 0
        self.activated = 0

    def raise_(self) -> None:
        self.raised += 1

    def activateWindow(self) -> None:  # noqa: N802 - Qt API
        self.activated += 1

    def show_main_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()


def test_tray_menu_keeps_the_required_order_and_floating_window_hook() -> None:
    app = _app()
    window = FakeWindow()
    controller = SystemTrayController(app, window, QIcon(), window)

    assert [action.text() for action in controller.menu.actions()] == [
        "打开主界面",
        "显示/关闭悬浮窗",
        "关闭",
    ]
    floating_window_requests = []
    controller.floating_window_toggle_requested.connect(
        lambda: floating_window_requests.append(True)
    )
    controller.floating_window_action.trigger()
    assert floating_window_requests == [True]

    quit_requests = []
    controller.quit_requested.connect(lambda: quit_requests.append(True))
    controller.quit_action.trigger()
    assert quit_requests == [True]

    window.deleteLater()
    app.processEvents()


def test_main_window_close_hides_to_tray_until_quit_is_requested(
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

    monkeypatch.setattr(MainWindow, "refresh_microphones", lambda window: None)
    window = MainWindow(database, config_store, MemoryKeyStore())
    window.set_close_to_tray(True)
    window.show()
    app.processEvents()

    window.close()
    app.processEvents()
    assert not window.isVisible()
    assert not window._closing

    window.request_quit()
    app.processEvents()
    assert window._closing
    database.close()


def test_left_click_and_open_action_restore_the_main_window() -> None:
    app = _app()
    window = FakeWindow()
    controller = SystemTrayController(app, window, QIcon(), window)
    window.hide()

    controller._on_activated(QSystemTrayIcon.ActivationReason.Context)
    assert not window.isVisible()

    controller._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert window.isVisible()
    assert window.raised == 1
    assert window.activated == 1

    window.hide()
    controller.open_action.trigger()
    assert window.isVisible()
    assert window.raised == 2
    assert window.activated == 2

    window.close()
    window.deleteLater()
    app.processEvents()
