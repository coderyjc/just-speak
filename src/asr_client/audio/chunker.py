from __future__ import annotations

import hashlib
import wave
from pathlib import Path

import numpy as np

from asr_client.models import AudioChunk, CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH


def _wav_shape(path: Path) -> tuple[int, int, int, int]:
    with wave.open(str(path), "rb") as source:
        shape = (
            source.getframerate(),
            source.getnchannels(),
            source.getsampwidth(),
            source.getnframes(),
        )
    if shape[:3] != (SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH):
        raise ValueError("分段输入必须为 16 kHz、单声道、16-bit PCM WAV")
    return shape


def plan_chunks(
    wav_path: Path,
    target_seconds: int = 60,
    search_seconds: int = 5,
    overlap_seconds: float = 1.0,
) -> list[tuple[int, int, int]]:
    _, _, _, total = _wav_shape(wav_path)
    target = max(30, min(120, int(target_seconds))) * SAMPLE_RATE
    search = max(1, int(search_seconds)) * SAMPLE_RATE
    overlap = int(overlap_seconds * SAMPLE_RATE)
    min_piece = 10 * SAMPLE_RATE
    analysis_window = SAMPLE_RATE // 10
    planned: list[tuple[int, int, int]] = []
    start = 0
    overlap_before = 0
    with wave.open(str(wav_path), "rb") as source:
        while total - start > target:
            ideal = start + target
            low = max(start + min_piece, ideal - search)
            high = min(total, ideal + search)
            best = _quietest_window(source, low, high, analysis_window)
            found_silence = best is not None and best[1] < 450
            end = best[0] if found_silence else ideal
            end = max(start + analysis_window, min(end, total))
            planned.append((start, end, overlap_before))
            next_start = end if found_silence else max(start + 1, end - overlap)
            overlap_before = end - next_start
            start = next_start
        if start < total:
            planned.append((start, total, overlap_before))
    return planned


def _quietest_window(
    source: wave.Wave_read, low: int, high: int, window: int
) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    cursor = low
    while cursor + window <= high:
        source.setpos(cursor)
        data = source.readframes(window)
        if not data:
            break
        values = np.frombuffer(data, dtype="<i2").astype(np.float32)
        rms = int(np.sqrt(np.mean(values * values))) if len(values) else 0
        if best is None or rms < best[1]:
            best = (cursor + window // 2, rms)
        cursor += window
    return best


def write_chunks(
    wav_path: Path, output_dir: Path, target_seconds: int = 60
) -> list[AudioChunk]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_chunks(wav_path, target_seconds=target_seconds)
    chunks: list[AudioChunk] = []
    frames_per_read = SAMPLE_RATE * 10
    with wave.open(str(wav_path), "rb") as source:
        for ordinal, (start, end, overlap) in enumerate(plan):
            digest = hashlib.sha256(
                f"{wav_path.resolve()}:{start}:{end}".encode("utf-8")
            ).hexdigest()[:20]
            chunk_path = output_dir / f"chunk-{ordinal:05d}-{digest}.wav"
            if not chunk_path.exists():
                temporary = chunk_path.with_suffix(".part.wav")
                source.setpos(start)
                remaining = end - start
                with wave.open(str(temporary), "wb") as target:
                    target.setnchannels(CHANNELS)
                    target.setsampwidth(SAMPLE_WIDTH)
                    target.setframerate(SAMPLE_RATE)
                    while remaining:
                        count = min(remaining, frames_per_read)
                        data = source.readframes(count)
                        if not data:
                            break
                        target.writeframesraw(data)
                        remaining -= len(data) // SAMPLE_WIDTH
                temporary.replace(chunk_path)
            chunks.append(
                AudioChunk(
                    stable_id=digest,
                    path=chunk_path,
                    start_sample=start,
                    end_sample=end,
                    overlap_before_samples=overlap,
                )
            )
    return chunks


def pcm_range_to_wav(
    pcm_path: Path, destination: Path, start_sample: int, end_sample: int
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".part.wav")
    with pcm_path.open("rb") as source, wave.open(str(temporary), "wb") as target:
        target.setnchannels(CHANNELS)
        target.setsampwidth(SAMPLE_WIDTH)
        target.setframerate(SAMPLE_RATE)
        source.seek(max(0, start_sample) * SAMPLE_WIDTH)
        remaining = max(0, end_sample - start_sample) * SAMPLE_WIDTH
        while remaining:
            data = source.read(min(remaining, SAMPLE_RATE * SAMPLE_WIDTH * 10))
            if not data:
                break
            target.writeframesraw(data)
            remaining -= len(data)
    temporary.replace(destination)
    return destination


def finalize_pcm_wav(pcm_path: Path, destination: Path) -> Path:
    samples = pcm_path.stat().st_size // SAMPLE_WIDTH
    return pcm_range_to_wav(pcm_path, destination, 0, samples)
