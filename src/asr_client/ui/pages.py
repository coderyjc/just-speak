from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QWheelEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from asr_client.models import (
    MAX_TRANSCRIPTION_PROMPT_LENGTH,
    AppConfig,
    AudioTrack,
    DEFAULT_POLISH_PROMPT,
)
from asr_client.ui.widgets import AnimatedProgressBar, Card, RecordingDot


def _label(text: str, name: str) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    return widget


def _icon_button(
    icon: QStyle.StandardPixmap, tooltip: str, object_name: str = "iconButton"
) -> QPushButton:
    button = QPushButton()
    button.setObjectName(object_name)
    button.setIcon(button.style().standardIcon(icon))
    button.setIconSize(QSize(16, 16))
    button.setFixedSize(36, 36)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    return button


class NoWheelComboBox(QComboBox):
    """Keep page scrolling from changing a selection accidentally."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt API
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    """Keep page scrolling from changing a numeric setting accidentally."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt API
        event.ignore()


def _settings_field(label: str, control: QWidget) -> QWidget:
    field = QWidget()
    field.setObjectName("settingsField")
    layout = QVBoxLayout(field)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    layout.addWidget(_label(label, "fieldLabel"))
    layout.addWidget(control)
    return field


class ElidingLabel(QLabel):
    def __init__(
        self,
        text: str = "",
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
    ) -> None:
        super().__init__()
        self._full_text = ""
        self._mode = mode
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._refresh_text()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._refresh_text()

    def _refresh_text(self) -> None:
        visible = self.fontMetrics().elidedText(
            self._full_text, self._mode, max(0, self.width() - 2)
        )
        QLabel.setText(self, visible)


class Page(QWidget):
    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 9)
        outer.setSpacing(6)
        self.layout = QVBoxLayout()
        self.layout.setSpacing(8)
        outer.addLayout(self.layout, 1)
        self.page_status = QLabel("准备就绪")
        self.page_status.setObjectName("pageFooterStatus")
        self.page_status.setProperty("tone", "idle")
        self.page_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        outer.addWidget(self.page_status)

    def set_page_status(self, text: str, tone: str = "idle") -> None:
        self.page_status.setText(text)
        self.page_status.setProperty("tone", tone)
        self.page_status.style().unpolish(self.page_status)
        self.page_status.style().polish(self.page_status)


PIPELINE_STAGES = (
    ("asr", "01  实时录音"),
    ("clarity", "02  文本清晰"),
    ("polish", "03  定向修复"),
)


class PipelineNav(QWidget):
    stage_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("pipelineNav")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for index, (stage, label) in enumerate(PIPELINE_STAGES):
            button = QPushButton(label)
            button.setObjectName("pipelineStep")
            button.setCheckable(True)
            button.setProperty("state", "pending")
            button.clicked.connect(
                lambda checked=False, value=stage: self.stage_selected.emit(value)
            )
            self.group.addButton(button)
            self.buttons[stage] = button
            layout.addWidget(button, 1)
            if index < len(PIPELINE_STAGES) - 1:
                arrow = _label("›", "pipelineArrow")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(arrow)
        self.select("asr")

    def set_stage(self, stage: str, state: str, available: bool = True) -> None:
        button = self.buttons[stage]
        button.setProperty("state", state)
        button.setEnabled(available)
        button.style().unpolish(button)
        button.style().polish(button)

    def select(self, stage: str) -> None:
        button = self.buttons.get(stage)
        if button and button.isEnabled():
            button.setChecked(True)


