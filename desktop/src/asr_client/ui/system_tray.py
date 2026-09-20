from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget


class TrayWindow(Protocol):
    def show_main_window(self) -> None:
        ...


class SystemTrayController(QObject):
    """Owns the Windows notification-area icon and its public actions."""

    floating_window_toggle_requested = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        app: QApplication,
        window: TrayWindow,
        icon: QIcon,
        menu_parent: QWidget | None = None,
    ) -> None:
        super().__init__(app)
        self.window = window
        self.tray_icon = QSystemTrayIcon(icon, app)
        self.tray_icon.setToolTip("JustSpeak")

        self.menu = QMenu(menu_parent)
        self.open_action = self.menu.addAction("打开主界面")
        self.floating_window_action = self.menu.addAction("显示/关闭悬浮窗")
        self.quit_action = self.menu.addAction("关闭")

        # Keep the command and signal stable for the planned floating window.
        self.open_action.triggered.connect(self.show_main_window)
        self.floating_window_action.triggered.connect(
            self.floating_window_toggle_requested.emit
        )
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self.tray_icon.activated.connect(self._on_activated)
        self.tray_icon.setContextMenu(self.menu)

    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self.tray_icon.show()

    def hide(self) -> None:
        self.tray_icon.hide()

    def show_main_window(self) -> None:
        self.window.show_main_window()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_main_window()
