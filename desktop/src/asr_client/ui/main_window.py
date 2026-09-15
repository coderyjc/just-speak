from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QCursor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from asr_client.audio.ffmpeg import probe_audio
from asr_client.jobs.common import export_transcript
from asr_client.jobs.file_job import FileTranscriptionJob, extract_audio_only
from asr_client.jobs.realtime_job import GapRecoveryJob, RealtimeJob
from asr_client.models import AppConfig, JobUpdate, SessionStatus
from asr_client.providers import (
    DashScopeAsrProvider,
    DashScopeLlmProvider,
    DashScopeOcrProvider,
    MockAsrProvider,
)
from asr_client.screen_context import (
    capture_screen_data_url,
    merge_screen_context,
)
from asr_client.storage.config import ApiKeyStore, ConfigStore, ScenarioStore
from asr_client.storage.database import Database
from asr_client.ui.pages import (
    FilePage,
    HistoryPage,
    HomePage,
    RealtimePage,
    SettingsPage,
)
from asr_client.ui.widgets import (
    AnimatedListWidget,
    ThemedSizeGrip,
    WindowTitleBar,
    animate_reveal,
)


BEIJING_TIMEZONE = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)


def _format_history_time(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(BEIJING_TIMEZONE).strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)[:16].replace("T", " ")


class Bridge(QObject):
    file_update = Signal(object)
    realtime_update = Signal(object)
    inspected = Signal(object, object)
    failed = Signal(str)
    extracted = Signal(str)
    enrichment_ready = Signal(object, str, str)


