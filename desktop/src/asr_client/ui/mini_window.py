from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes

from PySide6.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QCursor,
    QFocusEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from asr_client.ui.widgets import RecordingDot, animate_reveal


logger = logging.getLogger(__name__)
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LWIN = 0x5B
VK_RWIN = 0x5C

MODIFIER_KEYS = {
    VK_LSHIFT: "Shift",
    VK_RSHIFT: "Shift",
    VK_LCONTROL: "Ctrl",
    VK_RCONTROL: "Ctrl",
    VK_LMENU: "Alt",
    VK_RMENU: "Alt",
    VK_LWIN: "Win",
    VK_RWIN: "Win",
}
SIDE_KEY_NAMES = {
    VK_LSHIFT: "LeftShift",
    VK_RSHIFT: "RightShift",
    VK_LCONTROL: "LeftCtrl",
    VK_RCONTROL: "RightCtrl",
    VK_LMENU: "LeftAlt",
    VK_RMENU: "RightAlt",
    VK_LWIN: "LeftWin",
    VK_RWIN: "RightWin",
}
SPECIAL_KEY_NAMES = {
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x1B: "Esc",
    0x20: "Space",
    0x21: "PageUp",
    0x22: "PageDown",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2D: "Insert",
    0x2E: "Delete",
}


def key_name_from_virtual_key(virtual_key: int) -> str:
    if virtual_key in SIDE_KEY_NAMES:
        return SIDE_KEY_NAMES[virtual_key]
    if virtual_key in SPECIAL_KEY_NAMES:
        return SPECIAL_KEY_NAMES[virtual_key]
    if 0x30 <= virtual_key <= 0x39 or 0x41 <= virtual_key <= 0x5A:
        return chr(virtual_key)
    if 0x70 <= virtual_key <= 0x87:
        return f"F{virtual_key - 0x6F}"
    return f"VK_{virtual_key:02X}"


def virtual_key_from_name(name: str) -> int | None:
    normalized = name.strip()
    reverse_sides = {value.lower(): key for key, value in SIDE_KEY_NAMES.items()}
    reverse_special = {
        value.lower(): key for key, value in SPECIAL_KEY_NAMES.items()
    }
    lowered = normalized.lower()
    if lowered in reverse_sides:
        return reverse_sides[lowered]
    if lowered in reverse_special:
        return reverse_special[lowered]
    if len(normalized) == 1 and normalized.upper().isalnum():
        return ord(normalized.upper())
    if lowered.startswith("f") and lowered[1:].isdigit():
        number = int(lowered[1:])
        if 1 <= number <= 24:
            return 0x6F + number
    if lowered.startswith("vk_"):
        try:
            return int(lowered[3:], 16)
        except ValueError:
            return None
    return None


def parse_mini_shortcut(value: str) -> tuple[frozenset[str], int]:
    tokens = [token.strip() for token in str(value).split("+") if token.strip()]
    if not tokens:
        return frozenset(), VK_RMENU
    modifiers = frozenset(
        token.title() if token.lower() != "ctrl" else "Ctrl"
        for token in tokens[:-1]
        if token.lower() in {"ctrl", "shift", "alt", "win"}
    )
    key = virtual_key_from_name(tokens[-1])
    if key is None:
        return frozenset(), VK_RMENU
    return modifiers, key


def normalize_mini_shortcut(value: str) -> str:
    modifiers, key = parse_mini_shortcut(value)
    ordered = [name for name in ("Ctrl", "Shift", "Alt", "Win") if name in modifiers]
    return "+".join([*ordered, key_name_from_virtual_key(key)])


def mini_shortcut_display(value: str) -> str:
    labels = {
        "RightAlt": "右 Alt",
        "LeftAlt": "左 Alt",
        "RightCtrl": "右 Ctrl",
        "LeftCtrl": "左 Ctrl",
        "RightShift": "右 Shift",
        "LeftShift": "左 Shift",
        "RightWin": "右 Win",
        "LeftWin": "左 Win",
    }
    return " + ".join(
        labels.get(token, token) for token in normalize_mini_shortcut(value).split("+")
    )


