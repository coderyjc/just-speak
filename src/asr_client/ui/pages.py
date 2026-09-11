from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from asr_client.models import AppConfig, AudioTrack
from asr_client.ui.widgets import AnimatedProgressBar, Card, SignalOrb, StatusChip, StepBadge


def _label(text: str, name: str) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    return widget


class Page(QWidget):
    def __init__(self, eyebrow: str, title: str, subtitle: str) -> None:
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(34, 28, 34, 28)
        self.layout.setSpacing(16)
        header = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(3)
        copy.addWidget(_label(eyebrow.upper(), "eyebrow"))
        copy.addWidget(_label(title, "pageTitle"))
        description = _label(subtitle, "pageSubtitle")
        description.setWordWrap(True)
        copy.addWidget(description)
        header.addLayout(copy, 1)
        self.page_status = StatusChip()
        header.addWidget(self.page_status, 0, Qt.AlignmentFlag.AlignTop)
        self.layout.addLayout(header)

    def set_page_status(self, text: str, tone: str = "idle") -> None:
        self.page_status.setText(text)
        self.page_status.set_tone(tone)


class RealtimePage(Page):
    start_requested = Signal()
    stop_requested = Signal()
    export_requested = Signal()
    copy_requested = Signal()

    def __init__(self) -> None:
        super().__init__(
            "LIVE CAPTURE",
            "实时录音",
            "声音先安全写入本机，再以稳定节奏送往云端识别。",
        )
        hero = Card("heroCard")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(20, 18, 20, 18)
        hero_layout.setSpacing(16)
        self.orb = SignalOrb()
        hero_layout.addWidget(self.orb)
        readout = QVBoxLayout()
        readout.setSpacing(3)
        readout.addWidget(_label("SESSION MONITOR", "heroLabel"))
        self.status = _label("准备开始一段新录音", "heroStatus")
        self.status.setWordWrap(True)
        readout.addWidget(self.status)
        self.duration = _label("00:00.0", "heroDuration")
        readout.addWidget(self.duration)
        readout.addWidget(_label("音频持续落盘 · 停止后自动检查缺口", "heroHint"))
        readout.addStretch(1)
        hero_layout.addLayout(readout, 1)
        controls = QVBoxLayout()
        controls.setSpacing(9)
        controls.addWidget(_label("输入设备", "heroLabel"))
        self.device = QComboBox()
        self.device.setMinimumWidth(225)
        self.device.setToolTip("选择要使用的麦克风")
        controls.addWidget(self.device)
        self.refresh = QPushButton("刷新设备")
        self.refresh.setObjectName("quietButton")
        controls.addWidget(self.refresh)
        buttons = QHBoxLayout()
        self.start = QPushButton("开始录音")
        self.start.setObjectName("primaryButton")
        self.stop = QPushButton("停止并完成")
        self.stop.setObjectName("dangerButton")
        self.stop.setEnabled(False)
        buttons.addWidget(self.start, 1)
        buttons.addWidget(self.stop, 1)
        controls.addLayout(buttons)
        delaying = QVBoxLayout()
        delaying.addLayout(controls)
        delaying.addStretch(1)
        hero_layout.addLayout(delaying)
        self.layout.addWidget(hero)
        self.level = AnimatedProgressBar()
        self.level.setRange(0, 100)
        self.layout.addWidget(self.level)

        transcript_card = Card("transcriptCard")
        transcript_layout = QVBoxLayout(transcript_card)
        transcript_layout.setContentsMargins(18, 16, 18, 14)
        transcript_layout.setSpacing(10)
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(_label("实时转写稿", "cardTitle"))
        title_box.addWidget(_label("临时句原位更新，最终句自动保存", "cardCaption"))
        head.addLayout(title_box)
        head.addStretch(1)
        self.confirmed_hint = StatusChip("等待语音", "idle")
        head.addWidget(self.confirmed_hint)
        transcript_layout.addLayout(head)
        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText("开始说话后，识别文字会在这里逐句出现…")
        self.transcript.setReadOnly(True)
        transcript_layout.addWidget(self.transcript, 1)
        actions = QHBoxLayout()
        actions.addWidget(_label("录音中复制仅包含已确认文字", "cardCaption"))
        actions.addStretch(1)
        self.copy = QPushButton("复制全文")
        self.copy.setObjectName("quietButton")
        self.save_edit = QPushButton("保存修改")
        self.save_edit.setVisible(False)
        self.export = QPushButton("导出 TXT")
        actions.addWidget(self.copy)
        actions.addWidget(self.save_edit)
        actions.addWidget(self.export)
        transcript_layout.addLayout(actions)
        self.layout.addWidget(transcript_card, 1)
        self.start.clicked.connect(self.start_requested)
        self.stop.clicked.connect(self.stop_requested)
        self.export.clicked.connect(self.export_requested)
        self.copy.clicked.connect(self.copy_requested)

    def set_running(self, running: bool) -> None:
        self.start.setEnabled(not running)
        self.stop.setEnabled(running)
        self.device.setEnabled(not running)
        self.refresh.setEnabled(not running)
        self.transcript.setReadOnly(running)
        self.save_edit.setVisible(not running and bool(self.transcript.toPlainText()))
        self.orb.set_active(running)
        if running:
            self.set_page_status("正在录音", "live")
            self.confirmed_hint.setText("实时接收")
            self.confirmed_hint.set_tone("live")
        else:
            has_text = bool(self.transcript.toPlainText())
            self.set_page_status("准备就绪", "idle")
            self.confirmed_hint.setText("录音已停止" if has_text else "等待语音")
            self.confirmed_hint.set_tone("success" if has_text else "idle")

    def set_level(self, level: float) -> None:
        self.orb.set_level(level)
        self.level.setValue(int(level * 100))


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
        super().__init__(
            "MEDIA WORKFLOW",
            "文件转写",
            "导入媒体、选择音轨，长内容将自动拆分并按时间线合并。",
        )
        workflow = QHBoxLayout()
        workflow.setSpacing(14)
        self.drop_zone = Card("dropZone", shadow=False)
        self.drop_zone.setProperty("dragActive", False)
        drop_layout = QVBoxLayout(self.drop_zone)
        drop_layout.setContentsMargins(17, 15, 17, 15)
        drop_layout.setSpacing(8)
        step_row = QHBoxLayout()
        step_row.addWidget(StepBadge("01"))
        step_text = QVBoxLayout()
        step_text.setSpacing(0)
        step_text.addWidget(_label("选择媒体文件", "dropTitle"))
        step_text.addWidget(_label("支持常见音频与视频格式，也可拖放文件", "dropHint"))
        step_row.addLayout(step_text, 1)
        drop_layout.addLayout(step_row)
        source_row = QHBoxLayout()
        self.source = FileDropEdit()
        self.source.setPlaceholderText("将文件拖到这里，或浏览本机文件")
        browse = QPushButton("浏览文件")
        source_row.addWidget(self.source, 1)
        source_row.addWidget(browse)
        drop_layout.addLayout(source_row)
        workflow.addWidget(self.drop_zone, 3)

        track_card = Card("card")
        track_layout = QVBoxLayout(track_card)
        track_layout.setContentsMargins(17, 15, 17, 15)
        track_layout.setSpacing(8)
        track_head = QHBoxLayout()
        track_head.addWidget(StepBadge("02"))
        track_text = QVBoxLayout()
        track_text.setSpacing(0)
        track_text.addWidget(_label("确认音轨", "cardTitle"))
        track_text.addWidget(_label("多音轨媒体可单独选择", "cardCaption"))
        track_head.addLayout(track_text, 1)
        track_layout.addLayout(track_head)
        self.track = QComboBox()
        self.track.setEnabled(False)
        track_layout.addWidget(self.track)
        track_actions = QHBoxLayout()
        self.only_extract = QPushButton("仅提取 WAV")
        self.only_extract.setEnabled(False)
        self.start = QPushButton("开始转写")
        self.start.setObjectName("primaryButton")
        self.start.setEnabled(False)
        track_actions.addWidget(self.only_extract)
        track_actions.addWidget(self.start, 1)
        track_layout.addLayout(track_actions)
        workflow.addWidget(track_card, 2)
        self.layout.addLayout(workflow)

        progress_card = Card("card", shadow=False)
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(16, 12, 16, 12)
        progress_layout.setSpacing(9)
        state_row = QHBoxLayout()
        self.status = _label("等待选择文件", "cardTitle")
        state_row.addWidget(self.status, 1)
        self.pause = QPushButton("暂停")
        self.resume = QPushButton("继续")
        self.cancel = QPushButton("取消")
        self.cancel.setObjectName("dangerButton")
        self.pause.setEnabled(False)
        self.resume.setEnabled(False)
        self.cancel.setEnabled(False)
        state_row.addWidget(self.pause)
        state_row.addWidget(self.resume)
        state_row.addWidget(self.cancel)
        progress_layout.addLayout(state_row)
        self.progress = AnimatedProgressBar()
        self.progress.setRange(0, 100)
        progress_layout.addWidget(self.progress)
        self.layout.addWidget(progress_card)

        transcript_card = Card("transcriptCard")
        transcript_layout = QVBoxLayout(transcript_card)
        transcript_layout.setContentsMargins(18, 15, 18, 13)
        transcript_layout.setSpacing(9)
        head = QHBoxLayout()
        head.addWidget(_label("转写结果", "cardTitle"))
        head.addWidget(_label("每完成一个片段即刻保存", "cardCaption"))
        head.addStretch(1)
        transcript_layout.addLayout(head)
        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText("选择文件并开始后，片段结果会持续汇入这里…")
        self.transcript.setReadOnly(True)
        transcript_layout.addWidget(self.transcript, 1)
        footer = QHBoxLayout()
        self.copy = QPushButton("复制全文")
        self.copy.setObjectName("quietButton")
        self.save_edit = QPushButton("保存文字修改")
        self.save_edit.setVisible(False)
        self.export = QPushButton("导出 TXT")
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
        self.status.setText(f"已就绪 · {duration / 60:.1f} 分钟 · {len(tracks)} 条音轨")
        self.set_page_status("可以开始", "success")

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

    def __init__(self) -> None:
        super().__init__(
            "LOCAL ARCHIVE",
            "历史记录",
            "回看本机任务、修订最终文字，或继续中途停下的处理。",
        )
        workspace = Card("card")
        body = QHBoxLayout(workspace)
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(15)
        left = QVBoxLayout()
        left.setSpacing(7)
        left.addWidget(_label("任务时间线", "cardTitle"))
        left.addWidget(_label("最新任务排列在最前", "cardCaption"))
        self.sessions = QListWidget()
        self.sessions.setObjectName("historyList")
        self.sessions.setMinimumWidth(315)
        left.addWidget(self.sessions, 1)
        body.addLayout(left)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet("color:#e2ded5;")
        body.addWidget(divider)
        right = QVBoxLayout()
        right.setSpacing(8)
        detail_head = QHBoxLayout()
        detail_head.addWidget(_label("文稿详情", "cardTitle"))
        detail_head.addStretch(1)
        self.retry = QPushButton("继续未完成任务")
        self.retry.setEnabled(False)
        detail_head.addWidget(self.retry)
        right.addLayout(detail_head)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("从左侧选择一项历史任务")
        right.addWidget(self.text, 1)
        actions = QHBoxLayout()
        actions.addWidget(_label("已完成任务支持编辑，原始识别结果会保留", "cardCaption"))
        actions.addStretch(1)
        self.save = QPushButton("保存修改")
        self.export = QPushButton("导出 TXT")
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