class RealtimePage(Page):
    start_requested = Signal()
    stop_requested = Signal()
    export_requested = Signal()
    copy_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.status = self.page_status
        controls = Card("controlCard", shadow=False)
        control_row = QHBoxLayout(controls)
        control_row.setContentsMargins(10, 8, 10, 8)
        control_row.setSpacing(7)
        self.orb = RecordingDot()
        control_row.addWidget(self.orb)
        self.duration = _label("00:00.0", "recordingDuration")
        control_row.addWidget(self.duration)
        self.level = AnimatedProgressBar()
        self.level.setRange(0, 100)
        self.level.setFixedWidth(54)
        control_row.addWidget(self.level)
        self.device = NoWheelComboBox()
        self.device.setMinimumWidth(180)
        self.device.setToolTip("选择要使用的麦克风")
        control_row.addWidget(self.device, 1)
        self.refresh = _icon_button(
            QStyle.StandardPixmap.SP_BrowserReload, "刷新输入设备"
        )
        self.start = _icon_button(
            QStyle.StandardPixmap.SP_MediaPlay, "开始录音", "primaryIconButton"
        )
        self.stop = _icon_button(
            QStyle.StandardPixmap.SP_MediaStop, "停止并完成", "dangerIconButton"
        )
        self.stop.setEnabled(False)
        control_row.addWidget(self.refresh)
        control_row.addWidget(self.start)
        control_row.addWidget(self.stop)
        self.layout.addWidget(controls)

        transcript_card = Card("transcriptCard")
        transcript_layout = QVBoxLayout(transcript_card)
        transcript_layout.setContentsMargins(12, 10, 12, 9)
        transcript_layout.setSpacing(7)
        head = QHBoxLayout()
        head.addWidget(_label("实时转写稿", "cardTitle"))
        head.addStretch(1)
        transcript_layout.addLayout(head)
        self.pipeline = PipelineNav()
        transcript_layout.addWidget(self.pipeline)
        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText("开始说话后，识别文字会在这里逐句出现…")
        self.transcript.setReadOnly(True)
        transcript_layout.addWidget(self.transcript, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.copy = QPushButton("复制全文")
        self.copy.setObjectName("compactButton")
        self.save_edit = QPushButton("保存修改")
        self.save_edit.setObjectName("compactButton")
        self.save_edit.setVisible(False)
        self.export = QPushButton("导出 TXT")
        self.export.setObjectName("compactButton")
        actions.addWidget(self.copy)
        actions.addWidget(self.save_edit)
        actions.addWidget(self.export)
        transcript_layout.addLayout(actions)
        self.layout.addWidget(transcript_card, 1)
        self.start.clicked.connect(self.start_requested)
        self.stop.clicked.connect(self.stop_requested)
        self.export.clicked.connect(self.export_requested)
        self.copy.clicked.connect(self.copy_requested)
        self.pipeline.stage_selected.connect(self._select_stage)
        self._stage_texts: dict[str, str] = {stage: "" for stage, _ in PIPELINE_STAGES}
        self._stage_states: dict[str, str] = {
            stage: "pending" for stage, _ in PIPELINE_STAGES
        }
        self._selected_stage = "asr"
        self._final_stage = "asr"
        self._recording_running = False
        self.reset_pipeline()

    def set_running(self, running: bool) -> None:
        self._recording_running = running
        self.start.setEnabled(not running)
        self.stop.setEnabled(running)
        self.device.setEnabled(not running)
        self.refresh.setEnabled(not running)
        self._update_editor_access()
        self.orb.set_active(running)
        if running:
            self.set_page_status("正在录音", "live")
        else:
            has_text = bool(self.transcript.toPlainText())
            self.set_page_status(
                "录音已停止" if has_text else "准备就绪",
                "success" if has_text else "idle",
            )

    def set_level(self, level: float) -> None:
        self.level.setValue(int(level * 100))

    def reset_pipeline(self) -> None:
        self._stage_texts = {stage: "" for stage, _ in PIPELINE_STAGES}
        self._stage_states = {stage: "pending" for stage, _ in PIPELINE_STAGES}
        self._selected_stage = "asr"
        self._final_stage = "asr"
        for stage, _ in PIPELINE_STAGES:
            self.pipeline.set_stage(stage, "pending", stage == "asr")
        self.pipeline.set_stage("asr", "running", True)
        self.pipeline.select("asr")
        self.transcript.clear()

    def set_stage_text(self, stage: str, text: str) -> None:
        self._stage_texts[stage] = text
        if stage == self._selected_stage:
            self.transcript.setPlainText(text)

    def set_stage_state(self, stage: str, state: str, text: str = "") -> None:
        self._stage_states[stage] = state
        self.pipeline.set_stage(stage, state, state not in {"pending", "skipped"})
        if text:
            self.set_stage_text(stage, text)
        if state == "running":
            self._select_stage(stage)
        self._update_editor_access()

    def finish_pipeline(self) -> None:
        available = [
            stage
            for stage, _ in PIPELINE_STAGES
            if self._stage_states.get(stage) in {"completed", "failed"}
            and self._stage_texts.get(stage)
        ]
        self._final_stage = available[-1] if available else "asr"
        self._select_stage(self._final_stage)
        self._update_editor_access()

    def _select_stage(self, stage: str) -> None:
        if stage not in self._stage_texts:
            return
        if (
            self._selected_stage == self._final_stage
            and not self.transcript.isReadOnly()
        ):
            self._stage_texts[self._selected_stage] = self.transcript.toPlainText()
        self._selected_stage = stage
        self.pipeline.select(stage)
        self.transcript.setPlainText(self._stage_texts.get(stage, ""))
        self._update_editor_access()

    def _update_editor_access(self) -> None:
        editable = (
            not self._recording_running
            and self._selected_stage == self._final_stage
            and bool(self._stage_texts.get(self._final_stage))
        )
        self.transcript.setReadOnly(not editable)
        self.save_edit.setVisible(editable)


class FileDropEdit(QLineEdit):
    file_dropped = Signal(str)
    drag_state_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and len(event.mimeData().urls()) == 1:
            self.drag_state_changed.emit(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event: object) -> None:
        self.drag_state_changed.emit(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.drag_state_changed.emit(False)
        path = event.mimeData().urls()[0].toLocalFile()
        if path:
            self.setText(path)
            self.file_dropped.emit(path)
            event.acceptProposedAction()


class FilePage(Page):
    inspect_requested = Signal(str)
    start_requested = Signal(str, int)
    pause_requested = Signal()
    resume_requested = Signal()
    cancel_requested = Signal()
    extract_requested = Signal(str, int)
    export_requested = Signal()
    save_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.status = self.page_status
        self.drop_zone = Card("fileToolbar", shadow=False)
        self.drop_zone.setProperty("dragActive", False)
        toolbar = QHBoxLayout(self.drop_zone)
        toolbar.setContentsMargins(10, 8, 10, 8)
        toolbar.setSpacing(7)
        self.source = FileDropEdit()
        self.source.setPlaceholderText("拖入文件或浏览本机文件")
        browse = QPushButton("浏览文件")
        browse.setObjectName("compactButton")
        toolbar.addWidget(self.source, 1)
        toolbar.addWidget(browse)
        self.track = NoWheelComboBox()
        self.track.setFixedWidth(150)
        self.track.setPlaceholderText("选择音轨")
        self.track.setEnabled(False)
        toolbar.addWidget(self.track)
        self.only_extract = QPushButton("提取 WAV")
        self.only_extract.setObjectName("compactButton")
        self.only_extract.setEnabled(False)
        toolbar.addWidget(self.only_extract)
        self.start = _icon_button(
            QStyle.StandardPixmap.SP_MediaPlay,
            "开始转写",
            "primaryIconButton",
        )
        self.start.setEnabled(False)
        toolbar.addWidget(self.start)
        self.layout.addWidget(self.drop_zone)

        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(7)
        self.progress = AnimatedProgressBar()
        self.progress.setRange(0, 100)
        progress_layout.addWidget(self.progress, 1)
        self.pause = QPushButton("暂停")
        self.pause.setObjectName("compactButton")
        self.resume = QPushButton("继续")
        self.resume.setObjectName("compactButton")
        self.cancel = QPushButton("取消")
        self.cancel.setObjectName("compactDangerButton")
        self.pause.setEnabled(False)
        self.resume.setEnabled(False)
        self.cancel.setEnabled(False)
        progress_layout.addWidget(self.pause)
        progress_layout.addWidget(self.resume)
        progress_layout.addWidget(self.cancel)
        self.layout.addLayout(progress_layout)

        transcript_card = Card("transcriptCard")
        transcript_layout = QVBoxLayout(transcript_card)
        transcript_layout.setContentsMargins(12, 10, 12, 9)
        transcript_layout.setSpacing(7)
        head = QHBoxLayout()
        head.addWidget(_label("转写结果", "cardTitle"))
        head.addStretch(1)
        transcript_layout.addLayout(head)
        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText("选择文件并开始后，片段结果会持续汇入这里…")
        self.transcript.setReadOnly(True)
        transcript_layout.addWidget(self.transcript, 1)
        footer = QHBoxLayout()
        self.copy = QPushButton("复制全文")
        self.copy.setObjectName("compactButton")
        self.save_edit = QPushButton("保存文字修改")
        self.save_edit.setObjectName("compactButton")
        self.save_edit.setVisible(False)
        self.export = QPushButton("导出 TXT")
        self.export.setObjectName("compactButton")
        footer.addStretch(1)
        footer.addWidget(self.copy)
        footer.addWidget(self.save_edit)
        footer.addWidget(self.export)
        transcript_layout.addLayout(footer)
        self.layout.addWidget(transcript_card, 1)

        def choose() -> None:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "选择音频或视频",
                "",
                "媒体文件 (*.mp3 *.wav *.m4a *.flac *.mp4 *.mkv *.mov *.webm);;所有文件 (*)",
            )
            if path:
                self.source.setText(path)
                self.inspect_requested.emit(path)

        browse.clicked.connect(choose)
        self.source.file_dropped.connect(self.inspect_requested)
        self.source.drag_state_changed.connect(self._set_drag_active)
        self.source.editingFinished.connect(
            lambda: self.inspect_requested.emit(self.source.text().strip())
            if self.source.text().strip()
            else None
        )
        self.start.clicked.connect(
            lambda: self.start_requested.emit(self.source.text(), int(self.track.currentData()))
        )
        self.only_extract.clicked.connect(
            lambda: self.extract_requested.emit(self.source.text(), int(self.track.currentData()))
        )
        self.pause.clicked.connect(self.pause_requested)
        self.resume.clicked.connect(self.resume_requested)
        self.cancel.clicked.connect(self.cancel_requested)
        self.export.clicked.connect(self.export_requested)
        self.save_edit.clicked.connect(self.save_requested)
        self.copy.clicked.connect(lambda: self.transcript.selectAll())
        self.copy.clicked.connect(self.transcript.copy)

    def _set_drag_active(self, active: bool) -> None:
        self.drop_zone.setProperty("dragActive", active)
        self.drop_zone.style().unpolish(self.drop_zone)
        self.drop_zone.style().polish(self.drop_zone)

    def set_tracks(self, tracks: list[AudioTrack], duration: float) -> None:
        self.track.clear()
        for track in tracks:
            self.track.addItem(track.label, track.index)
        self.track.setEnabled(bool(tracks))
        self.start.setEnabled(bool(tracks))
        self.only_extract.setEnabled(bool(tracks))
        self.set_page_status(
            f"已就绪 · {duration / 60:.1f} 分钟 · {len(tracks)} 条音轨",
            "success",
        )

    def set_running(self, running: bool) -> None:
        self.start.setEnabled(not running and self.track.count() > 0)
        self.only_extract.setEnabled(not running and self.track.count() > 0)
        self.pause.setEnabled(running)
        self.resume.setEnabled(False)
        self.cancel.setEnabled(running)
        self.source.setEnabled(not running)
        self.track.setEnabled(not running)
        self.transcript.setReadOnly(running)
        self.save_edit.setVisible(not running and bool(self.transcript.toPlainText()))
        self.set_page_status("处理中" if running else "准备就绪", "live" if running else "idle")


class HistoryPage(Page):
    selection_changed = Signal(str)
    retry_requested = Signal(str)
    save_requested = Signal(str, str)
    export_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        workspace = Card("card")
        body = QHBoxLayout(workspace)
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(0)
        self.sessions = QListWidget()
        self.sessions.setObjectName("historyList")
        self.sessions.setFixedWidth(190)
        self.sessions.setWordWrap(False)
        self.sessions.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.sessions.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        left.addWidget(self.sessions, 1)
        body.addLayout(left)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet("color:#e2ded5;")
        body.addWidget(divider)
        right = QVBoxLayout()
        right.setSpacing(6)
        detail_head = QHBoxLayout()
        detail_head.setSpacing(6)
        self.detail_title = ElidingLabel("选择一项文稿")
        self.detail_title.setObjectName("documentTitle")
        self.detail_title.setWordWrap(False)
        self.detail_title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        detail_head.addWidget(self.detail_title, 1)
        self.retry = QPushButton("继续未完成")
        self.retry.setObjectName("compactButton")
        self.retry.setEnabled(False)
        detail_head.addWidget(self.retry)
        self.delete = QPushButton("删除")
        self.delete.setObjectName("compactDangerButton")
        self.delete.setEnabled(False)
        detail_head.addWidget(self.delete)
        right.addLayout(detail_head)
        self.pipeline = PipelineNav()
        right.addWidget(self.pipeline)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("从左侧选择一项历史任务")
        right.addWidget(self.text, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.save = QPushButton("保存修改")
        self.save.setObjectName("compactButton")
        self.export = QPushButton("导出 TXT")
        self.export.setObjectName("compactButton")
        self.save.setEnabled(False)
        actions.addWidget(self.save)
        actions.addWidget(self.export)
        right.addLayout(actions)
        body.addLayout(right, 1)
        self.layout.addWidget(workspace, 1)
        self.sessions.currentItemChanged.connect(self._selection)
        self.retry.clicked.connect(self._retry)
        self.save.clicked.connect(self._save)
        self.export.clicked.connect(self._export)
        self.delete.clicked.connect(self._delete)
        self.pipeline.stage_selected.connect(self._select_stage)
        self._session_id = ""
        self._stage_texts: dict[str, str] = {}
        self._selected_stage = "asr"
        self._final_stage = "asr"
        self._editable = False
        self.clear_detail()

    def _selection(self, current: object, previous: object) -> None:
        if current:
            self.selection_changed.emit(str(current.data(Qt.ItemDataRole.UserRole)))

    def _retry(self) -> None:
        item = self.sessions.currentItem()
        if item:
            self.retry_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def _save(self) -> None:
        item = self.sessions.currentItem()
        if item:
            self.save_requested.emit(
                str(item.data(Qt.ItemDataRole.UserRole)), self.text.toPlainText()
            )

    def _export(self) -> None:
        item = self.sessions.currentItem()
        if item:
            self.export_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def _delete(self) -> None:
        item = self.sessions.currentItem()
        if item:
            self.delete_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def set_pipeline(
        self,
        session_id: str,
        title: str,
        stages: list[dict[str, str]],
        final_text: str,
        editable: bool,
        visible: bool = True,
    ) -> None:
        self._session_id = session_id
        self.detail_title.setText(title or "未命名文稿")
        self.detail_title.setToolTip(title)
        self._stage_texts = {}
        self._editable = editable
        self.pipeline.setVisible(visible)
        states: dict[str, str] = {}
        for item in stages:
            stage = item.get("stage", "")
            if stage:
                self._stage_texts[stage] = item.get("text", "")
                states[stage] = item.get("status", "completed")
        if not self._stage_texts:
            self._stage_texts["asr"] = final_text
            states["asr"] = "completed"
        available = [
            stage for stage, _ in PIPELINE_STAGES if self._stage_texts.get(stage)
        ]
        self._final_stage = available[-1] if available else "asr"
        self._stage_texts[self._final_stage] = final_text
        for stage, _ in PIPELINE_STAGES:
            state = states.get(stage, "pending")
            self.pipeline.set_stage(stage, state, bool(self._stage_texts.get(stage)))
        self._select_stage(self._final_stage)
        self.delete.setEnabled(bool(session_id))

    def _select_stage(self, stage: str) -> None:
        if stage not in self._stage_texts:
            return
        if self._selected_stage == self._final_stage and not self.text.isReadOnly():
            self._stage_texts[self._selected_stage] = self.text.toPlainText()
        self._selected_stage = stage
        self.pipeline.select(stage)
        self.text.setPlainText(self._stage_texts.get(stage, ""))
        can_edit = self._editable and stage == self._final_stage
        self.text.setReadOnly(not can_edit)
        self.save.setEnabled(can_edit)

    def clear_detail(self) -> None:
        self._session_id = ""
        self._stage_texts = {}
        self._selected_stage = "asr"
        self._final_stage = "asr"
        self._editable = False
        self.detail_title.setText("选择一项文稿")
        self.detail_title.setToolTip("")
        self.text.clear()
        self.text.setReadOnly(True)
        self.save.setEnabled(False)
        self.retry.setEnabled(False)
        self.delete.setEnabled(False)
        for stage, _ in PIPELINE_STAGES:
            self.pipeline.set_stage(stage, "pending", False)


class SettingsPage(Page):
    save_requested = Signal(object, str)
    test_requested = Signal(object, str)
    scenario_save_requested = Signal(str, object)
    scenario_apply_requested = Signal(str)
    scenario_delete_requested = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        api_key: str,
        scenarios: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__()
        self.scroll = QScrollArea()
        self.scroll.setObjectName("settingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("settingsScrollContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 4)
        content_layout.setSpacing(15)

        scenario_card = Card("scenarioCard", shadow=False)
        scenario_layout = QVBoxLayout(scenario_card)
        scenario_layout.setContentsMargins(16, 12, 16, 12)
        scenario_layout.setSpacing(10)
        scenario_copy = QVBoxLayout()
        scenario_copy.setSpacing(1)
        scenario_copy.addWidget(_label("场景配置", "cardTitle"))
        scenario_layout.addLayout(scenario_copy)
        scenario_controls = QHBoxLayout()
        scenario_controls.setSpacing(8)
        self.scenario = NoWheelComboBox()
        self.scenario.setMinimumWidth(250)
        scenario_controls.addWidget(self.scenario, 1)
        self.apply_scenario = QPushButton("应用")
        self.save_scenario = QPushButton("保存为场景")
        self.save_scenario.setObjectName("primaryButton")
        self.delete_scenario = QPushButton("删除")
        self.delete_scenario.setObjectName("dangerButton")
        scenario_controls.addWidget(self.apply_scenario)
        scenario_controls.addWidget(self.save_scenario)
        scenario_controls.addWidget(self.delete_scenario)
        scenario_layout.addLayout(scenario_controls)
        content_layout.addWidget(scenario_card)

        columns = QVBoxLayout()
        columns.setSpacing(15)
        cloud = Card("settingsCard")
        cloud.setMinimumHeight(760)
        cloud_layout = QVBoxLayout(cloud)
        cloud_layout.setContentsMargins(20, 18, 20, 18)
        cloud_layout.setSpacing(11)
        cloud_layout.addWidget(_label("云端模型链", "cardTitle"))
        cloud_layout.addWidget(_label("同一密钥串联语音识别与两次文本处理", "cardCaption"))

        self.api_key = QLineEdit(api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-…")
        self.remember = QCheckBox("保存凭据")
        self.remember.setToolTip("保存到 Windows 凭据存储")
        self.remember.setChecked(config.remember_key)
        key_box = QHBoxLayout()
        key_box.setContentsMargins(0, 0, 0, 0)
        key_box.setSpacing(10)
        key_box.addWidget(self.api_key, 1)
        key_box.addWidget(self.remember)
        key_widget = QWidget()
        key_widget.setLayout(key_box)
        cloud_layout.addWidget(_settings_field("API Key", key_widget))

        self.region = NoWheelComboBox()
        self.region.addItem("北京", "beijing")
        self.region.addItem("新加坡", "singapore")
        self.region.setCurrentIndex(max(0, self.region.findData(config.region)))
        self.language = NoWheelComboBox()
        self.language.addItem("中文", "zh")
        self.language.addItem("英文", "en")
        self.language.addItem("自动识别", "auto")
        self.language.setCurrentIndex(max(0, self.language.findData(config.language)))
        region_row = QHBoxLayout()
        region_row.setContentsMargins(0, 0, 0, 0)
        region_row.setSpacing(12)
        region_row.addWidget(_settings_field("地域", self.region), 1)
        region_row.addWidget(_settings_field("语言", self.language), 1)
        cloud_layout.addLayout(region_row)

        self.model = QLineEdit(config.model)
        self.model.setPlaceholderText("例如 qwen-audio-3.0-asr-flash-streaming")
        self.llm_model = QLineEdit(config.llm_model)
        self.llm_model.setPlaceholderText("例如 qwen-plus；留空将跳过文本处理")
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(12)
        model_row.addWidget(_settings_field("ASR Model ID", self.model), 1)
        model_row.addWidget(_settings_field("LLM Model ID", self.llm_model), 1)
        cloud_layout.addLayout(model_row)

        self.workspace = QLineEdit(config.workspace_id)
        self.workspace.setPlaceholderText("可选；填写 Base URL 后可留空")
        self.endpoint = QLineEdit(config.base_url or config.websocket_url)
        self.endpoint.setPlaceholderText(
            "https://…/api/v1 或 wss://…/api-ws/v1/inference"
        )
        self.endpoint.setToolTip(
            "填写完整 Base URL 时将优先使用；留空则按地域和 Workspace ID 自动生成"
        )
        endpoint_row = QHBoxLayout()
        endpoint_row.setContentsMargins(0, 0, 0, 0)
        endpoint_row.setSpacing(12)
        endpoint_row.addWidget(_settings_field("Workspace ID", self.workspace), 1)
        endpoint_row.addWidget(_settings_field("Base URL", self.endpoint), 1)
        cloud_layout.addLayout(endpoint_row)
        prompt_header = QHBoxLayout()
        prompt_header.addWidget(_label("定向转写 Prompt", "cardTitle"))
        prompt_header.addStretch(1)
        self.transcription_prompt_count = _label("0 / 400", "cardCaption")
        prompt_header.addWidget(self.transcription_prompt_count)
        cloud_layout.addLayout(prompt_header)
        cloud_layout.addWidget(
            _label(
                "把人名、产品名和领域术语作为上下文随每个文件切片发送；最多 400 字符",
                "cardCaption",
            )
        )
        self.transcription_prompt = QPlainTextEdit(config.transcription_prompt)
        self.transcription_prompt.setObjectName("promptEditor")
        self.transcription_prompt.setPlaceholderText(
            "例如：JustSpeak、Qwen-Audio、百炼、项目代号 Aurora"
        )
        self.transcription_prompt.setFixedHeight(112)
        cloud_layout.addWidget(self.transcription_prompt)
        cloud_layout.addWidget(_label("定向修复 Prompt", "cardTitle"))
        cloud_layout.addWidget(
            _label("可使用 {text} 指定原文插入位置；留空时使用默认模板", "cardCaption")
        )
        self.polish_prompt = QPlainTextEdit(
            config.polish_prompt or DEFAULT_POLISH_PROMPT
        )
        self.polish_prompt.setObjectName("promptEditor")
        self.polish_prompt.setFixedHeight(132)
        cloud_layout.addWidget(self.polish_prompt)
        self.test = QPushButton("录制 4 秒并测试 ASR")
        self.test.setToolTip("实际调用一次语音模型；该操作不会触发两次 LLM 文本处理")
        cloud_layout.addWidget(self.test)
        columns.addWidget(cloud)

        local = Card("settingsCard")
        local_layout = QVBoxLayout(local)
        local_layout.setContentsMargins(20, 18, 20, 18)
        local_layout.setSpacing(11)
        local_layout.addWidget(_label("本机处理", "cardTitle"))
        local_layout.addWidget(_label("控制任务文件、FFmpeg 与分段策略", "cardCaption"))
        self.data_dir = QLineEdit(config.data_dir)
        data_button = QPushButton("选择")
        data_button.setObjectName("quietButton")
        data_row = QHBoxLayout()
        data_row.setContentsMargins(0, 0, 0, 0)
        data_row.setSpacing(7)
        data_row.addWidget(self.data_dir, 1)
        data_row.addWidget(data_button)
        data_widget = QWidget()
        data_widget.setLayout(data_row)
        self.ffmpeg = QLineEdit(config.ffmpeg_path)
        self.ffmpeg.setPlaceholderText("自动查找，或选择 ffmpeg.exe")
        ffmpeg_button = QPushButton("选择")
        ffmpeg_button.setObjectName("quietButton")
        ffmpeg_row = QHBoxLayout()
        ffmpeg_row.setContentsMargins(0, 0, 0, 0)
        ffmpeg_row.setSpacing(7)
        ffmpeg_row.addWidget(self.ffmpeg, 1)
        ffmpeg_row.addWidget(ffmpeg_button)
        ffmpeg_widget = QWidget()
        ffmpeg_widget.setLayout(ffmpeg_row)
        paths_row = QHBoxLayout()
        paths_row.setContentsMargins(0, 0, 0, 0)
        paths_row.setSpacing(12)
        paths_row.addWidget(_settings_field("数据目录", data_widget), 1)
        paths_row.addWidget(_settings_field("FFmpeg", ffmpeg_widget), 1)
        local_layout.addLayout(paths_row)

        self.chunk = NoWheelSpinBox()
        self.chunk.setRange(1, 10)
        self.chunk.setSuffix(" 分钟")
        self.chunk.setValue(max(1, min(10, round(config.chunk_seconds / 60))))
        self.chunk.setToolTip("文件转写时每个音频切片的目标时长，相邻切片固定重叠 10 秒")
        local_layout.addWidget(_settings_field("切片长度", self.chunk))
        local_layout.addStretch(1)
        columns.addWidget(local)
        content_layout.addLayout(columns)
        actions = QHBoxLayout()
        actions.addWidget(_label("配置修改对下一项任务生效", "cardCaption"))
        actions.addStretch(1)
        self.save = QPushButton("保存设置")
        self.save.setObjectName("primaryButton")
        actions.addWidget(self.save)
        content_layout.addLayout(actions)
        content_layout.addStretch(1)
        self.scroll.setWidget(content)
        self.layout.addWidget(self.scroll, 1)
        data_button.clicked.connect(self._choose_data_dir)
        ffmpeg_button.clicked.connect(self._choose_ffmpeg)
        self.save.clicked.connect(
            lambda: self.save_requested.emit(self.values(), self.api_key.text())
        )
        self.test.clicked.connect(
            lambda: self.test_requested.emit(self.values(), self.api_key.text())
        )
        self.save_scenario.clicked.connect(self._save_scenario)
        self.apply_scenario.clicked.connect(self._apply_scenario)
        self.delete_scenario.clicked.connect(self._delete_scenario)
        self.scenario.currentIndexChanged.connect(self._scenario_selection_changed)
        self.transcription_prompt.textChanged.connect(
            self._limit_transcription_prompt
        )
        self._limit_transcription_prompt()
        self.set_scenarios(scenarios or [])

    def values(self) -> AppConfig:
        return AppConfig(
            region=str(self.region.currentData()),
            workspace_id=self.workspace.text().strip(),
            base_url=self.endpoint.text().strip(),
            model=(
                self.model.text().strip()
                or "qwen-audio-3.0-asr-flash-streaming"
            ),
            llm_model=self.llm_model.text().strip(),
            transcription_prompt=self.transcription_prompt.toPlainText().strip()[
                :MAX_TRANSCRIPTION_PROMPT_LENGTH
            ],
            polish_prompt=self.polish_prompt.toPlainText().strip() or DEFAULT_POLISH_PROMPT,
            language=str(self.language.currentData()),
            data_dir=self.data_dir.text().strip(),
            ffmpeg_path=self.ffmpeg.text().strip(),
            chunk_seconds=self.chunk.value() * 60,
            remember_key=self.remember.isChecked(),
        )

    def apply_config(self, config: AppConfig) -> None:
        self.region.setCurrentIndex(max(0, self.region.findData(config.region)))
        self.workspace.setText(config.workspace_id)
        self.model.setText(config.model)
        self.endpoint.setText(config.base_url or config.websocket_url)
        self.llm_model.setText(config.llm_model)
        self.transcription_prompt.setPlainText(
            config.transcription_prompt[:MAX_TRANSCRIPTION_PROMPT_LENGTH]
        )
        self.polish_prompt.setPlainText(config.polish_prompt or DEFAULT_POLISH_PROMPT)
        self.language.setCurrentIndex(max(0, self.language.findData(config.language)))
        self.chunk.setValue(max(1, min(10, round(config.chunk_seconds / 60))))

    def _limit_transcription_prompt(self) -> None:
        text = self.transcription_prompt.toPlainText()
        if len(text) > MAX_TRANSCRIPTION_PROMPT_LENGTH:
            cursor = self.transcription_prompt.textCursor()
            position = min(cursor.position(), MAX_TRANSCRIPTION_PROMPT_LENGTH)
            self.transcription_prompt.blockSignals(True)
            self.transcription_prompt.setPlainText(
                text[:MAX_TRANSCRIPTION_PROMPT_LENGTH]
            )
            cursor = self.transcription_prompt.textCursor()
            cursor.setPosition(position)
            self.transcription_prompt.setTextCursor(cursor)
            self.transcription_prompt.blockSignals(False)
            text = text[:MAX_TRANSCRIPTION_PROMPT_LENGTH]
        self.transcription_prompt_count.setText(
            f"{len(text)} / {MAX_TRANSCRIPTION_PROMPT_LENGTH}"
        )

    def set_scenarios(
        self, scenarios: list[dict[str, object]], selected_id: str = ""
    ) -> None:
        self.scenario.clear()
        self.scenario.addItem("选择一个已保存场景", "")
        for item in scenarios:
            self.scenario.addItem(str(item.get("name") or "未命名场景"), item.get("id"))
        selected = self.scenario.findData(selected_id)
        self.scenario.setCurrentIndex(selected if selected >= 0 else 0)
        has_scenarios = len(scenarios) > 0
        self.apply_scenario.setEnabled(has_scenarios and bool(self.scenario.currentData()))
        self.delete_scenario.setEnabled(has_scenarios and bool(self.scenario.currentData()))

    def _scenario_selection_changed(self, index: int = -1) -> None:
        selected = bool(self.scenario.currentData())
        self.apply_scenario.setEnabled(selected)
        self.delete_scenario.setEnabled(selected)

    def _save_scenario(self) -> None:
        name, accepted = QInputDialog.getText(self, "保存场景", "场景名称")
        if accepted and name.strip():
            self.scenario_save_requested.emit(name.strip(), self.values())

    def _apply_scenario(self) -> None:
        scenario_id = str(self.scenario.currentData() or "")
        if scenario_id:
            self.scenario_apply_requested.emit(scenario_id)

    def _delete_scenario(self) -> None:
        scenario_id = str(self.scenario.currentData() or "")
        if scenario_id:
            self.scenario_delete_requested.emit(scenario_id)

    def _choose_data_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择数据目录", self.data_dir.text())
        if path:
            self.data_dir.setText(path)

    def _choose_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 ffmpeg.exe",
            self.ffmpeg.text(),
            "ffmpeg (ffmpeg.exe);;所有文件 (*)",
        )
        if path:
            self.ffmpeg.setText(path)
