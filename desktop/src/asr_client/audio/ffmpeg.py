from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from asr_client.models import AudioInfo, AudioTrack


class FFmpegError(RuntimeError):
    pass


def _candidate(path_hint: str, name: str) -> str | None:
    if path_hint:
        hint = Path(path_hint)
        if hint.is_dir():
            executable = hint / f"{name}.exe"
        elif hint.name.lower() in {"ffmpeg.exe", "ffprobe.exe"}:
            executable = hint.with_name(f"{name}.exe")
        else:
            executable = hint
        if executable.exists():
            return str(executable)
    roots = [Path(__file__).resolve().parents[3]]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
        bundle_root = getattr(sys, "_MEIPASS", "")
        if bundle_root:
            roots.insert(1, Path(bundle_root))
    for root in roots:
        bundled = root / "bin" / f"{name}.exe"
        if bundled.exists():
            return str(bundled)
    return shutil.which(name)


def find_ffmpeg(path_hint: str = "") -> tuple[str, str]:
    ffmpeg = _candidate(path_hint, "ffmpeg")
    ffprobe = _candidate(path_hint, "ffprobe")
    if not ffmpeg or not ffprobe:
        raise FFmpegError(
            "未找到 FFmpeg/ffprobe。请在设置中选择 FFmpeg 所在目录，"
            "或将两个程序放入项目 bin 目录。"
        )
    return ffmpeg, ffprobe


def _run(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FFmpegError(f"无法运行音频工具：{exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "未知错误"
        raise FFmpegError(f"FFmpeg 处理失败：{detail}")
    return result


def probe_audio(path: Path, path_hint: str = "") -> AudioInfo:
    _, ffprobe = find_ffmpeg(path_hint)
    if not path.is_file():
        raise FFmpegError(f"文件不存在：{path}")
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        timeout=30,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError("ffprobe 返回了无法解析的信息") from exc
    tracks: list[AudioTrack] = []
    for stream in payload.get("streams", []):
        if stream.get("codec_type") != "audio":
            continue
        tags = stream.get("tags") or {}
        tracks.append(
            AudioTrack(
                index=int(stream["index"]),
                codec=str(stream.get("codec_name") or "unknown"),
                sample_rate=_safe_int(stream.get("sample_rate")),
                channels=_safe_int(stream.get("channels")),
                language=str(tags.get("language") or ""),
            )
        )
    if not tracks:
        raise FFmpegError("文件中没有可用的音轨")
    duration = _safe_float((payload.get("format") or {}).get("duration"))
    if duration <= 0:
        duration = max(
            (_safe_float(s.get("duration")) for s in payload.get("streams", [])),
            default=0.0,
        )
    if duration <= 0:
        raise FFmpegError("无法读取媒体时长，文件可能已经损坏")
    return AudioInfo(path=path, duration_seconds=duration, tracks=tracks)


def convert_to_standard_wav(
    source: Path, destination: Path, track_index: int, path_hint: str = ""
) -> Path:
    ffmpeg, _ = find_ffmpeg(path_hint)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".part.wav")
    _run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            f"0:{track_index}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(temp),
        ]
    )
    os.replace(temp, destination)
    return destination


def _safe_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: object) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0