class KbdLlHookStruct(ctypes.Structure):
    _fields_ = (
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class MiniHomeButton(QPushButton):
    """Draw a crisp home action without relying on an icon font."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("miniHomeButton")
        self.setFixedSize(38, 38)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("返回主页")
        self.setAccessibleName("返回主页")

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() or self.isDown():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#303941" if self.isDown() else "#262e35"))
            painter.drawRoundedRect(self.rect(), 10, 10)
        color = QColor("#ff7657" if self.underMouse() else "#aeb7bf")
        painter.setPen(
            QPen(
                color,
                1.7,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(12.5, 18.5), QPointF(19.0, 12.8))
        painter.drawLine(QPointF(19.0, 12.8), QPointF(25.5, 18.5))
        painter.drawRoundedRect(QRectF(14.0, 17.5, 10.0, 8.0), 1.8, 1.8)
        painter.drawLine(QPointF(18.9, 21.0), QPointF(18.9, 25.2))


class MiniShortcutEdit(QPushButton):
    shortcutChanged = Signal(str)

    def __init__(self, shortcut: str = "RightAlt") -> None:
        super().__init__()
        self.setObjectName("miniShortcutEdit")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("点击后按下新的 Mini 全局快捷键")
        self.setAccessibleName("Mini 全局快捷键")
        self._shortcut = normalize_mini_shortcut(shortcut)
        self._capturing = False
        self._capture_modifiers: set[int] = set()
        self.clicked.connect(self.begin_capture)
        self._sync_text()

    def shortcut(self) -> str:
        return self._shortcut

    def set_shortcut(self, shortcut: str, emit: bool = False) -> None:
        normalized = normalize_mini_shortcut(shortcut)
        changed = normalized != self._shortcut
        self._shortcut = normalized
        self._sync_text()
        if emit and changed:
            self.shortcutChanged.emit(normalized)

    def begin_capture(self) -> None:
        self._capturing = True
        self._capture_modifiers.clear()
        self.setProperty("capturing", True)
        self.setText("请按新的快捷键…")
        self.style().unpolish(self)
        self.style().polish(self)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.grabKeyboard()

    def _sync_text(self) -> None:
        if not self._capturing:
            self.setText(mini_shortcut_display(self._shortcut))

    def _finish_capture(self, shortcut: str | None = None) -> None:
        self.releaseKeyboard()
        self._capturing = False
        self._capture_modifiers.clear()
        self.setProperty("capturing", False)
        self.style().unpolish(self)
        self.style().polish(self)
        if shortcut:
            self.set_shortcut(shortcut, emit=True)
        else:
            self._sync_text()

    @staticmethod
    def _event_virtual_key(event: QKeyEvent) -> int:
        native = int(event.nativeVirtualKey())
        if native:
            return native
        key = int(event.key())
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return 0x41 + key - int(Qt.Key.Key_A)
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return 0x30 + key - int(Qt.Key.Key_0)
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
            return 0x70 + key - int(Qt.Key.Key_F1)
        fallback = {
            int(Qt.Key.Key_Space): 0x20,
            int(Qt.Key.Key_Tab): 0x09,
            int(Qt.Key.Key_Return): 0x0D,
            int(Qt.Key.Key_Enter): 0x0D,
            int(Qt.Key.Key_Backspace): 0x08,
            int(Qt.Key.Key_Delete): 0x2E,
        }
        return fallback.get(key, 0)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if not self._capturing:
            super().keyPressEvent(event)
            return
        if event.isAutoRepeat():
            event.accept()
            return
        virtual_key = self._event_virtual_key(event)
        if virtual_key in MODIFIER_KEYS:
            self._capture_modifiers.add(virtual_key)
            event.accept()
            return
        if virtual_key:
            modifiers = {
                MODIFIER_KEYS[key]
                for key in self._capture_modifiers
                if key in MODIFIER_KEYS
            }
            ordered = [
                name for name in ("Ctrl", "Shift", "Alt", "Win")
                if name in modifiers
            ]
            self._finish_capture(
                "+".join([*ordered, key_name_from_virtual_key(virtual_key)])
            )
            event.accept()
            return
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if not self._capturing:
            super().keyReleaseEvent(event)
            return
        virtual_key = self._event_virtual_key(event)
        if virtual_key in self._capture_modifiers:
            self._finish_capture(key_name_from_virtual_key(virtual_key))
        event.accept()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt API
        if self._capturing:
            self._finish_capture()
        super().focusOutEvent(event)


class GlobalAltHotkey(QObject):
    """Listen for the configured global key-down while mini mode is active."""

    activated = Signal(int)

    def __init__(
        self,
        window: "MiniWindow",
        parent: QObject | None = None,
        shortcut: str = "RightAlt",
    ) -> None:
        super().__init__(parent)
        self.window = window
        self._enabled = False
        self._registered = False
        self._hook_handle = 0
        self._thread_id = 0
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._pressed_keys: set[int] = set()
        self._hook_callback: object | None = None
        self._shortcut_modifiers: frozenset[str] = frozenset()
        self._shortcut_key = VK_RMENU
        self.set_shortcut(shortcut)

    @property
    def is_registered(self) -> bool:
        return self._registered

    def set_shortcut(self, shortcut: str) -> None:
        self._shortcut_modifiers, self._shortcut_key = parse_mini_shortcut(shortcut)
        self._pressed_keys.clear()

    def _active_modifiers(self, trigger_key: int) -> frozenset[str]:
        return frozenset(
            modifier
            for key, modifier in MODIFIER_KEYS.items()
            if key != trigger_key and key in self._pressed_keys
        )

    def set_enabled(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        self._enabled = enabled
        if sys.platform != "win32":
            return False
        if enabled and self._thread is None:
            self._ready = threading.Event()
            self._thread = threading.Thread(
                target=self._run_hook,
                name="mini-alt-hotkey",
                daemon=True,
            )
            self._thread.start()
            self._ready.wait(timeout=1.5)
            if not self._registered:
                logger.warning("无法启动 Mini 全局快捷键")
        elif not enabled and self._thread is not None:
            self._stop_hook()
        return self._registered

    def process_key_event(self, virtual_key: int, message: int) -> bool:
        if message in {WM_KEYDOWN, WM_SYSKEYDOWN}:
            if virtual_key in self._pressed_keys:
                return virtual_key == self._shortcut_key
            self._pressed_keys.add(virtual_key)
            matches = (
                virtual_key == self._shortcut_key
                and (
                    not self._shortcut_modifiers
                    and virtual_key in SIDE_KEY_NAMES
                    or self._active_modifiers(virtual_key)
                    == self._shortcut_modifiers
                )
            )
            if self._enabled and matches:
                self.activated.emit(virtual_key)
            return matches
        if message in {WM_KEYUP, WM_SYSKEYUP}:
            self._pressed_keys.discard(virtual_key)
            return virtual_key == self._shortcut_key
        return False

    def _run_hook(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        result_type = ctypes.c_ssize_t
        hook_proc_type = ctypes.WINFUNCTYPE(
            result_type, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int,
            hook_proc_type,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        )
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = (
            wintypes.HHOOK,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        user32.CallNextHookEx.restype = result_type
        user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = (
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        )
        user32.GetMessageW.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = (
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        )
        user32.PeekMessageW.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def keyboard_proc(
            code: int, word_param: int, long_param: int
        ) -> int:
            if code == HC_ACTION:
                data = ctypes.cast(
                    long_param, ctypes.POINTER(KbdLlHookStruct)
                ).contents
                self.process_key_event(int(data.vkCode), int(word_param))
            return int(
                user32.CallNextHookEx(
                    self._hook_handle, code, word_param, long_param
                )
            )

        self._hook_callback = hook_proc_type(keyboard_proc)
        self._thread_id = int(kernel32.GetCurrentThreadId())
        module = kernel32.GetModuleHandleW(None)
        self._hook_handle = int(
            user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._hook_callback, module, 0
            )
            or 0
        )
        self._registered = bool(self._hook_handle)

        message = wintypes.MSG()
        user32.PeekMessageW(
            ctypes.byref(message), None, 0, 0, PM_NOREMOVE
        )
        self._ready.set()
        if not self._registered:
            return
        try:
            while user32.GetMessageW(
                ctypes.byref(message), None, 0, 0
            ) > 0:
                pass
        finally:
            user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = 0
            self._registered = False
            self._pressed_keys.clear()

    def _stop_hook(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if self._thread_id:
            user32 = ctypes.windll.user32
            user32.PostThreadMessageW.argtypes = (
                wintypes.DWORD,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.PostThreadMessageW.restype = wintypes.BOOL
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread.join(timeout=2)
        self._thread = None
        self._thread_id = 0
        self._pressed_keys.clear()

    def close_registration(self) -> None:
        self.set_enabled(False)


class MiniWindow(QWidget):
    main_window_requested = Signal()
    home_requested = Signal()
    pause_toggle_requested = Signal()
    cancel_requested = Signal()

    STATUS = {
        "ready": ("按右 Alt 开始录音", False, "idle"),
        "context": ("读取屏幕", True, "live"),
        "recording": ("正在录音", True, "live"),
        "paused": ("录音已暂停", False, "warning"),
        "clarity": ("文本清洗", True, "processing"),
        "polish": ("定向修复", True, "processing"),
        "resetting": ("正在重置", True, "warning"),
        "completed": ("完成", False, "success"),
        "failed": ("流程未完成", False, "danger"),
    }

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("miniWindow")
        self.setWindowTitle("JustSpeak Mini")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(308, 62)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._drag_offset: QPoint | None = None
        self._positioned = False
        self._allow_close = False
        self._shortcut = "RightAlt"
        self._status = "ready"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)
        shell = QFrame()
        shell.setObjectName("miniShell")
        row = QHBoxLayout(shell)
        row.setContentsMargins(13, 6, 8, 6)
        row.setSpacing(8)

        self.indicator = RecordingDot()
        self.indicator.setToolTip("录音流程状态")
        row.addWidget(self.indicator)
        row.setAlignment(self.indicator, Qt.AlignmentFlag.AlignVCenter)
        self.status_label = QLabel()
        self.status_label.setObjectName("miniStatus")
        self.status_label.setAccessibleName("Mini 状态")
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        row.addWidget(self.status_label, 1)
        self.home_button = MiniHomeButton()
        row.addWidget(self.home_button)
        row.setAlignment(self.home_button, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(shell)

        shadow = QGraphicsDropShadowEffect(shell)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 105))
        shell.setGraphicsEffect(shadow)

        self.home_button.clicked.connect(self.home_requested.emit)
        self.set_status("ready")

    @property
    def status(self) -> str:
        return self._status

    def set_shortcut(self, shortcut: str) -> None:
        self._shortcut = normalize_mini_shortcut(shortcut)
        if self._status == "ready":
            self.status_label.setText(
                f"按 {mini_shortcut_display(self._shortcut)} 开始录音"
            )

    def set_status(self, status: str) -> None:
        if status not in self.STATUS:
            return
        text, active, tone = self.STATUS[status]
        if status == "ready":
            text = f"按 {mini_shortcut_display(self._shortcut)} 开始录音"
        changed = status != self._status or self.status_label.text() != text
        self._status = status
        self.status_label.setText(text)
        self.status_label.setProperty("tone", tone)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.indicator.set_active(active)
        if changed and self.isVisible():
            animate_reveal(self.status_label, 130, 0.38)

    def show_for_mode(self) -> None:
        if not self._positioned:
            screen = self.screen()
            if screen is None:
                screen = self.windowHandle().screen() if self.windowHandle() else None
            if screen is None:
                from PySide6.QtGui import QGuiApplication

                screen = QGuiApplication.screenAt(QCursor.pos())
                screen = screen or QGuiApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(
                    area.right() - self.width() - 22,
                    area.bottom() - self.height() - 22,
                )
            self._positioned = True
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def close_for_shutdown(self) -> None:
        self._allow_close = True
        self.close()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.isAutoRepeat():
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            self.pause_toggle_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        self.main_window_requested.emit()