class MainWindow(QMainWindow):
    def __init__(
        self,
        database: Database,
        config_store: ConfigStore,
        key_store: ApiKeyStore,
        startup_warning: str = "",
    ) -> None:
        super().__init__()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.database = database
        self.config_store = config_store
        self.key_store = key_store
        self.scenario_store = ScenarioStore(
            self.config_store.path.with_name("scenarios.json")
        )
        self.config = config_store.load()
        self._file_job: FileTranscriptionJob | GapRecoveryJob | None = None
        self._realtime_job: RealtimeJob | None = None
        self._file_thread: threading.Thread | None = None
        self._realtime_thread: threading.Thread | None = None
        self._file_session_id = ""
        self._realtime_session_id = ""
        self._connection_test = False
        self._realtime_preparing = False
        self._closing = False
        self.bridge = Bridge()
        self.setWindowTitle("JustSpeak · 云端语音转文字")
        self.resize(784, 532)
        self.setMinimumSize(672, 455)
        self._build_ui()
        self._connect()
        self._build_realtime_shortcuts()
        self._enforce_history_limit()
        self.refresh_microphones()
        self.refresh_history()
        self.refresh_home()
        if startup_warning:
            QTimer.singleShot(
                100,
                lambda: QMessageBox.warning(self, "数据目录已回退", startup_warning),
            )

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        shell = QVBoxLayout(root)
        shell.setContentsMargins(1, 1, 1, 1)
        shell.setSpacing(0)
        self.title_bar = WindowTitleBar(self)
        shell.addWidget(self.title_bar)

        content = QWidget()
        content.setObjectName("appContent")
        layout = QHBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(120)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 10, 8, 10)
        self.navigation = AnimatedListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setSpacing(2)
        for name in ("主页", "实时录音", "文件转写", "历史记录", "设置"):
            self.navigation.addItem(name)
        self.navigation.setCurrentRow(0)
        side.addWidget(self.navigation, 1)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pageStack")
        self.home_page = HomePage()
        self.realtime_page = RealtimePage()
        self.realtime_page.set_information_enhancement_enabled(
            self.config.information_enhancement_enabled
        )
        self.file_page = FilePage()
        self.history_page = HistoryPage()
        self.settings_page = SettingsPage(
            self.config, self.key_store.get(), self.scenario_store.list()
        )
        if self.key_store.get():
            self.settings_page.set_page_status("云端已配置", "success")
        else:
            self.settings_page.set_page_status("等待 API Key", "warning")
        for page in (
            self.home_page,
            self.realtime_page,
            self.file_page,
            self.history_page,
            self.settings_page,
        ):
            self.pages.addWidget(page)
        layout.addWidget(sidebar)
        layout.addWidget(self.pages, 1)
        shell.addWidget(content, 1)
        self.setCentralWidget(root)
        self.size_grip = ThemedSizeGrip(root)
        self.size_grip.raise_()

    def _connect(self) -> None:
        self.navigation.currentRowChanged.connect(self._switch_page)
        self.realtime_page.refresh.clicked.connect(self.refresh_microphones)
        self.settings_page.information_enhancement.toggled.connect(
            self.realtime_page.set_information_enhancement_enabled
        )
        self.realtime_page.start_requested.connect(self.toggle_realtime)
        self.realtime_page.stop_requested.connect(self.stop_realtime)
        self.realtime_page.export_requested.connect(
            lambda: self._export_text(
                self.realtime_page.transcript.toPlainText(), "实时转写.txt"
            )
        )
        self.realtime_page.copy_requested.connect(self.copy_realtime)
        self.file_page.inspect_requested.connect(self.inspect_media)
        self.file_page.start_requested.connect(self.start_file)
        self.file_page.pause_requested.connect(self.pause_file)
        self.file_page.resume_requested.connect(self.resume_file)
        self.file_page.cancel_requested.connect(self.cancel_file)
        self.file_page.extract_requested.connect(self.extract_media_audio)
        self.file_page.export_requested.connect(
            lambda: self._export_text(
                self.file_page.transcript.toPlainText(), "文件转写.txt"
            )
        )
        self.file_page.save_requested.connect(self._save_file_edit)
        self.settings_page.save_requested.connect(self.save_settings)
        self.settings_page.test_requested.connect(self.test_connection)
        self.settings_page.scenario_save_requested.connect(self.save_scenario)
        self.settings_page.scenario_apply_requested.connect(self.apply_scenario)
        self.settings_page.scenario_delete_requested.connect(self.delete_scenario)
        self.history_page.selection_changed.connect(self.show_history)
        self.history_page.retry_requested.connect(self.retry_history)
        self.history_page.save_requested.connect(self.save_history_edit)
        self.history_page.export_requested.connect(self.export_history)
        self.history_page.delete_requested.connect(self.delete_history)
        self.bridge.file_update.connect(lambda update: self.on_job_update("file", update))
        self.bridge.realtime_update.connect(
            lambda update: self.on_job_update("realtime", update)
        )
        self.bridge.inspected.connect(self.on_media_inspected)
        self.bridge.failed.connect(lambda message: QMessageBox.critical(self, "操作失败", message))
        self.bridge.extracted.connect(
            lambda path: QMessageBox.information(self, "提取完成", f"音频已保存到：\n{path}")
        )
        self.bridge.enrichment_ready.connect(self._on_enrichment_ready)

    def _build_realtime_shortcuts(self) -> None:
        self._realtime_toggle_sequence = QKeySequence()
        self._realtime_stop_sequence = QKeySequence()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._apply_realtime_shortcuts()

    @staticmethod
    def _shortcut_label(sequence: QKeySequence) -> str:
        portable = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        if portable == "Space":
            return "空格"
        return sequence.toString(QKeySequence.SequenceFormat.NativeText)

    @staticmethod
    def _shortcut_conflict(config: AppConfig) -> bool:
        if not (
            config.realtime_toggle_shortcut_enabled
            and config.realtime_stop_shortcut_enabled
        ):
            return False
        toggle = QKeySequence(config.realtime_toggle_shortcut)
        stop = QKeySequence(config.realtime_stop_shortcut)
        return not toggle.isEmpty() and toggle == stop

    def _apply_realtime_shortcuts(self) -> None:
        toggle = QKeySequence(self.config.realtime_toggle_shortcut)
        stop = QKeySequence(self.config.realtime_stop_shortcut)
        toggle_enabled = (
            self.config.realtime_toggle_shortcut_enabled and not toggle.isEmpty()
        )
        stop_enabled = self.config.realtime_stop_shortcut_enabled and not stop.isEmpty()
        if toggle_enabled and stop_enabled and toggle == stop:
            stop_enabled = False
        self._realtime_toggle_sequence = toggle if toggle_enabled else QKeySequence()
        self._realtime_stop_sequence = stop if stop_enabled else QKeySequence()
        self.realtime_page.set_shortcut_hints(
            self._shortcut_label(toggle) if toggle_enabled else "",
            self._shortcut_label(stop) if stop_enabled else "",
        )

    def _realtime_shortcut_allowed(self) -> bool:
        if self.pages.currentWidget() is not self.realtime_page:
            return False
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QPlainTextEdit)) and not focused.isReadOnly():
            return False
        return True

    def _handle_realtime_toggle_shortcut(self) -> None:
        if self._realtime_shortcut_allowed():
            self.toggle_realtime()

    def _handle_realtime_stop_shortcut(self) -> None:
        if self._realtime_shortcut_allowed() and self._realtime_job is not None:
            self.stop_realtime()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress or not self._realtime_shortcut_allowed():
            return super().eventFilter(watched, event)
        focused = QApplication.focusWidget()
        if focused is not None and focused.window() is not self:
            return super().eventFilter(watched, event)
        key_combination = getattr(event, "keyCombination", lambda: None)()
        if key_combination is None:
            return super().eventFilter(watched, event)
        pressed = QKeySequence(key_combination)
        if not self._realtime_stop_sequence.isEmpty() and pressed == self._realtime_stop_sequence:
            if not getattr(event, "isAutoRepeat", lambda: False)():
                self._handle_realtime_stop_shortcut()
            return True
        if (
            not self._realtime_toggle_sequence.isEmpty()
            and pressed == self._realtime_toggle_sequence
        ):
            if not getattr(event, "isAutoRepeat", lambda: False)():
                self._handle_realtime_toggle_shortcut()
            return True
        return super().eventFilter(watched, event)

    def _switch_page(self, index: int) -> None:
        if index < 0 or index >= self.pages.count():
            return
        if self.pages.currentIndex() == index:
            return
        self.pages.setCurrentIndex(index)
        animate_reveal(self.pages.currentWidget(), 190, 0.18)
        if self.pages.currentWidget() is self.home_page:
            self.refresh_home()

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)  # type: ignore[arg-type]
        if hasattr(self, "size_grip"):
            root = self.centralWidget()
            self.size_grip.move(
                root.width() - self.size_grip.width(),
                root.height() - self.size_grip.height(),
            )
            self.size_grip.setVisible(not self.isMaximized())
            self.size_grip.raise_()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.sync_window_state()
            self.size_grip.setVisible(not self.isMaximized())

    def provider(self, config: AppConfig | None = None, key: str | None = None):
        snapshot = config or self.config
        api_key = self.key_store.get() if key is None else key.strip()
        if api_key:
            return DashScopeAsrProvider(snapshot, api_key)
        return MockAsrProvider()

    def text_provider(
        self, config: AppConfig | None = None, key: str | None = None
    ) -> DashScopeLlmProvider | None:
        snapshot = config or self.config
        api_key = self.key_store.get() if key is None else key.strip()
        if api_key and snapshot.llm_model.strip():
            return DashScopeLlmProvider(snapshot, api_key)
        return None

    def refresh_microphones(self) -> None:
        self.realtime_page.device.clear()
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            for index, device in enumerate(devices):
                if int(device.get("max_input_channels", 0)) > 0:
                    label = f"{device['name']} · {int(device['default_samplerate'])} Hz"
                    self.realtime_page.device.addItem(label, index)
                    item_index = self.realtime_page.device.count() - 1
                    self.realtime_page.device.setItemData(
                        item_index, label, Qt.ItemDataRole.ToolTipRole
                    )
            selected = self.realtime_page.device.findData(self.config.microphone)
            if selected >= 0:
                self.realtime_page.device.setCurrentIndex(selected)
            if self.realtime_page.device.count() == 0:
                self.realtime_page.device.addItem("未发现输入设备", None)
                self.realtime_page.start.setEnabled(False)
                self.realtime_page.set_page_status("无输入设备", "danger")
            else:
                self.realtime_page.start.setEnabled(self._realtime_job is None)
        except Exception as exc:
            self.realtime_page.device.addItem(f"无法枚举麦克风：{exc}", None)
            self.realtime_page.start.setEnabled(False)
            self.realtime_page.set_page_status("设备不可用", "danger")
        self.refresh_capture_screens()

    def refresh_capture_screens(self) -> None:
        selected_name = str(self.realtime_page.screen.currentData() or "")
        pointer_screen = QGuiApplication.screenAt(QCursor.pos())
        preferred_name = selected_name or (
            pointer_screen.name() if pointer_screen is not None else ""
        )
        self.realtime_page.screen.clear()
        for index, screen in enumerate(QGuiApplication.screens(), 1):
            size = screen.size()
            ratio = screen.devicePixelRatio()
            width = round(size.width() * ratio)
            height = round(size.height() * ratio)
            label = f"屏幕 {index}：{width} × {height}"
            self.realtime_page.screen.addItem(label, screen.name())
            item_index = self.realtime_page.screen.count() - 1
            self.realtime_page.screen.setItemData(
                item_index,
                f"{label}\n{screen.name()}",
                Qt.ItemDataRole.ToolTipRole,
            )
        selected = self.realtime_page.screen.findData(preferred_name)
        if selected >= 0:
            self.realtime_page.screen.setCurrentIndex(selected)
        if self.realtime_page.screen.count() == 0:
            self.realtime_page.screen.addItem("未发现显示器", "")
            self.realtime_page.screen.setEnabled(False)
        else:
            self.realtime_page.screen.setEnabled(self._realtime_job is None)

    def toggle_realtime(self) -> None:
        if self._realtime_job is None:
            if self.realtime_page.can_copy_and_reset():
                QApplication.clipboard().setText(self.realtime_page.final_text())
                self.realtime_page.reset_for_next_round()
                self._realtime_session_id = ""
                self.realtime_page.set_page_status(
                    self.realtime_page.next_round_status(), "success"
                )
                return
            self.start_realtime()
        elif self._realtime_job.is_paused:
            self.resume_realtime()
        elif not self._realtime_job.is_stopping:
            self.pause_realtime()

    def start_realtime(self) -> None:
        if self._realtime_job is not None or self._realtime_preparing:
            return
        # Read the live settings controls so a just-changed OCR limit takes effect
        # even when the debounce timer has not emitted its auto-save yet.
        config = self.settings_page.values()
        device = self.realtime_page.device.currentData()
        if device is None:
            QMessageBox.warning(self, "没有麦克风", "请连接麦克风并刷新设备列表。")
            return
        config.microphone = int(device)
        if config.information_enhancement_enabled and not self._connection_test:
            screen_name = str(self.realtime_page.screen.currentData() or "")
            self._prepare_realtime_with_screen_context(config, screen_name)
            return
        self._launch_realtime(config)

    def _prepare_realtime_with_screen_context(
        self, config: AppConfig, screen_name: str
    ) -> None:
        self._realtime_preparing = True
        self.realtime_page.reset_pipeline(context_enabled=True, started=True)
        self.realtime_page.set_recording_state("preparing")
        logger.info("信息增强开始：screen=%s", screen_name or "auto")
        try:
            image_data_url = capture_screen_data_url(screen_name)
        except Exception as exc:
            logger.warning("信息增强截图失败：%s", exc)
            self.realtime_page.set_stage_state(
                "context", "failed", f"屏幕上下文读取失败：{exc}"
            )
            self._realtime_preparing = False
            self._launch_realtime(
                config, enhancement_error=str(exc), pipeline_prepared=True
            )
            return

        def work() -> None:
            try:
                provider = DashScopeOcrProvider(config, self.key_store.get())
                text = provider.extract_text(image_data_url)
                self.bridge.enrichment_ready.emit(config, text, "")
            except Exception as exc:
                self.bridge.enrichment_ready.emit(config, "", str(exc))

        threading.Thread(
            target=work, name="screen-context-ocr", daemon=True
        ).start()

    def _on_enrichment_ready(
        self, config: AppConfig, text: str, error: str
    ) -> None:
        if self._closing or not self._realtime_preparing:
            return
        self._realtime_preparing = False
        if error:
            logger.warning("信息增强 OCR 失败：%s", error)
            self.realtime_page.set_stage_state(
                "context", "failed", f"屏幕上下文读取失败：{error}"
            )
            self._launch_realtime(
                config, enhancement_error=error, pipeline_prepared=True
            )
            return
        augmented, included = merge_screen_context(
            config.transcription_prompt,
            text,
            config.ocr_context_max_chars,
        )
        config.transcription_prompt = augmented
        screen_context_text = " ".join(text.split())[
            : config.ocr_context_max_chars
        ]
        self.realtime_page.set_stage_state(
            "context", "completed", screen_context_text
        )
        logger.info(
            "信息增强完成：model=%s ocr_chars=%d display_limit=%d context_chars=%d",
            config.ocr_model,
            len(text),
            config.ocr_context_max_chars,
            included,
        )
        self._launch_realtime(
            config,
            context_chars=included,
            screen_context_text=screen_context_text,
            pipeline_prepared=True,
        )

    def _launch_realtime(
        self,
        config: AppConfig,
        context_chars: int = 0,
        enhancement_error: str = "",
        screen_context_text: str = "",
        pipeline_prepared: bool = False,
    ) -> None:
        if self._closing or self._realtime_job is not None:
            return
        if self._file_job is not None:
            self._file_job.pause()
            self.file_page.set_page_status(
                "实时录音优先，文件任务将在当前片段后暂停", "warning"
            )
        try:
            provider = self.provider(config)
            if not getattr(provider, "supports_streaming", True):
                QMessageBox.warning(
                    self,
                    "模型不支持当前实时入口",
                    f"{config.model} 无法通过当前实时录音入口调用。\n\n"
                    "实时录音请选择 qwen-audio-3.0-asr-flash-streaming "
                    "或 fun-asr-realtime；HTTP 模型请前往“文件转写”。",
                )
                self.realtime_page.set_recording_state("idle")
                return
            self._realtime_job = RealtimeJob(
                self.database,
                provider,
                config,
                self.bridge.realtime_update.emit,
                text_provider=(
                    None if self._connection_test else self.text_provider(config)
                ),
            )
            if config.information_enhancement_enabled:
                self.database.save_text_stage(
                    self._realtime_job.session_id,
                    "context",
                    -1,
                    "屏幕上下文",
                    screen_context_text,
                    "failed" if enhancement_error else "completed",
                    config.ocr_model,
                    config.ocr_prompt,
                    error=enhancement_error,
                )
        except Exception as exc:
            QMessageBox.critical(self, "无法开始录音", str(exc))
            self._realtime_job = None
            self.realtime_page.set_recording_state("idle")
            return
        self._realtime_session_id = self._realtime_job.session_id
        pruned, _ = self._enforce_history_limit()
        if pruned:
            self.refresh_history()
        self.refresh_home()
        if pipeline_prepared:
            self.realtime_page.set_stage_state("asr", "running")
        else:
            self.realtime_page.reset_pipeline(False, started=True)
        self.realtime_page.set_running(True)
        self._realtime_thread = threading.Thread(
            target=self._realtime_job.run, name="realtime-job", daemon=True
        )
        self._realtime_thread.start()

    def pause_realtime(self) -> None:
        if self._realtime_job and self._realtime_job.pause():
            self.realtime_page.set_recording_state("paused")

    def resume_realtime(self) -> None:
        if self._realtime_job and self._realtime_job.resume():
            self.realtime_page.set_recording_state("recording")

    def stop_realtime(self) -> None:
        if self._realtime_job and not self._realtime_job.is_stopping:
            self.realtime_page.set_recording_state("stopping")
            self._realtime_job.stop()

    def inspect_media(self, path: str) -> None:
        if not path:
            return
        self.file_page.set_page_status("正在检查媒体…", "warning")

        def work() -> None:
            try:
                info = probe_audio(Path(path), self.config.ffmpeg_path)
                self.bridge.inspected.emit(info, None)
            except Exception as exc:
                self.bridge.inspected.emit(None, str(exc))

        threading.Thread(target=work, name="media-probe", daemon=True).start()

    def on_media_inspected(self, info: object, error: object) -> None:
        if error:
            self.file_page.track.clear()
            self.file_page.start.setEnabled(False)
            self.file_page.only_extract.setEnabled(False)
            self.file_page.set_page_status(f"文件不可用 · {error}", "danger")
            return
        self.file_page.set_tracks(info.tracks, info.duration_seconds)

    def start_file(self, path: str, track_index: int) -> None:
        if self._file_job is not None:
            return
        if self._realtime_preparing:
            QMessageBox.information(self, "正在准备录音", "请等待屏幕上下文读取完成。")
            return
        if self._realtime_job is not None:
            QMessageBox.information(self, "实时录音进行中", "请先停止实时录音。")
            return
        try:
            self._file_job = FileTranscriptionJob(
                self.database,
                self.provider(),
                self.config,
                Path(path),
                track_index,
                self.bridge.file_update.emit,
            )
        except Exception as exc:
            QMessageBox.critical(self, "无法创建任务", str(exc))
            return
        self._file_session_id = self._file_job.session_id
        pruned, _ = self._enforce_history_limit()
        if pruned:
            self.refresh_history()
        self.refresh_home()
        self.file_page.transcript.clear()
        self.file_page.progress.setValue(0)
        self.file_page.set_running(True)
        self._file_thread = threading.Thread(
            target=self._file_job.run, name="file-job", daemon=True
        )
        self._file_thread.start()

    def pause_file(self) -> None:
        if self._file_job:
            self._file_job.pause()
            self.file_page.pause.setEnabled(False)
            self.file_page.resume.setEnabled(True)
            self.file_page.set_page_status("即将暂停", "warning")

    def resume_file(self) -> None:
        if self._file_job:
            self._file_job.resume()
            self.file_page.pause.setEnabled(True)
            self.file_page.resume.setEnabled(False)
            self.file_page.set_page_status("处理中", "live")

    def cancel_file(self) -> None:
        if self._file_job:
            self._file_job.cancel()

    def extract_media_audio(self, source: str, track: int) -> None:
        default = str(Path(source).with_suffix(".wav"))
        destination, _ = QFileDialog.getSaveFileName(
            self, "保存 WAV", default, "WAV 音频 (*.wav)"
        )
        if not destination:
            return

        def work() -> None:
            try:
                result = extract_audio_only(
                    Path(source), Path(destination), track, self.config.ffmpeg_path
                )
                self.bridge.extracted.emit(str(result))
            except Exception as exc:
                self.bridge.failed.emit(str(exc))

        threading.Thread(target=work, name="audio-extract", daemon=True).start()

    def on_job_update(self, source: str, update: JobUpdate) -> None:
        if update.kind == "meter":
            self.realtime_page.duration.setText(self._format_duration(update.message))
            self.realtime_page.set_level(float(update.payload or 0))
            return
        if update.kind == "recording_state" and source == "realtime":
            state = str(update.payload or "recording")
            self.realtime_page.set_recording_state(state)
            self.realtime_page.set_page_status(
                update.message, "warning" if state == "paused" else "live"
            )
            return
        if update.kind == "network":
            if "已连接" in update.message:
                self.realtime_page.set_page_status(update.message, "success")
            else:
                self.realtime_page.set_page_status(update.message, "warning")
            return
        if update.kind == "progress":
            self.file_page.progress.setValue(update.progress or 0)
            self.file_page.set_page_status(
                f"{update.message} · {update.progress or 0}%", "live"
            )
            return
        if update.kind == "transcript":
            if source == "realtime":
                self.realtime_page.set_stage_text("asr", str(update.payload or ""))
            else:
                self.file_page.transcript.setPlainText(str(update.payload or ""))
            return
        if update.kind == "pipeline" and source == "realtime":
            payload = update.payload if isinstance(update.payload, dict) else {}
            stage = str(payload.get("stage") or "asr")
            state = str(payload.get("state") or "running")
            text = str(payload.get("text") or "")
            self.realtime_page.set_stage_state(stage, state, text)
            tone = "danger" if state == "failed" else (
                "success" if state == "completed" else "live"
            )
            self.realtime_page.set_page_status(update.message, tone)
            return
        if update.kind in {"status", "started"}:
            if source == "realtime":
                self.realtime_page.set_page_status(update.message, "live")
            else:
                self.file_page.set_page_status(update.message, "live")
            return
        if update.kind in {"completed", "incomplete", "error", "cancelled"}:
            if source == "realtime":
                if update.kind == "completed":
                    self.realtime_page.finish_pipeline()
                self.realtime_page.set_running(False)
                self.realtime_page.set_page_status(
                    update.message,
                    (
                        "warning"
                        if update.kind != "completed" or "部分" in update.message
                        else "success"
                    ),
                )
                self._realtime_job = None
                if self._connection_test:
                    self._connection_test = False
                    if update.kind == "completed":
                        QMessageBox.information(self, "连接测试成功", "模型已返回结果，配置可用。")
                    else:
                        QMessageBox.critical(self, "连接测试失败", update.message)
            elif self._file_job is not None:
                self.file_page.set_running(False)
                self.file_page.set_page_status(
                    update.message,
                    "success" if update.kind == "completed" else "warning",
                )
                if update.kind != "completed":
                    self.file_page.transcript.setReadOnly(True)
                    self.file_page.save_edit.setVisible(False)
                self._file_job = None
            self._enforce_history_limit()
            self.refresh_history()
            self.refresh_home()

    @staticmethod
    def _format_duration(message: str) -> str:
        try:
            seconds = float(message.replace("秒", "").strip())
        except ValueError:
            return message
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(int(minutes), 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:04.1f}"
        return f"{minutes:02d}:{seconds:04.1f}"

    def save_settings(self, config: AppConfig, api_key: str) -> None:
        if self._shortcut_conflict(config):
            self.settings_page.set_page_status(
                "自动保存暂停 · 两项实时录音快捷键不能相同", "danger"
            )
            return
        if not config.data_dir:
            self.settings_page.set_page_status(
                "自动保存暂停 · 请选择可写的数据目录", "danger"
            )
            return
        try:
            Path(config.data_dir).mkdir(parents=True, exist_ok=True)
            target_database = Path(config.data_dir) / "justspeak.sqlite3"
            moving_database = target_database.resolve() != self.database.path.resolve()
            if moving_database:
                if self._file_job or self._realtime_job:
                    self.settings_page.set_page_status(
                        "自动保存暂停 · 请在当前任务结束后更改数据目录",
                        "warning",
                    )
                    return
                if target_database.exists():
                    self.settings_page.set_page_status(
                        "自动保存暂停 · 目标目录中已有 justspeak.sqlite3",
                        "danger",
                    )
                    return
                self.database.backup_to(target_database)
            self.key_store.set(api_key, config.remember_key)
            self.config_store.save(config)
            if moving_database:
                old_database = self.database
                self.database = Database(target_database)
                old_database.close()
            self.config = config
            self.realtime_page.set_information_enhancement_enabled(
                config.information_enhancement_enabled
            )
            self._apply_realtime_shortcuts()
            pruned, residual_dirs = self._enforce_history_limit()
            if moving_database or pruned:
                self.refresh_history()
                self.refresh_home()
            status_parts = ["已自动保存"]
            if pruned:
                status_parts.append(f"已清理 {pruned} 条旧记录")
            if residual_dirs:
                status_parts.append(f"{residual_dirs} 个任务目录需手动清理")
            status_parts.append(datetime.now().strftime("%H:%M:%S"))
            self.settings_page.set_page_status(
                " · ".join(status_parts),
                "warning" if residual_dirs else "success",
            )
        except Exception as exc:
            self.settings_page.set_page_status(f"自动保存失败 · {exc}", "danger")

    def test_connection(self, config: AppConfig, api_key: str) -> None:
        if not api_key.strip():
            QMessageBox.warning(self, "需要 API Key", "连接测试会实际调用模型，请先填写 API Key。")
            return
        if self._realtime_job or self._file_job:
            QMessageBox.information(self, "任务进行中", "请等待当前任务结束后再测试。")
            return
        self.config = config
        self.key_store.set(api_key, False)
        self._connection_test = True
        self.navigation.setCurrentRow(1)
        self.start_realtime()
        if self._realtime_job:
            QTimer.singleShot(4000, self.stop_realtime)

    def refresh_history(self) -> None:
        current = self.history_page.sessions.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.history_page.sessions.clear()
        selected_row = -1
        status_labels = {
            "completed": "已完成",
            "incomplete": "待继续",
            "paused": "已暂停",
            "failed": "失败",
            "cancelled": "已取消",
            "running": "处理中",
            "preparing": "准备中",
            "created": "已创建",
            "recovering": "补转写中",
        }
        rows = self.database.list_sessions()
        for index, row in enumerate(rows):
            status_label = status_labels.get(row["status"], row["status"])
            if row["status"] == SessionStatus.COMPLETED and row["error"]:
                status_label = "部分完成"
            title = (
                f"实时录音·{int(row['char_count'] or 0)}字"
                if row["kind"] == "realtime"
                else str(row["title"] or "未命名文稿")
            )
            short_title = self.history_page.sessions.fontMetrics().elidedText(
                title, Qt.TextElideMode.ElideMiddle, 180
            )
            item = QListWidgetItem(
                f"{short_title}\n{status_label} · {_format_history_time(row['created_at'])}"
            )
            item.setToolTip(title)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.history_page.sessions.addItem(item)
            if row["id"] == current_id:
                selected_row = index
        if selected_row >= 0:
            self.history_page.sessions.setCurrentRow(selected_row)
        self.history_page.set_page_status(
            f"{len(rows)} 项记录" if rows else "暂无记录",
            "success" if rows else "idle",
        )

    def _enforce_history_limit(self) -> tuple[int, int]:
        protected_ids: set[str] = set()
        if self._realtime_job is not None and self._realtime_session_id:
            protected_ids.add(self._realtime_session_id)
        if self._file_job is not None and self._file_session_id:
            protected_ids.add(self._file_session_id)
        victims = self.database.prune_sessions(
            self.config.history_limit, protected_ids
        )
        residual_dirs = 0
        for row in victims:
            session_id = str(row["id"])
            task_dir_value = str(row["task_dir"] or "").strip()
            if task_dir_value:
                task_dir = Path(task_dir_value)
                try:
                    if task_dir.exists():
                        resolved = task_dir.resolve()
                        if (
                            resolved.name != session_id
                            or resolved.parent.name != "tasks"
                        ):
                            raise RuntimeError("任务目录校验失败")
                        shutil.rmtree(resolved)
                except (OSError, RuntimeError) as exc:
                    residual_dirs += 1
                    logger.warning(
                        "历史记录已清理，任务目录保留：%s (%s)",
                        task_dir,
                        exc,
                    )
            if self._realtime_session_id == session_id:
                self._realtime_session_id = ""
            if self._file_session_id == session_id:
                self._file_session_id = ""
        if victims:
            self.history_page.clear_detail()
        return len(victims), residual_dirs

    def refresh_home(self) -> None:
        self.home_page.set_statistics(self.database.usage_statistics())

    def show_history(self, session_id: str) -> None:
        row = self.database.get_session(session_id)
        if not row:
            return
        final_text = self.database.transcript(session_id)
        stages = [dict(item) for item in self.database.text_stages(session_id)]
        completed = row["status"] == SessionStatus.COMPLETED
        self.history_page.set_pipeline(
            session_id,
            str(row["title"] or "未命名文稿"),
            stages,
            final_text,
            completed,
            row["kind"] == "realtime",
        )
        pending = self.database.pending_units(session_id)
        retryable = bool(pending) and row["status"] in {
            SessionStatus.INCOMPLETE,
            SessionStatus.PAUSED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        }
        self.history_page.retry.setEnabled(retryable and self._file_job is None and self._realtime_job is None)
        tone = "success" if completed and not row["error"] else (
            "warning" if retryable or row["error"] else "idle"
        )
        self.history_page.set_page_status(
            (
                "部分完成"
                if completed and row["error"]
                else "已完成" if completed else str(row["status"])
            ),
            tone,
        )

    def retry_history(self, session_id: str) -> None:
        if self._file_job or self._realtime_job:
            return
        row = self.database.get_session(session_id)
        if not row:
            return
        try:
            raw_config = json.loads(row["config_json"])
            track_index = int(raw_config.pop("track_index", 0))
            fields = set(AppConfig.__dataclass_fields__)
            config = AppConfig(**{k: v for k, v in raw_config.items() if k in fields})
            if row["kind"] == "file":
                self._file_job = FileTranscriptionJob(
                    self.database,
                    self.provider(config),
                    config,
                    Path(row["source_path"]),
                    track_index,
                    self.bridge.file_update.emit,
                    session_id=session_id,
                )
            else:
                self._file_job = GapRecoveryJob(
                    self.database,
                    self.provider(config),
                    session_id,
                    self.bridge.file_update.emit,
                )
            self._file_session_id = session_id
            self.navigation.setCurrentRow(2)
            self.file_page.source.setText(row["source_path"] or row["title"])
            self.file_page.transcript.setPlainText(self.database.transcript(session_id))
            self.file_page.set_running(True)
            self._file_thread = threading.Thread(
                target=self._file_job.run, name="file-retry", daemon=True
            )
            self._file_thread.start()
        except Exception as exc:
            QMessageBox.critical(self, "无法继续任务", str(exc))
            self._file_job = None

    def save_history_edit(self, session_id: str, text: str) -> None:
        row = self.database.get_session(session_id)
        if not row or row["status"] != SessionStatus.COMPLETED:
            QMessageBox.warning(self, "任务尚未完成", "补齐待转写区间后才能修改最终文稿。")
            return
        self.database.save_edited_text(session_id, text)
        export_transcript(Path(row["task_dir"]) / "transcript.txt", text)
        self.refresh_home()
        QMessageBox.information(self, "已保存", "文字修改已经保存，原始识别结果仍保留在数据库中。")

    def export_history(self, session_id: str) -> None:
        row = self.database.get_session(session_id)
        title = row["title"] if row else "转写"
        self._export_text(self.database.transcript(session_id), f"{Path(title).stem}.txt")

    def delete_history(self, session_id: str) -> None:
        if session_id in {self._realtime_session_id, self._file_session_id} and (
            self._realtime_job or self._file_job
        ):
            QMessageBox.warning(self, "任务进行中", "请先结束当前任务，再删除该记录。")
            return
        row = self.database.get_session(session_id)
        if not row:
            return
        answer = QMessageBox.question(
            self,
            "删除历史记录",
            f"确定删除“{row['title']}”及其本地音频、转写结果和处理阶段吗？\n\n"
            "删除后无法恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        task_dir = Path(row["task_dir"])
        staged: Path | None = None
        try:
            if task_dir.exists():
                resolved = task_dir.resolve()
                if resolved.name != session_id or resolved.parent.name != "tasks":
                    raise RuntimeError("任务目录校验失败，已停止删除本地文件")
                staged = resolved.with_name(f".{session_id}.deleting")
                if staged.exists():
                    raise RuntimeError(f"临时删除目录已存在：{staged}")
                resolved.rename(staged)
            if not self.database.delete_session(session_id):
                raise RuntimeError("数据库中没有找到该记录")
        except Exception as exc:
            if staged and staged.exists() and not task_dir.exists():
                try:
                    staged.rename(task_dir)
                except OSError:
                    pass
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        if staged and staged.exists():
            try:
                shutil.rmtree(staged)
            except OSError as exc:
                QMessageBox.warning(
                    self,
                    "记录已删除",
                    f"数据库记录已经删除，残留文件夹需要手动清理：\n{staged}\n\n{exc}",
                )
        if self._realtime_session_id == session_id:
            self._realtime_session_id = ""
        if self._file_session_id == session_id:
            self._file_session_id = ""
        self.history_page.clear_detail()
        self.refresh_history()
        self.refresh_home()

    def save_scenario(self, name: str, config: AppConfig) -> None:
        try:
            item = self.scenario_store.save(name, config)
            self.settings_page.set_scenarios(
                self.scenario_store.list(), str(item.get("id") or "")
            )
            self.settings_page.set_page_status(f"场景“{name}”已保存", "success")
        except Exception as exc:
            QMessageBox.warning(self, "保存场景失败", str(exc))

    def apply_scenario(self, scenario_id: str) -> None:
        config = self.scenario_store.apply_to(
            scenario_id, self.settings_page.values()
        )
        if config is None:
            QMessageBox.warning(self, "场景不存在", "该场景可能已经被删除。")
            return
        self.settings_page.apply_config(config)
        self.config = config
        self.config_store.save(config)
        item = self.scenario_store.get(scenario_id)
        name = str(item.get("name") if item else "场景")
        self.settings_page.set_page_status(f"已应用 · {name}", "success")

    def delete_scenario(self, scenario_id: str) -> None:
        item = self.scenario_store.get(scenario_id)
        if item is None:
            return
        answer = QMessageBox.question(
            self,
            "删除场景",
            f"确定删除场景“{item.get('name')}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.scenario_store.delete(scenario_id)
            self.settings_page.set_scenarios(self.scenario_store.list())
            self.settings_page.set_page_status("场景已删除", "idle")

    def copy_realtime(self) -> None:
        if self._realtime_job is not None and self._realtime_session_id:
            text = self.database.transcript(
                self._realtime_session_id, include_pending=False
            )
        else:
            text = self.realtime_page.transcript.toPlainText()
        if not text.strip():
            self.realtime_page.set_page_status("当前没有可复制的文字", "warning")
            return
        QApplication.clipboard().setText(text)
        self.realtime_page.set_page_status("全文已复制到剪贴板", "success")
        button = self.realtime_page.copy
        button.setProperty("feedback", "success")
        button.style().unpolish(button)
        button.style().polish(button)

        def clear_feedback() -> None:
            if button.property("feedback") == "success":
                button.setProperty("feedback", "")
                button.style().unpolish(button)
                button.style().polish(button)

        QTimer.singleShot(1400, clear_feedback)

    def _save_file_edit(self) -> None:
        if self._file_session_id:
            self.database.save_edited_text(
                self._file_session_id, self.file_page.transcript.toPlainText()
            )
            row = self.database.get_session(self._file_session_id)
            if row:
                export_transcript(
                    Path(row["task_dir"]) / "transcript.txt",
                    self.file_page.transcript.toPlainText(),
                )
            self.refresh_home()

    def _export_text(self, text: str, default_name: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出 UTF-8 TXT", default_name, "文本文件 (*.txt)")
        if path:
            try:
                export_transcript(Path(path), text)
            except Exception as exc:
                QMessageBox.critical(self, "导出失败", str(exc))

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self._realtime_preparing = False
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._realtime_job:
            self._realtime_job.stop()
        if self._file_job:
            self._file_job.cancel()
        for thread in (self._realtime_thread, self._file_thread):
            if thread and thread.is_alive():
                thread.join(timeout=5)
        event.accept()
