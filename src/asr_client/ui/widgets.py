from __future__ import annotations

import math

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QProgressBar,
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
