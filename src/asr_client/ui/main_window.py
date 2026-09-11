from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from asr_client.audio.ffmpeg import probe_audio
from asr_client.jobs.common import export_transcript
from asr_client.jobs.file_job import FileTranscriptionJob, extract_audio_only
from asr_client.jobs.realtime_job import GapRecoveryJob, RealtimeJob
from asr_client.models import AppConfig, JobUpdate, SessionStatus
from asr_client.providers import DashScopeAsrProvider, MockAsrProvider
from asr_client.storage.config import ApiKeyStore, ConfigStore
from asr_client.storage.database import Database
from asr_client.ui.pages import FilePage, HistoryPage, RealtimePage, SettingsPage


class Bridge(QObject):
    file_update = Signal(object)
    realtime_update = Signal(object)
    inspected = Signal(object, object)
    failed = Signal(str)
    extracted = Signal(str)


class MainWindow(QMainWindow):
    def __init__(
        self,
        database: Database,
        config_store: ConfigStore,
        key_store: ApiKeyStore,
        startup_warning: str = "",
    ) -> None:
        super().__init__()
        self.database = database
        self.config_store = config_store
        self.key_store = key_store
        self.config = config_store.load()
        self._file_job: FileTranscriptionJob | GapRecoveryJob | None = None
        self._realtime_job: RealtimeJob | None = None
        self._file_thread: threading.Thread | None = None
        self._realtime_thread: threading.Thread | None = None
        self._file_session_id = ""
        self._realtime_session_id = ""
        self._connection_test = False
        self.bridge = Bridge()
        self.setWindowTitle("JustSpeak · 云端语音转文字")
        self.resize(1120, 760)
        self.setMinimumSize(960, 650)
        self._build_ui()
        self._connect()
        self.refresh_microphones()
        self.refresh_history()
        if startup_warning:
            QTimer.singleShot(
                100,
                lambda: QMessageBox.warning(self, "数据目录已回退", startup_warning),
            )

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(224)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 25, 20, 20)
        brand_row = QHBoxLayout()
        brand_mark = QLabel("JS")
        brand_mark.setObjectName("brandMark")
        brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_mark.setFixedSize(38, 38)
        brand_row.addWidget(brand_mark)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        brand = QLabel("JustSpeak")
        brand.setObjectName("brand")
        tagline = QLabel("VOICE WORKSPACE")
        tagline.setObjectName("tagline")
        brand_copy.addWidget(brand)
        brand_copy.addWidget(tagline)
        brand_row.addLayout(brand_copy, 1)
        side.addLayout(brand_row)
        side.addSpacing(30)
        menu_label = QLabel("工作区")
        menu_label.setObjectName("sidebarLabel")
        side.addWidget(menu_label)
        side.addSpacing(5)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setSpacing(2)
        for name in ("01   实时录音", "02   文件转写", "03   历史记录", "04   设置"):
            self.navigation.addItem(name)
        self.navigation.setCurrentRow(0)
        side.addWidget(self.navigation, 1)
        privacy = QFrame()
        privacy.setObjectName("privacyBadge")
        privacy_layout = QHBoxLayout(privacy)
        privacy_layout.setContentsMargins(11, 10, 11, 10)
        privacy_layout.setSpacing(8)
        privacy_dot = QLabel("●")
        privacy_dot.setObjectName("privacyDot")
        privacy_layout.addWidget(privacy_dot, 0, Qt.AlignmentFlag.AlignTop)
        privacy_copy = QVBoxLayout()
        privacy_copy.setSpacing(2)
        privacy_title = QLabel("本机安全落盘")
        privacy_title.setObjectName("privacyTitle")
        mode = QLabel("识别由百炼完成\n本机无模型下载")
        mode.setObjectName("sideNotice")
        privacy_copy.addWidget(privacy_title)
        privacy_copy.addWidget(mode)
        privacy_layout.addLayout(privacy_copy, 1)
        side.addWidget(privacy)
        side.addSpacing(10)
        version = QLabel("OPEN SOURCE · V0.1")
        version.setObjectName("sidebarLabel")
        side.addWidget(version)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pageStack")
        self.realtime_page = RealtimePage()
        self.file_page = FilePage()
        self.history_page = HistoryPage()
        self.settings_page = SettingsPage(self.config, self.key_store.get())
        if self.key_store.get():
            self.settings_page.set_page_status("云端已配置", "success")
        else:
            self.settings_page.set_page_status("等待 API Key", "warning")
        for page in (
            self.realtime_page,
            self.file_page,
            self.history_page,
            self.settings_page,
        ):
            self.pages.addWidget(page)
        layout.addWidget(sidebar)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

    def _connect(self) -> None:
        self.navigation.currentRowChanged.connect(self._switch_page)
        self.realtime_page.refresh.clicked.connect(self.refresh_microphones)
        self.realtime_page.start_requested.connect(self.start_realtime)
        self.realtime_page.stop_requested.connect(self.stop_realtime)
        self.realtime_page.export_requested.connect(
            lambda: self._export_text(
                self.realtime_page.transcript.toPlainText(), "实时转写.txt"
            )
        )
        self.realtime_page.copy_requested.connect(self.copy_realtime)
        self.realtime_page.save_edit.clicked.connect(self._save_realtime_edit)
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
        self.history_page.selection_changed.connect(self.show_history)
        self.history_page.retry_requested.connect(self.retry_history)
        self.history_page.save_requested.connect(self.save_history_edit)
        self.history_page.export_requested.connect(self.export_history)
        self.bridge.file_update.connect(lambda update: self.on_job_update("file", update))
        self.bridge.realtime_update.connect(
            lambda update: self.on_job_update("realtime", update)
        )
        self.bridge.inspected.connect(self.on_media_inspected)
        self.bridge.failed.connect(lambda message: QMessageBox.critical(self, "操作失败", message))
        self.bridge.extracted.connect(
            lambda path: QMessageBox.information(self, "提取完成", f"音频已保存到：\n{path}")
        )

    def _switch_page(self, index: int) -> None:
        if index < 0 or index >= self.pages.count():
            return
        self.pages.setCurrentIndex(index)
        page = self.pages.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", page)
        animation.setDuration(210)
        animation.setStartValue(0.2)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        page._reveal_animation = animation  # type: ignore[attr-defined]
        animation.finished.connect(lambda: page.setGraphicsEffect(None))
        animation.start()

    def provider(self, config: AppConfig | None = None, key: str | None = None):
        snapshot = config or self.config
        api_key = self.key_store.get() if key is None else key.strip()
        if api_key:
            return DashScopeAsrProvider(snapshot, api_key)
        return MockAsrProvider()

    def refresh_microphones(self) -> None:
        self.realtime_page.device.clear()
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            for index, device in enumerate(devices):
                if int(device.get("max_input_channels", 0)) > 0:
                    label = f"{device['name']} · {int(device['default_samplerate'])} Hz"
                    self.realtime_page.device.addItem(label, index)
            selected = self.realtime_page.device.findData(self.config.microphone)
            if selected >= 0:
                self.realtime_page.device.setCurrentIndex(selected)
            if self.realtime_page.device.count() == 0:
                self.realtime_page.device.addItem("未发现输入设备", None)
                self.realtime_page.start.setEnabled(False)
                self.realtime_page.set_page_status("无输入设备", "danger")
        except Exception as exc:
            self.realtime_page.device.addItem(f"无法枚举麦克风：{exc}", None)
            self.realtime_page.start.setEnabled(False)
            self.realtime_page.set_page_status("设备不可用", "danger")

    def start_realtime(self) -> None:
        if self._realtime_job is not None:
            return
        if self._file_job is not None:
            self._file_job.pause()
            self.file_page.status.setText("实时录音优先，文件任务将在当前片段后暂停")
        config = AppConfig(**self.config.snapshot())
        device = self.realtime_page.device.currentData()
        if device is None:
            QMessageBox.warning(self, "没有麦克风", "请连接麦克风并刷新设备列表。")
            return
        config.microphone = int(device)
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
                return
            self._realtime_job = RealtimeJob(
                self.database,
                provider,
                config,
                self.bridge.realtime_update.emit,
            )
        except Exception as exc:
            QMessageBox.critical(self, "无法开始录音", str(exc))
            self._realtime_job = None
            return
        self._realtime_session_id = self._realtime_job.session_id
        self.realtime_page.transcript.clear()
        self.realtime_page.set_running(True)
        self._realtime_thread = threading.Thread(
            target=self._realtime_job.run, name="realtime-job", daemon=True
        )
        self._realtime_thread.start()

    def stop_realtime(self) -> None:
        if self._realtime_job:
            self.realtime_page.status.setText("正在停止并等待最后结果…")
            self._realtime_job.stop()
            self.realtime_page.stop.setEnabled(False)

    def inspect_media(self, path: str) -> None:
        if not path:
            return
        self.file_page.status.setText("正在检查媒体…")
        self.file_page.set_page_status("正在检查", "warning")

        def work() -> None:
            try:
                info = probe_audio(Path(path), self.config.ffmpeg_path)
                self.bridge.inspected.emit(info, None)
            except Exception as exc:
                self.bridge.inspected.emit(None, str(exc))

        threading.Thread(target=work, name="media-probe", daemon=True).start()

    def on_media_inspected(self, info: object, error: object) -> None:
        if error:
            self.file_page.status.setText(str(error))
            self.file_page.track.clear()
            self.file_page.start.setEnabled(False)
            self.file_page.only_extract.setEnabled(False)
            self.file_page.set_page_status("文件不可用", "danger")
            return
        self.file_page.set_tracks(info.tracks, info.duration_seconds)

    def start_file(self, path: str, track_index: int) -> None:
        if self._file_job is not None:
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
        if update.kind == "network":
            self.realtime_page.status.setText(update.message)
            if "已连接" in update.message:
                self.realtime_page.set_page_status("云端已连接", "success")
            else:
                self.realtime_page.set_page_status("连接波动", "warning")
            return
        if update.kind == "progress":
            self.file_page.progress.setValue(update.progress or 0)
            self.file_page.status.setText(update.message)
            self.file_page.set_page_status(f"{update.progress or 0}%", "live")
            return
        if update.kind == "transcript":
            if source == "realtime":
                self.realtime_page.transcript.setPlainText(str(update.payload or ""))
            else:
                self.file_page.transcript.setPlainText(str(update.payload or ""))
            return
        if update.kind in {"status", "started"}:
            if source == "realtime":
                self.realtime_page.status.setText(update.message)
                self.realtime_page.set_page_status("录音中", "live")
            else:
                self.file_page.status.setText(update.message)
                self.file_page.set_page_status("处理中", "live")
            return
        if update.kind in {"completed", "incomplete", "error", "cancelled"}:
            if source == "realtime":
                self.realtime_page.status.setText(update.message)
                self.realtime_page.set_running(False)
                self.realtime_page.set_page_status(
                    "已完成" if update.kind == "completed" else "需要处理",
                    "success" if update.kind == "completed" else "warning",
                )
                self._realtime_job = None
                if update.kind != "completed":
                    self.realtime_page.transcript.setReadOnly(True)
                    self.realtime_page.save_edit.setVisible(False)
                if self._connection_test:
                    self._connection_test = False
                    if update.kind == "completed":
                        QMessageBox.information(self, "连接测试成功", "模型已返回结果，配置可用。")
                    else:
                        QMessageBox.critical(self, "连接测试失败", update.message)
            elif self._file_job is not None:
                self.file_page.status.setText(update.message)
                self.file_page.set_running(False)
                self.file_page.set_page_status(
                    "已完成" if update.kind == "completed" else "需要处理",
                    "success" if update.kind == "completed" else "warning",
                )
                if update.kind != "completed":
                    self.file_page.transcript.setReadOnly(True)
                    self.file_page.save_edit.setVisible(False)
                self._file_job = None
            self.refresh_history()

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
        if not config.data_dir:
            QMessageBox.warning(self, "数据目录为空", "请选择一个可写的数据目录。")
            return
        try:
            Path(config.data_dir).mkdir(parents=True, exist_ok=True)
            target_database = Path(config.data_dir) / "justspeak.sqlite3"
            moving_database = target_database.resolve() != self.database.path.resolve()
            if moving_database:
                if self._file_job or self._realtime_job:
                    QMessageBox.warning(
                        self, "任务进行中", "数据目录需在所有任务结束后更改。"
                    )
                    return
                if target_database.exists():
                    QMessageBox.warning(
                        self,
                        "目标目录已有数据",
                        "目标目录中已有 justspeak.sqlite3，请选择空目录以避免覆盖历史。",
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
            self.refresh_microphones()
            self.settings_page.set_page_status("已保存", "success")
            QMessageBox.information(self, "设置已保存", f"新任务将使用：\n{config.endpoint()}")
        except Exception as exc:
            self.settings_page.set_page_status("保存失败", "danger")
            QMessageBox.warning(self, "保存设置", str(exc))

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
        self.navigation.setCurrentRow(0)
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
            item = QListWidgetItem(
                f"{row['title']}\n{status_labels.get(row['status'], row['status'])} · {row['created_at'][:16].replace('T', ' ')}"
            )
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

    def show_history(self, session_id: str) -> None:
        row = self.database.get_session(session_id)
        if not row:
            return
        self.history_page.text.setPlainText(self.database.transcript(session_id))
        completed = row["status"] == SessionStatus.COMPLETED
        self.history_page.text.setReadOnly(not completed)
        self.history_page.save.setEnabled(completed)
        pending = self.database.pending_units(session_id)
        retryable = bool(pending) and row["status"] in {
            SessionStatus.INCOMPLETE,
            SessionStatus.PAUSED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        }
        self.history_page.retry.setEnabled(retryable and self._file_job is None and self._realtime_job is None)
        tone = "success" if completed else ("warning" if retryable else "idle")
        self.history_page.set_page_status(
            "已完成" if completed else str(row["status"]), tone
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
            self.navigation.setCurrentRow(1)
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
        QMessageBox.information(self, "已保存", "文字修改已经保存，原始识别结果仍保留在数据库中。")

    def export_history(self, session_id: str) -> None:
        row = self.database.get_session(session_id)
        title = row["title"] if row else "转写"
        self._export_text(self.database.transcript(session_id), f"{Path(title).stem}.txt")

    def _save_realtime_edit(self) -> None:
        if self._realtime_session_id:
            text = self.realtime_page.transcript.toPlainText()
            self.database.save_edited_text(
                self._realtime_session_id, text
            )
            row = self.database.get_session(self._realtime_session_id)
            if row:
                export_transcript(Path(row["task_dir"]) / "transcript.txt", text)

    def copy_realtime(self) -> None:
        if self._realtime_job is not None and self._realtime_session_id:
            text = self.database.transcript(
                self._realtime_session_id, include_pending=False
            )
        else:
            text = self.realtime_page.transcript.toPlainText()
        QApplication.clipboard().setText(text)

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

    def _export_text(self, text: str, default_name: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出 UTF-8 TXT", default_name, "文本文件 (*.txt)")
        if path:
            try:
                export_transcript(Path(path), text)
            except Exception as exc:
                QMessageBox.critical(self, "导出失败", str(exc))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._realtime_job:
            self._realtime_job.stop()
        if self._file_job:
            self._file_job.cancel()
        for thread in (self._realtime_thread, self._file_thread):
            if thread and thread.is_alive():
                thread.join(timeout=5)
        event.accept()
