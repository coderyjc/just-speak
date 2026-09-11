from __future__ import annotations

import math

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizeGrip,
    QWidget,
)


def add_shadow(widget: QWidget, blur: int = 28, y: int = 8) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, y)
    shadow.setColor(QColor(22, 27, 32, 28))
    widget.setGraphicsEffect(shadow)


class Card(QFrame):
    def __init__(self, object_name: str = "card", shadow: bool = True) -> None:
        super().__init__()
        self.setObjectName(object_name)
        if shadow:
            add_shadow(self)


class WindowControlButton(QPushButton):
    """Font-independent title-bar control icon."""

    def __init__(self, kind: str, tooltip: str, name: str) -> None:
        super().__init__()
        self._kind = kind
        self.setObjectName(name)
        self.setFixedSize(42, 35)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_restore_mode(self, restore: bool) -> None:
        self._kind = "restore" if restore else "maximize"
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)  # type: ignore[arg-type]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#fffdf8" if self.underMouse() else "#aeb6bd")
        painter.setPen(QPen(color, 1.15))
        if self._kind == "minimize":
            painter.drawLine(16, 19, 26, 19)
        elif self._kind == "maximize":
            painter.drawRect(QRectF(16.5, 12.5, 9, 9))
        elif self._kind == "restore":
            painter.drawRect(QRectF(15.5, 14.5, 8, 8))
            painter.drawLine(18, 12, 26, 12)
            painter.drawLine(26, 12, 26, 20)
            painter.drawLine(24, 20, 26, 20)
        else:
            painter.drawLine(17, 13, 25, 21)
            painter.drawLine(25, 13, 17, 21)


class WindowTitleBar(QFrame):
    """Theme-aware replacement for the native Windows title bar."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self._drag_offset = None
        self.setObjectName("titleBar")
        self.setFixedHeight(36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 0, 0, 0)
        layout.setSpacing(8)
        icon = QLabel()
        icon.setObjectName("titleBarIcon")
        icon.setFixedSize(16, 16)
        pixmap = window.windowIcon().pixmap(16, 16)
        if not pixmap.isNull():
            icon.setPixmap(pixmap)
        layout.addWidget(icon)
        title = QLabel("JustSpeak")
        title.setObjectName("titleBarTitle")
        layout.addWidget(title)
        layout.addStretch(1)

        self.minimize = self._button("minimize", "最小化")
        self.maximize = self._button("maximize", "最大化")
        self.close_button = self._button("close", "关闭", "titleBarClose")
        layout.addWidget(self.minimize)
        layout.addWidget(self.maximize)
        layout.addWidget(self.close_button)

        self.minimize.clicked.connect(window.showMinimized)
        self.maximize.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close)

    @staticmethod
    def _button(
        kind: str, tooltip: str, name: str = "titleBarButton"
    ) -> WindowControlButton:
        return WindowControlButton(kind, tooltip, name)

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        QTimer.singleShot(0, self.sync_window_state)

    def sync_window_state(self) -> None:
        maximized = self._window.isMaximized()
        self.maximize.set_restore_mode(maximized)
        label = "还原" if maximized else "最大化"
        self.maximize.setToolTip(label)
        self.maximize.setAccessibleName(label)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self._window.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self._window.isMaximized()
        ):
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ThemedSizeGrip(QSizeGrip):
    """Small frameless-window resize handle using the app's muted palette."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("windowSizeGrip")
        self.setFixedSize(15, 15)

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#aaa69e"), 1.2))
        for inset in (4, 7, 10):
            painter.drawLine(
                self.width() - inset,
                self.height() - 2,
                self.width() - 2,
                self.height() - inset,
            )


class StatusChip(QLabel):
    def __init__(self, text: str = "准备就绪", tone: str = "idle") -> None:
        super().__init__(text)
        self.setObjectName("statusChip")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


class RecordingDot(QLabel):
    """Small recording indicator with a restrained breathing blink."""

    def __init__(self) -> None:
        super().__init__("●")
        self.setObjectName("recordingDot")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(18, 18)
        self.setAccessibleName("录音状态")
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        fade_out = QPropertyAnimation(self._effect, b"opacity", self)
        fade_out.setDuration(620)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.28)
        fade_out.setEasingCurve(QEasingCurve.Type.InOutSine)
        fade_in = QPropertyAnimation(self._effect, b"opacity", self)
        fade_in.setDuration(620)
        fade_in.setStartValue(0.28)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._blink = QSequentialAnimationGroup(self)
        self._blink.addAnimation(fade_out)
        self._blink.addAnimation(fade_in)
        self._blink.setLoopCount(-1)
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        if active:
            if self._blink.state() != self._blink.State.Running:
                self._blink.start()
        else:
            self._blink.stop()
            self._effect.setOpacity(1.0)


class AnimatedProgressBar(QProgressBar):
    def __init__(self) -> None:
        super().__init__()
        self.setTextVisible(False)
        self._animation = QPropertyAnimation(self, b"animatedValue", self)
        self._animation.setDuration(260)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_value(self) -> int:
        return super().value()

    def _set_value(self, value: int) -> None:
        super().setValue(int(value))

    animatedValue = Property(int, _get_value, _set_value)

    def setValue(self, value: int) -> None:  # noqa: N802 - Qt API
        if not self.isVisible() or abs(value - super().value()) <= 1:
            super().setValue(value)
            return
        self._animation.stop()
        self._animation.setStartValue(super().value())
        self._animation.setEndValue(value)
        self._animation.start()


class SignalOrb(QWidget):
    """Animated recorder visualization driven by the microphone level."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(112, 112)
        self._level = 0.0
        self._display_level = 0.0
        self._phase = 0.0
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self._timer.start()
        else:
            self._timer.stop()
            self._level = 0.0
            self._display_level = 0.0
            self.update()

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level)))

    def _tick(self) -> None:
        self._phase = (self._phase + 0.07) % (math.pi * 2)
        self._display_level += (self._level - self._display_level) * 0.22
        self.update()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.rect().center()
        pulse = (math.sin(self._phase) + 1) / 2 if self._active else 0.15
        outer = 40 + 5 * pulse + 6 * self._display_level
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(240, 91, 58, 24 if self._active else 12))
        painter.drawEllipse(center, int(outer), int(outer))
        painter.setBrush(QColor("#2b3239"))
        painter.drawEllipse(center, 36, 36)
        painter.setPen(QPen(QColor("#4a535c"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, 36, 36)

        color = QColor("#ff6542") if self._active else QColor("#77818a")
        pen = QPen(color, 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        amplitude = 7 + self._display_level * 22
        width = 44
        points = []
        for x in range(-width // 2, width // 2 + 1, 3):
            envelope = math.cos((x / width) * math.pi) ** 2
            y = math.sin(x * 0.27 + self._phase * 2) * amplitude * envelope
            points.append((center.x() + x, center.y() + y))
        for first, second in zip(points, points[1:]):
            painter.drawLine(int(first[0]), int(first[1]), int(second[0]), int(second[1]))


class StepBadge(QLabel):
    def __init__(self, number: str) -> None:
        super().__init__(number)
        self.setFixedSize(28, 28)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background:#20262c;color:#fffdf8;border-radius:14px;"
            "font-family:Bahnschrift;font-size:11px;font-weight:700;"
        )
