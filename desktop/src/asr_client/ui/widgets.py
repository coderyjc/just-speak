from __future__ import annotations

import math
from datetime import date, timedelta

from PySide6.QtCore import (
    QEvent,
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QEnterEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QSizeGrip,
    QToolTip,
    QWidget,
)


def animate_reveal(
    widget: QWidget, duration: int = 180, start_opacity: float = 0.22
) -> None:
    """为已切换的内容做可中断的短淡入。"""
    previous = getattr(widget, "_reveal_animation", None)
    if previous is not None:
        previous.stop()
        previous.deleteLater()
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(start_opacity)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def finish() -> None:
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
        widget._reveal_animation = None  # type: ignore[attr-defined]

    animation.finished.connect(finish)
    widget._reveal_animation = animation  # type: ignore[attr-defined]
    animation.start()


class SidebarMiniButton(QPushButton):
    """Prominent bottom-of-sidebar entry for the compact floating mode."""

    def __init__(self) -> None:
        super().__init__("Mini")
        self.setObjectName("miniModeButton")
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("切换到 Mini")
        self.setAccessibleName("Mini")
        self._hover_progress = 0.0
        self._hover_animation = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_animation.setDuration(155)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def getHoverProgress(self) -> float:  # noqa: N802 - Qt property API
        return self._hover_progress

    def setHoverProgress(self, value: float) -> None:  # noqa: N802 - Qt property API
        self._hover_progress = max(0.0, min(1.0, float(value)))
        self.update()

    hoverProgress = Property(float, getHoverProgress, setHoverProgress)

    def _animate_hover(self, target: float) -> None:
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_progress)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt API
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API
        self._animate_hover(0.0)
        super().leaveEvent(event)

    @staticmethod
    def _blend(start: str, end: str, progress: float) -> QColor:
        first = QColor(start)
        second = QColor(end)
        return QColor(
            round(first.red() + (second.red() - first.red()) * progress),
            round(first.green() + (second.green() - first.green()) * progress),
            round(first.blue() + (second.blue() - first.blue()) * progress),
        )

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        progress = min(1.0, self._hover_progress + (0.12 if self.isDown() else 0.0))
        pressed_offset = 1.0 if self.isDown() else 0.0

        shadow = QRectF(self.rect()).adjusted(0.75, 3.25, -0.75, -0.75)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(8, 11, 14, round(105 - 30 * progress)))
        painter.drawRoundedRect(shadow, 15, 15)

        card = QRectF(self.rect()).adjusted(
            0.75,
            0.75 + pressed_offset,
            -0.75,
            -3.0 + pressed_offset,
        )
        painter.setBrush(self._blend("#242c33", "#303a42", progress))
        painter.setPen(
            QPen(self._blend("#3b4650", "#ff6542", progress), 1.15)
        )
        painter.drawRoundedRect(card, 14, 14)

        highlight = self._blend("#49545d", "#ff8a70", progress)
        highlight.setAlpha(round(72 + 38 * progress))
        painter.setPen(QPen(highlight, 1.0))
        painter.drawLine(
            QPointF(card.left() + 14, card.top() + 1.2),
            QPointF(card.right() - 14, card.top() + 1.2),
        )

        icon_color = self._blend("#9eabb5", "#ff7657", progress)
        icon = QRectF(
            10.5 + progress * 1.5,
            card.center().y() - 7.5,
            20,
            15,
        )
        painter.setPen(QPen(icon_color, 1.45))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(icon, 5, 5)
        painter.drawLine(
            QPointF(icon.left() + 6, icon.center().y()),
            QPointF(icon.right() - 6, icon.center().y()),
        )

        text_left = 38.0 + progress
        text_width = max(1.0, self.width() - text_left - 14.0)
        primary_font = self.font()
        primary_font.setPixelSize(15)
        primary_font.setBold(True)
        painter.setFont(primary_font)
        painter.setPen(self._blend("#f0f3f4", "#fffdf8", progress))
        painter.drawText(
            QRectF(
                text_left,
                card.top(),
                text_width,
                card.height(),
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text(),
        )

        chevron_x = self.width() - 9.0 + progress
        chevron_y = card.center().y()
        painter.setPen(
            QPen(
                self._blend("#68747e", "#fff4ef", progress),
                1.35,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawLine(
            QPointF(chevron_x - 3, chevron_y - 4),
            QPointF(chevron_x, chevron_y),
        )
        painter.drawLine(
            QPointF(chevron_x, chevron_y),
            QPointF(chevron_x - 3, chevron_y + 4),
        )


class AnimatedListWidget(QListWidget):
    """用滑动细轨表达列表选中项，保留原生键盘导航。"""

    def __init__(self) -> None:
        super().__init__()
        self._selection_rail = QFrame(self.viewport())
        self._selection_rail.setObjectName("animatedSelectionRail")
        self._selection_rail.setFixedWidth(3)
        self._selection_rail.hide()
        self._selection_animation = QPropertyAnimation(
            self._selection_rail, b"geometry", self
        )
        self._selection_animation.setDuration(190)
        self._selection_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.currentRowChanged.connect(self._animate_selection)

    def _target_geometry(self, row: int) -> QRect | None:
        item = self.item(row)
        if item is None:
            return None
        rect = self.visualItemRect(item)
        if not rect.isValid() or rect.height() <= 0:
            return None
        inset = min(6, max(2, rect.height() // 4))
        return QRect(1, rect.y() + inset, 3, max(6, rect.height() - inset * 2))

    def _animate_selection(self, row: int) -> None:
        target = self._target_geometry(row)
        if target is None:
            self._selection_animation.stop()
            self._selection_rail.hide()
            return
        if not self._selection_rail.isVisible():
            self._selection_rail.setGeometry(target)
            self._selection_rail.show()
        else:
            self._selection_animation.stop()
            self._selection_animation.setStartValue(self._selection_rail.geometry())
            self._selection_animation.setEndValue(target)
            self._selection_animation.start()
        self._selection_rail.raise_()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        super().scrollContentsBy(dx, dy)
        target = self._target_geometry(self.currentRow())
        if target is not None:
            self._selection_animation.stop()
            self._selection_rail.setGeometry(target)

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)  # type: ignore[arg-type]
        target = self._target_geometry(self.currentRow())
        if (
            target is not None
            and self._selection_animation.state()
            != self._selection_animation.State.Running
        ):
            self._selection_rail.setGeometry(target)
            self._selection_rail.show()
            self._selection_rail.raise_()


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


class RecordingDot(QWidget):
    """Fixed-geometry recording indicator with a painted breathing pulse."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("recordingDot")
        self.setFixedSize(18, 18)
        self.setAccessibleName("录音状态")
        self._active = False
        self._pulse_opacity = 1.0
        fade_out = QPropertyAnimation(self, b"pulseOpacity", self)
        fade_out.setDuration(620)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.28)
        fade_out.setEasingCurve(QEasingCurve.Type.InOutSine)
        fade_in = QPropertyAnimation(self, b"pulseOpacity", self)
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
        self._active = active
        self.setProperty("active", active)
        if active:
            if self._blink.state() != self._blink.State.Running:
                self._blink.start()
        else:
            self._blink.stop()
            self.setPulseOpacity(1.0)
        self.update()

    def getPulseOpacity(self) -> float:  # noqa: N802 - Qt property API
        return self._pulse_opacity

    def setPulseOpacity(self, value: float) -> None:  # noqa: N802 - Qt property API
        self._pulse_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    pulseOpacity = Property(float, getPulseOpacity, setPulseOpacity)

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#e44836" if self._active else "#c4c1b9")
        color.setAlphaF(self._pulse_opacity if self._active else 1.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        # Always draw around the same half-pixel center, independent of font metrics.
        painter.drawEllipse(QRectF(5.5, 5.5, 7.0, 7.0))


class ActivityHeatmap(QWidget):
    """Compact rolling activity heatmap for daily transcript characters."""

    _COLORS = ("#ebe8e0", "#ffd8ca", "#ffad94", "#f47759", "#c9472c")

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("activityHeatmap")
        self.setFixedHeight(112)
        self.setMouseTracking(True)
        self._daily: dict[date, int] = {}
        self._end_date = date.today()
        self._range_days = 365
        self._cells: list[tuple[QRectF, date, int]] = []

    @property
    def range_days(self) -> int:
        return self._range_days

    def set_range_days(self, days: int) -> None:
        normalized = 30 if int(days) <= 30 else 365
        if normalized == self._range_days:
            return
        self._range_days = normalized
        self.update()

    def set_data(self, values: dict[str, int], end_date: str = "") -> None:
        parsed: dict[date, int] = {}
        for key, value in values.items():
            try:
                parsed[date.fromisoformat(str(key))] = max(0, int(value))
            except (TypeError, ValueError):
                continue
        self._daily = parsed
        try:
            self._end_date = date.fromisoformat(end_date) if end_date else date.today()
        except ValueError:
            self._end_date = date.today()
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.font())
        start = self._end_date - timedelta(days=self._range_days - 1)
        grid_start = start - timedelta(days=start.weekday())
        weeks = ((self._end_date - grid_start).days // 7) + 1
        left = 23
        top = 18
        gap = 2
        available_width = max(1, self.width() - left - 8 - gap * (weeks - 1))
        cell_height = 9
        if self._range_days <= 30:
            # Five or six week columns expand evenly so the month view uses the
            # same visual footprint as the rolling-year grid.
            cell_width = max(5.0, available_width / weeks)
        else:
            cell_width = float(max(5, min(9, available_width // weeks)))
            cell_height = int(cell_width)
        maximum = max(
            (
                value
                for current, value in self._daily.items()
                if start <= current <= self._end_date
            ),
            default=0,
        )
        self._cells = []

        painter.setPen(QColor("#8b918f"))
        for weekday, label in ((0, "一"), (2, "三"), (4, "五")):
            painter.drawText(
                1,
                top + weekday * (cell_height + gap) + cell_height,
                label,
            )

        previous_month = -1
        for week in range(weeks):
            week_date = grid_start + timedelta(days=week * 7)
            if week_date.month != previous_month:
                painter.drawText(
                    int(left + week * (cell_width + gap)),
                    11,
                    f"{week_date.month}月",
                )
                previous_month = week_date.month
            for weekday in range(7):
                current = week_date + timedelta(days=weekday)
                in_range = start <= current <= self._end_date
                count = self._daily.get(current, 0) if in_range else 0
                x = left + week * (cell_width + gap)
                y = top + weekday * (cell_height + gap)
                rect = QRectF(x, y, cell_width, cell_height)
                if not in_range:
                    color = QColor("#f4f2ed")
                else:
                    color = QColor(self._COLORS[self._level(count, maximum)])
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(rect, 1.7, 1.7)
                self._cells.append((rect, current, count))

        legend_y = top + 7 * (cell_height + gap) + 7
        legend_x = max(left, self.width() - 91)
        painter.setPen(QColor("#8b918f"))
        painter.drawText(legend_x, legend_y + 7, "少")
        for index, color in enumerate(self._COLORS[1:]):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(
                QRectF(legend_x + 17 + index * 11, legend_y, 8, 8), 1.5, 1.5
            )
        painter.setPen(QColor("#8b918f"))
        painter.drawText(legend_x + 64, legend_y + 7, "多")

    @staticmethod
    def _level(value: int, maximum: int) -> int:
        if value <= 0 or maximum <= 0:
            return 0
        ratio = math.log1p(value) / math.log1p(maximum)
        return min(4, max(1, math.ceil(ratio * 4)))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        position = event.position()
        for rect, current, count in self._cells:
            if rect.contains(position):
                message = f"{current.isoformat()} · {count:,} 字"
                if count == 0:
                    message = f"{current.isoformat()} · 无输入"
                QToolTip.showText(event.globalPosition().toPoint(), message, self)
                return
        QToolTip.hideText()

    def leaveEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        QToolTip.hideText()
        super().leaveEvent(event)  # type: ignore[arg-type]


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
