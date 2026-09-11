from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

import numpy as np

from asr_client.audio.chunker import finalize_pcm_wav, pcm_range_to_wav
from asr_client.audio.resample import StatefulResampler
from asr_client.jobs.common import atomic_write_text
from asr_client.models import (
    AppConfig,
    AudioChunk,
    DEFAULT_POLISH_PROMPT,
    JobUpdate,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    SessionStatus,
    TextModelResult,
    TranscriptEvent,
    UnitStatus,
)
from asr_client.providers.base import (
    AsrError,
    AsrProvider,
    ErrorKind,
    StreamingSession,
    TextProvider,
)
from asr_client.providers.dashscope_llm import CLEAR_TEXT_PROMPT
from asr_client.storage.database import Database


UpdateSink = Callable[[JobUpdate], None]


class RealtimeJob:
    """Durable microphone recorder with an independently paced cloud sender."""

    def __init__(
        self,
        database: Database,
        provider: AsrProvider,
        config: AppConfig,
        update: UpdateSink | None = None,
        stop_timeout: float = 15.0,
        text_provider: TextProvider | None = None,
        text_stage_timeout: float = 60.0,
    ) -> None:
        self.database = database
        self.provider = provider
        self.config = AppConfig(**config.snapshot())
        self.update = update or (lambda _: None)
        self.stop_timeout = stop_timeout
        self.text_provider = text_provider
        self.text_stage_timeout = max(0.1, float(text_stage_timeout))
        self._stop_requested = threading.Event()
        self._capture_done = threading.Event()
        self._writer_done = threading.Event()
        self._fatal = threading.Event()
        self._audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._condition = threading.Condition()
        self._durable_samples = 0
        self._confirmed_samples = 0
        self._fatal_message = ""
        self._stream: StreamingSession | None = None
        self._gaps: list[tuple[int, int]] = []
        self._callback_run_id = ""
        self._callback_origin = 0
        self._callback_lock = threading.Lock()
        self._current_stream_had_text = False
        self._current_stream_had_timed_final = False
        self._cloud_stop_timed_out = False
        self._temporary: dict[str, tuple[int, str, bool]] = {}

        data_root = Path(self.config.data_dir)
        provisional = data_root / "tasks" / "pending"
        self.session_id = database.create_session(
            "realtime", "实时录音", provisional, self.config.snapshot()
        )
        self.task_dir = data_root / "tasks" / self.session_id
        self.task_dir.mkdir(parents=True, exist_ok=True)
        with database.transaction() as db:
            db.execute(
                "UPDATE sessions SET task_dir=? WHERE id=?",
                (str(self.task_dir), self.session_id),
            )
        self.pcm_path = self.task_dir / "recording.pcm"
        self.wav_path = self.task_dir / "recording.wav"

    def stop(self) -> None:
        self._stop_requested.set()
        with self._condition:
            self._condition.notify_all()

    def run(self) -> None:
        self.database.set_session_status(self.session_id, SessionStatus.RUNNING)
        self.update(JobUpdate("started", "正在打开麦克风", payload=self.session_id))
        writer = threading.Thread(target=self._writer_loop, name="audio-writer", daemon=True)
        sender = threading.Thread(target=self._sender_loop, name="asr-sender", daemon=True)
        writer.start()
        sender.start()
        try:
            self._capture()
        except Exception as exc:
            self._fail_local(f"麦克风录音失败：{exc}")
        finally:
            self._capture_done.set()
            with self._condition:
                self._condition.notify_all()
            writer.join(timeout=10)
            if writer.is_alive():
                self._fail_local("音频写入线程未能及时结束")
            sender.join(timeout=max(20.0, self.stop_timeout + 5))
            if sender.is_alive():
                self._fail_local("云端结束操作超时，尾部已标记为待补转写")
            self._finish_recording()

    def _capture(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("缺少 sounddevice 依赖，请先运行 setup.ps1") from exc
        device = self.config.microphone
        input_rate = self._select_input_rate(sd, device)
        blocksize = max(1, input_rate // 10)
        self._input_rate = input_rate

        def callback(indata: object, frames: int, time_info: object, status: object) -> None:
            if status and getattr(status, "input_overflow", False):
                self._fail_local("麦克风输入发生溢出，录音已停止")
                return
            try:
                self._audio_queue.put_nowait(bytes(indata))
            except queue.Full:
                self._fail_local("录音队列已满，录音已停止以防止静默丢帧")

        self.update(JobUpdate("status", f"录音中 · 输入 {input_rate} Hz"))
        with sd.RawInputStream(
            samplerate=input_rate,
            blocksize=blocksize,
            device=device,
            channels=1,
            dtype="int16",
            callback=callback,
        ):
            while not self._stop_requested.wait(0.1):
                if self._fatal.is_set():
                    if self._stream is not None:
                        self._invalidate_callback()
                        self._stream.abort()
                        self._stream = None
                    break

    @staticmethod
    def _select_input_rate(sd: object, device: int | None) -> int:
        try:
            sd.check_input_settings(device=device, channels=1, dtype="int16", samplerate=SAMPLE_RATE)
            return SAMPLE_RATE
        except Exception:
            info = sd.query_devices(device, "input")
            rate = int(round(float(info["default_samplerate"])))
            sd.check_input_settings(device=device, channels=1, dtype="int16", samplerate=rate)
            return rate

    def _writer_loop(self) -> None:
        while not hasattr(self, "_input_rate") and not self._capture_done.wait(0.01):
            pass
        input_rate = getattr(self, "_input_rate", SAMPLE_RATE)
        resampler = StatefulResampler(input_rate)
        try:
            with self.pcm_path.open("ab", buffering=0) as output:
                self._durable_samples = self.pcm_path.stat().st_size // SAMPLE_WIDTH
                while not (self._capture_done.is_set() and self._audio_queue.empty()):
                    try:
                        raw = self._audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    standard = resampler.process(raw)
                    output.write(standard)
                    samples = len(standard) // SAMPLE_WIDTH
                    with self._condition:
                        self._durable_samples += samples
                        durable = self._durable_samples
                        self._condition.notify_all()
                    level = self._level(standard)
                    self.update(
                        JobUpdate(
                            "meter",
                            f"{durable / SAMPLE_RATE:0.1f} 秒",
                            payload=level,
                        )
                    )
                    if durable % SAMPLE_RATE < samples:
                        self._write_metadata()
                        self.database.update_samples(
                            self.session_id, durable, self._confirmed_samples
                        )
        except Exception as exc:
            self._fail_local(f"录音写入失败：{exc}")
        finally:
            self._writer_done.set()
            with self._condition:
                self._condition.notify_all()

    @staticmethod
    def _level(pcm: bytes) -> float:
        if not pcm:
            return 0.0
        values = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        rms = float(np.sqrt(np.mean(values * values))) if len(values) else 0.0
        return min(1.0, rms / 12_000.0)

    def _sender_loop(self) -> None:
        cursor = 0
        stream_origin = 0
        reconnect_attempt = 0
        offline_permanently = False
        try:
            self.pcm_path.touch(exist_ok=True)
            reader = self.pcm_path.open("rb", buffering=0)
        except OSError as exc:
            self._fail_local(f"无法打开录音缓存：{exc}")
            return
        with reader:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._durable_samples - cursor >= 1600
                        or self._writer_done.is_set()
                        or self._fatal.is_set(),
                        timeout=0.5,
                    )
                    durable = self._durable_samples
                if self._fatal.is_set():
                    break
                if offline_permanently:
                    if self._writer_done.is_set():
                        self._add_gap(self._confirmed_samples, durable)
                        break
                    continue
                if self._stream is None:
                    try:
                        if reconnect_attempt:
                            gap_start = max(stream_origin, self._confirmed_samples)
                            self._add_gap(gap_start, durable)
                            cursor = durable
                            stream_origin = cursor
                        self._start_cloud_stream(stream_origin)
                        with self._condition:
                            latest = self._durable_samples
                        if latest - cursor > 5 * SAMPLE_RATE:
                            # Starting the SDK may wait behind a file request. Skip the
                            # accumulated audio here and recover it as a paced file gap.
                            self._add_gap(max(stream_origin, self._confirmed_samples), latest)
                            if self._stream is not None:
                                self._invalidate_callback()
                                self._stream.abort()
                                self._stream = None
                            cursor = latest
                            stream_origin = cursor
                            self._start_cloud_stream(stream_origin)
                            durable = latest
                        reconnect_attempt = 0
                        self.update(JobUpdate("network", "云端已连接"))
                    except AsrError as exc:
                        reconnect_attempt += 1
                        if self._writer_done.is_set():
                            self._add_gap(max(stream_origin, self._confirmed_samples), durable)
                            break
                        if not exc.retryable:
                            offline_permanently = True
                            self.update(JobUpdate("network", f"云端暂停：{exc}"))
                            continue
                        delay = (1, 2, 4, 8)[min(reconnect_attempt - 1, 3)]
                        self.update(
                            JobUpdate("network", f"录音继续，云端暂不可用，{delay} 秒后重连")
                        )
                        self._stop_requested.wait(delay)
                        continue
                available = durable - cursor
                if available > 5 * SAMPLE_RATE and self._stream is not None:
                    self._mark_temporary_uncertain()
                    self._add_gap(max(stream_origin, self._confirmed_samples), durable)
                    self._invalidate_callback()
                    self._stream.abort()
                    self._stream = None
                    cursor = durable
                    stream_origin = cursor
                    self.update(JobUpdate("network", "云端发送持续落后，已切换到缺口补转写"))
                    continue
                if available > 0 and self._stream is not None:
                    count = min(1600, available)
                    reader.seek(cursor * SAMPLE_WIDTH)
                    data = reader.read(count * SAMPLE_WIDTH)
                    if data:
                        try:
                            self._stream.send(data)
                            cursor += len(data) // SAMPLE_WIDTH
                        except AsrError as exc:
                            self._mark_temporary_uncertain()
                            try:
                                self._invalidate_callback()
                                self._stream.abort()
                            finally:
                                self._stream = None
                            reconnect_attempt += 1
                            if not exc.retryable:
                                offline_permanently = True
                            self.update(JobUpdate("network", f"云端连接中断：{exc}"))
                            continue
                if self._writer_done.is_set() and cursor >= durable:
                    if self._stream is not None:
                        if not self._stop_cloud_with_timeout():
                            self._add_gap(max(stream_origin, self._confirmed_samples), durable)
                        elif self._current_stream_had_text and not self._current_stream_had_timed_final:
                            self._add_gap(stream_origin, durable)
                        self._stream = None
                    break

    def _start_cloud_stream(self, origin: int) -> None:
        run_id = uuid.uuid4().hex
        with self._callback_lock:
            self._callback_run_id = run_id
            self._callback_origin = origin
            self._current_stream_had_text = False
            self._current_stream_had_timed_final = False

        def sink(event: TranscriptEvent) -> None:
            with self._callback_lock:
                if run_id != self._callback_run_id:
                    return
                self._current_stream_had_text = self._current_stream_had_text or bool(event.text)
                event.source_key = f"{run_id}:{event.source_key}"
                event.start_sample = origin + (event.start_sample or 0)
                if event.end_sample is not None:
                    event.end_sample += origin
                if event.is_final and event.end_sample is not None:
                    self._current_stream_had_timed_final = True
            self.database.upsert_segment(self.session_id, event)
            with self._callback_lock:
                if event.is_final:
                    self._temporary.pop(event.source_key, None)
                else:
                    self._temporary[event.source_key] = (
                        event.start_sample or origin,
                        event.text,
                        True,
                    )
            if event.is_final and event.end_sample is not None:
                self._confirmed_samples = max(self._confirmed_samples, event.end_sample)
                self.database.update_samples(
                    self.session_id, self._durable_samples, self._confirmed_samples
                )
            self.update(JobUpdate("transcript", payload=self._display_text()))

        self._stream = self.provider.start_stream(sink)

    def _mark_temporary_uncertain(self) -> None:
        with self._callback_lock:
            self._temporary = {
                key: (start, text, False)
                for key, (start, text, _confirmed) in self._temporary.items()
            }
        self.update(JobUpdate("transcript", payload=self._display_text()))

    def _invalidate_callback(self) -> None:
        with self._callback_lock:
            self._callback_run_id = ""

    def _display_text(self) -> str:
        confirmed = self.database.transcript(self.session_id, include_pending=False)
        with self._callback_lock:
            temporary = sorted(self._temporary.values(), key=lambda item: item[0])
        parts = [confirmed] if confirmed else []
        parts.extend(
            f"[{'临时' if is_current else '未确认'}] {text}"
            for _, text, is_current in temporary
            if text.strip()
        )
        return "\n".join(parts)

    def _stop_cloud_with_timeout(self) -> bool:
        stream = self._stream
        if stream is None:
            return True
        finished = threading.Event()
        error: list[Exception] = []

        def finish() -> None:
            try:
                stream.stop()
            except Exception as exc:
                error.append(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=finish, name="asr-stop", daemon=True)
        worker.start()
        if not finished.wait(self.stop_timeout):
            self._cloud_stop_timed_out = True
            self.update(JobUpdate("network", "云端结束等待超时，尾部将补转写"))
            return False
        if error:
            self.update(JobUpdate("network", f"云端结束失败：{error[0]}"))
            return False
        return True

    def _add_gap(self, start: int, end: int) -> None:
        start = max(0, min(start, end))
        end = max(start, end)
        if end <= start:
            return
        if self._gaps and start <= self._gaps[-1][1]:
            old_start, old_end = self._gaps[-1]
            self._gaps[-1] = (min(old_start, start), max(old_end, end))
        else:
            self._gaps.append((start, end))

    def _finish_recording(self) -> None:
        with self._callback_lock:
            self._callback_run_id = ""
        if self.pcm_path.exists():
            actual_samples = self.pcm_path.stat().st_size // SAMPLE_WIDTH
            self._durable_samples = actual_samples
            self.database.update_samples(
                self.session_id, actual_samples, self._confirmed_samples
            )
            self._write_metadata()
            try:
                finalize_pcm_wav(self.pcm_path, self.wav_path)
            except Exception as exc:
                self._fail_local(f"WAV 封装失败：{exc}")
        if self._fatal.is_set():
            self.database.set_session_status(
                self.session_id, SessionStatus.FAILED, self._fatal_message
            )
            self.update(JobUpdate("error", self._fatal_message))
            return
        if self._gaps:
            self.database.set_session_status(self.session_id, SessionStatus.RECOVERING)
            self.update(JobUpdate("status", "正在补转写网络缺口"))
            self._prepare_gap_units()
            if self._cloud_stop_timed_out:
                self.database.set_session_status(
                    self.session_id,
                    SessionStatus.INCOMPLETE,
                    "云端结束超时，等待连接释放后继续补转写",
                )
                self._publish()
                self.update(JobUpdate("incomplete", "录音已保存，尾部等待重试"))
                return
            if not self._transcribe_gaps():
                self.database.set_session_status(
                    self.session_id,
                    SessionStatus.INCOMPLETE,
                    "存在尚未完成的网络缺口",
                )
                self._publish()
                self.update(JobUpdate("incomplete", "录音已保存，部分区间等待重试"))
                return
        self._publish()
        pipeline_errors = self._process_text_pipeline()
        self.database.set_session_status(
            self.session_id,
            SessionStatus.COMPLETED,
            "；".join(pipeline_errors),
        )
        message = "录音、清晰化与定向修复已完成"
        if pipeline_errors:
            message = "录音已完成，部分文本处理未成功"
        elif self.text_provider is None or not self.config.llm_model.strip():
            message = "录音与转写已完成；配置 LLM 后可自动清晰化"
        self.update(JobUpdate("completed", message, 100, self.session_id))

    def _process_text_pipeline(self) -> list[str]:
        raw_text = self.database.transcript(self.session_id, include_pending=False).strip()
        self.database.save_text_stage(
            self.session_id, "asr", 0, "实时录音", raw_text, "completed"
        )
        atomic_write_text(self.task_dir / "stages" / "01-realtime.txt", raw_text)
        self.update(
            JobUpdate(
                "pipeline",
                "实时录音已完成",
                payload={"stage": "asr", "state": "completed", "text": raw_text},
            )
        )
        if not raw_text:
            return []
        if self.text_provider is None or not self.config.llm_model.strip():
            for stage, ordinal, label in (
                ("clarity", 1, "文本清晰"),
                ("polish", 2, "定向修复"),
            ):
                self.database.save_text_stage(
                    self.session_id,
                    stage,
                    ordinal,
                    label,
                    status="skipped",
                    error="未配置 LLM Model ID",
                )
                self.update(
                    JobUpdate(
                        "pipeline",
                        f"{label}已跳过",
                        payload={"stage": stage, "state": "skipped", "text": ""},
                    )
                )
            return []

        errors: list[str] = []
        current_text = raw_text
        stages = (
            ("clarity", 1, "文本清晰", CLEAR_TEXT_PROMPT),
            (
                "polish",
                2,
                "定向修复",
                self.config.polish_prompt.strip() or DEFAULT_POLISH_PROMPT,
            ),
        )
        for stage, ordinal, label, prompt in stages:
            self.database.save_text_stage(
                self.session_id,
                stage,
                ordinal,
                label,
                current_text,
                "running",
                self.config.llm_model,
                prompt,
            )
            self.update(
                JobUpdate(
                    "pipeline",
                    f"正在进行{label} · 最长等待 {self.text_stage_timeout:g} 秒",
                    payload={"stage": stage, "state": "running", "text": current_text},
                )
            )
            try:
                result = self._complete_text_stage(prompt, current_text, label)
                current_text = result.text.strip()
                self.database.save_text_stage(
                    self.session_id,
                    stage,
                    ordinal,
                    label,
                    current_text,
                    "completed",
                    self.config.llm_model,
                    prompt,
                    result.request_id,
                    raw=result.raw,
                )
                atomic_write_text(
                    self.task_dir / "stages" / f"{ordinal + 1:02d}-{stage}.txt",
                    current_text,
                )
                self.update(
                    JobUpdate(
                        "pipeline",
                        f"{label}已完成",
                        payload={
                            "stage": stage,
                            "state": "completed",
                            "text": current_text,
                        },
                    )
                )
                self.database.save_edited_text(self.session_id, current_text)
                atomic_write_text(self.task_dir / "transcript.txt", current_text)
            except Exception as exc:
                message = str(exc)
                errors.append(f"{label}：{message}")
                self.database.save_text_stage(
                    self.session_id,
                    stage,
                    ordinal,
                    label,
                    current_text,
                    "failed",
                    self.config.llm_model,
                    prompt,
                    error=message,
                )
                atomic_write_text(
                    self.task_dir / "stages" / f"{ordinal + 1:02d}-{stage}.txt",
                    current_text,
                )
                self.update(
                    JobUpdate(
                        "pipeline",
                        f"{label}失败：{message}",
                        payload={
                            "stage": stage,
                            "state": "failed",
                            "text": current_text,
                            "error": message,
                        },
                    )
                )
        self.database.save_edited_text(self.session_id, current_text)
        atomic_write_text(self.task_dir / "transcript.txt", current_text)
        return errors

    def _complete_text_stage(
        self, prompt: str, text: str, label: str
    ) -> TextModelResult:
        if self.text_provider is None:
            raise AsrError("文本模型未配置", ErrorKind.CONFIGURATION)
        outcome: queue.Queue[tuple[TextModelResult | None, Exception | None]] = (
            queue.Queue(maxsize=1)
        )

        def invoke() -> None:
            try:
                outcome.put((self.text_provider.complete(prompt, text), None))
            except Exception as exc:
                outcome.put((None, exc))

        worker = threading.Thread(
            target=invoke,
            name=f"llm-{label}-{self.session_id[:8]}",
            daemon=True,
        )
        worker.start()
        try:
            result, error = outcome.get(timeout=self.text_stage_timeout)
        except queue.Empty as exc:
            raise AsrError(
                f"{label}超过 {self.text_stage_timeout:g} 秒，已停止等待并保留上一阶段结果",
                ErrorKind.TEMPORARY,
            ) from exc
        if error is not None:
            raise error
        if result is None:
            raise AsrError(f"{label}没有返回结果", ErrorKind.TEMPORARY)
        return result

    def _prepare_gap_units(self) -> None:
        chunks: list[AudioChunk] = []
        for ordinal, (gap_start, gap_end) in enumerate(self._gaps):
            context_start = max(0, gap_start - SAMPLE_RATE)
            context_end = min(self._durable_samples, gap_end + SAMPLE_RATE)
            gap_path = self.task_dir / "gaps" / f"gap-{ordinal:04d}.wav"
            pcm_range_to_wav(self.pcm_path, gap_path, context_start, context_end)
            chunks.append(
                AudioChunk(
                    stable_id=f"{self.session_id}-gap-{ordinal}",
                    path=gap_path,
                    start_sample=context_start,
                    end_sample=context_end,
                    overlap_before_samples=gap_start - context_start,
                )
            )
        self.database.add_units(self.session_id, "gap", chunks)

    def _transcribe_gaps(self) -> bool:
        all_ok = True
        gap_rows = [row for row in self.database.all_units(self.session_id) if row["kind"] == "gap"]
        for row, (gap_start, gap_end) in zip(gap_rows, self._gaps):
            unit_id = row["id"]
            self.database.set_unit_status(unit_id, UnitStatus.RUNNING, increment_attempt=True)
            try:
                events = self.provider.transcribe_file(Path(row["path"]), f"gap-{unit_id}")
                self.database.replace_range(self.session_id, gap_start, gap_end)
                for index, event in enumerate(events):
                    event.start_sample = int(row["start_sample"]) + (event.start_sample or 0)
                    if event.end_sample is not None:
                        event.end_sample += int(row["start_sample"])
                    event.source_key = f"{unit_id}:{event.source_key}:{index}"
                    self.database.upsert_segment(self.session_id, event)
                self.database.set_unit_status(
                    unit_id,
                    UnitStatus.COMPLETED,
                    request_id=events[-1].request_id if events else "",
                )
            except Exception as exc:
                all_ok = False
                self.database.set_unit_status(unit_id, UnitStatus.FAILED, str(exc))
        return all_ok

    def _publish(self) -> None:
        text = self.database.transcript(self.session_id)
        atomic_write_text(self.task_dir / "transcript.txt", text)
        self.update(JobUpdate("transcript", payload=text))

    def _write_metadata(self) -> None:
        atomic_write_text(
            self.task_dir / "recording.json",
            json.dumps(
                {
                    "sample_rate": SAMPLE_RATE,
                    "channels": 1,
                    "sample_width": SAMPLE_WIDTH,
                    "saved_samples": self._durable_samples,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    def _fail_local(self, message: str) -> None:
        if not self._fatal.is_set():
            self._fatal_message = message
        self._fatal.set()
        self._stop_requested.set()
        with self._condition:
            self._condition.notify_all()


def recover_recording_metadata(pcm_path: Path) -> dict[str, int]:
    """Rebuild trustworthy metadata after an abnormal exit."""
    size = pcm_path.stat().st_size
    complete_size = size - (size % SAMPLE_WIDTH)
    return {
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "sample_width": SAMPLE_WIDTH,
        "saved_samples": complete_size // SAMPLE_WIDTH,
    }


class GapRecoveryJob:
    """Resume persisted real-time gap units after configuration is repaired."""

    def __init__(
        self,
        database: Database,
        provider: AsrProvider,
        session_id: str,
        update: UpdateSink | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.database = database
        self.provider = provider
        self.session_id = session_id
        self.update = update or (lambda _: None)
        self.sleep = sleep
        self._pause = threading.Event()
        self._pause.set()
        self._cancel = threading.Event()
        row = database.get_session(session_id)
        if row is None or row["kind"] != "realtime":
            raise ValueError("待补转写的录音任务不存在")
        self.task_dir = Path(row["task_dir"])

    def pause(self) -> None:
        self._pause.clear()
        self.update(JobUpdate("status", "将在当前缺口完成后暂停"))

    def resume(self) -> None:
        self._pause.set()
        self.update(JobUpdate("status", "继续补转写"))

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.set()

    def run(self) -> None:
        pending = [
            row
            for row in self.database.pending_units(self.session_id)
            if row["kind"] == "gap"
        ]
        if not pending:
            self.update(JobUpdate("error", "该录音没有可继续的缺口片段"))
            return
        self.database.set_session_status(self.session_id, SessionStatus.RECOVERING)
        try:
            for ordinal, row in enumerate(pending, 1):
                while not self._pause.wait(0.2):
                    self.database.set_session_status(self.session_id, SessionStatus.PAUSED)
                if self._cancel.is_set():
                    self.database.set_session_status(
                        self.session_id, SessionStatus.INCOMPLETE, "用户停止了缺口补转写"
                    )
                    self.update(JobUpdate("cancelled", "补转写已停止，可稍后继续"))
                    return
                self.database.set_session_status(self.session_id, SessionStatus.RECOVERING)
                self._process(row)
                self.update(
                    JobUpdate(
                        "progress",
                        f"已补转写 {ordinal}/{len(pending)} 个缺口",
                        int(ordinal * 100 / len(pending)),
                    )
                )
            self.database.set_session_status(self.session_id, SessionStatus.COMPLETED)
            text = self.database.transcript(self.session_id)
            atomic_write_text(self.task_dir / "transcript.txt", text)
            self.update(JobUpdate("transcript", payload=text))
            self.update(JobUpdate("completed", "录音缺口补转写完成", 100, self.session_id))
        except Exception as exc:
            self.database.set_session_status(
                self.session_id, SessionStatus.INCOMPLETE, str(exc)
            )
            self.update(JobUpdate("error", str(exc)))

    def _process(self, row: object) -> None:
        unit_id = row["id"]  # type: ignore[index]
        for attempt in range(1, 5):
            self.database.set_unit_status(
                unit_id, UnitStatus.RUNNING, increment_attempt=True
            )
            try:
                events = self.provider.transcribe_file(
                    Path(row["path"]), f"resume-gap-{unit_id}"  # type: ignore[index]
                )
                start = int(row["start_sample"])  # type: ignore[index]
                end = int(row["end_sample"])  # type: ignore[index]
                self.database.replace_range(self.session_id, start, end)
                for index, event in enumerate(events):
                    event.start_sample = start + (event.start_sample or 0)
                    if event.end_sample is not None:
                        event.end_sample += start
                    event.source_key = f"resume-{unit_id}:{event.source_key}:{index}"
                    self.database.upsert_segment(self.session_id, event)
                self.database.set_unit_status(
                    unit_id,
                    UnitStatus.COMPLETED,
                    request_id=events[-1].request_id if events else "",
                )
                return
            except AsrError as exc:
                self.database.set_unit_status(unit_id, UnitStatus.FAILED, str(exc))
                if not exc.retryable or attempt >= 4:
                    raise
                self.sleep(exc.retry_delay(attempt))
            except Exception as exc:
                self.database.set_unit_status(unit_id, UnitStatus.FAILED, str(exc))
                raise