class SettingsPage(Page):
    save_requested = Signal(object, str)
    test_requested = Signal(object, str)

    def __init__(self, config: AppConfig, api_key: str) -> None:
        super().__init__(
            "CONFIGURATION",
            "设置",
            "连接你的百炼空间，并决定录音与任务数据在本机的保存位置。",
        )
        columns = QHBoxLayout()
        columns.setSpacing(15)
        cloud = Card("settingsCard")
        cloud_layout = QVBoxLayout(cloud)
        cloud_layout.setContentsMargins(20, 18, 20, 18)
        cloud_layout.setSpacing(11)
        cloud_layout.addWidget(_label("云端识别", "cardTitle"))
        cloud_layout.addWidget(_label("密钥可安全保存在 Windows 凭据存储中", "cardCaption"))
        cloud_form = QFormLayout()
        cloud_form.setSpacing(11)
        self.api_key = QLineEdit(api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-…")
        self.remember = QCheckBox("保存到 Windows 凭据存储")
        self.remember.setChecked(config.remember_key)
        key_box = QVBoxLayout()
        key_box.setSpacing(5)
        key_box.addWidget(self.api_key)
        key_box.addWidget(self.remember)
        key_widget = QWidget()
        key_widget.setLayout(key_box)
        cloud_form.addRow("API Key", key_widget)
        self.region = QComboBox()
        self.region.addItem("北京", "beijing")
        self.region.addItem("新加坡", "singapore")
        self.region.setCurrentIndex(max(0, self.region.findData(config.region)))
        cloud_form.addRow("地域", self.region)
        self.workspace = QLineEdit(config.workspace_id)
        self.workspace.setPlaceholderText("可选；填写 Base URL 后可留空")
        cloud_form.addRow("Workspace ID", self.workspace)
        self.model = QLineEdit(config.model)
        self.model.setPlaceholderText("例如 qwen-audio-3.0-asr-flash-streaming")
        cloud_form.addRow("Model ID", self.model)
        self.endpoint = QLineEdit(config.base_url or config.websocket_url)
        self.endpoint.setPlaceholderText(
            "https://…/api/v1 或 wss://…/api-ws/v1/inference"
        )
        self.endpoint.setToolTip(
            "填写完整 Base URL 时将优先使用；留空则按地域和 Workspace ID 自动生成"
        )
        cloud_form.addRow("Base URL", self.endpoint)
        self.language = QComboBox()
        self.language.addItem("中文", "zh")
        self.language.addItem("英文", "en")
        self.language.addItem("自动识别", "auto")
        self.language.setCurrentIndex(max(0, self.language.findData(config.language)))
        cloud_form.addRow("语言", self.language)
        cloud_layout.addLayout(cloud_form)
        cloud_layout.addStretch(1)
        self.test = QPushButton("录制 4 秒并测试连接")
        self.test.setToolTip("实际调用一次模型，确认密钥、地域和模型均可用")
        cloud_layout.addWidget(self.test)
        columns.addWidget(cloud, 1)

        local = Card("settingsCard")
        local_layout = QVBoxLayout(local)
        local_layout.setContentsMargins(20, 18, 20, 18)
        local_layout.setSpacing(11)
        local_layout.addWidget(_label("本机处理", "cardTitle"))
        local_layout.addWidget(_label("控制任务文件、FFmpeg 与分段策略", "cardCaption"))
        local_form = QFormLayout()
        local_form.setSpacing(11)
        self.data_dir = QLineEdit(config.data_dir)
        data_button = QPushButton("选择")
        data_button.setObjectName("quietButton")
        data_row = QHBoxLayout()
        data_row.addWidget(self.data_dir, 1)
        data_row.addWidget(data_button)
        data_widget = QWidget()
        data_widget.setLayout(data_row)
        local_form.addRow("数据目录", data_widget)
        self.ffmpeg = QLineEdit(config.ffmpeg_path)
        self.ffmpeg.setPlaceholderText("自动查找，或选择 ffmpeg.exe")
        ffmpeg_button = QPushButton("选择")
        ffmpeg_button.setObjectName("quietButton")
        ffmpeg_row = QHBoxLayout()
        ffmpeg_row.addWidget(self.ffmpeg, 1)
        ffmpeg_row.addWidget(ffmpeg_button)
        ffmpeg_widget = QWidget()
        ffmpeg_widget.setLayout(ffmpeg_row)
        local_form.addRow("FFmpeg", ffmpeg_widget)
        self.chunk = QSpinBox()
        self.chunk.setRange(30, 120)
        self.chunk.setSuffix(" 秒")
        self.chunk.setValue(config.chunk_seconds)
        local_form.addRow("片段长度", self.chunk)
        local_layout.addLayout(local_form)
        local_layout.addStretch(1)
        privacy = _label(
            "隐私提示\n选中的音频会发送到你配置的阿里云百炼服务。密钥不会写入配置、数据库或日志。",
            "notice",
        )
        privacy.setWordWrap(True)
        local_layout.addWidget(privacy)
        columns.addWidget(local, 1)
        self.layout.addLayout(columns, 1)
        actions = QHBoxLayout()
        actions.addWidget(_label("配置修改对下一项任务生效", "cardCaption"))
        actions.addStretch(1)
        self.save = QPushButton("保存设置")
        self.save.setObjectName("primaryButton")
        actions.addWidget(self.save)
        self.layout.addLayout(actions)
        data_button.clicked.connect(self._choose_data_dir)
        ffmpeg_button.clicked.connect(self._choose_ffmpeg)
        self.save.clicked.connect(
            lambda: self.save_requested.emit(self.values(), self.api_key.text())
        )
        self.test.clicked.connect(
            lambda: self.test_requested.emit(self.values(), self.api_key.text())
        )

    def values(self) -> AppConfig:
        return AppConfig(
            region=str(self.region.currentData()),
            workspace_id=self.workspace.text().strip(),
            base_url=self.endpoint.text().strip(),
            model=(
                self.model.text().strip()
                or "qwen-audio-3.0-asr-flash-streaming"
            ),
            language=str(self.language.currentData()),
            data_dir=self.data_dir.text().strip(),
            ffmpeg_path=self.ffmpeg.text().strip(),
            chunk_seconds=self.chunk.value(),
            remember_key=self.remember.isChecked(),
        )

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
