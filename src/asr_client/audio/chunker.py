from __future__ import annotations

import hashlib
import wave
from pathlib import Path

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
    overlap_seconds: float = 10.0,
) -> list[tuple[int, int, int]]:
    _, _, _, total = _wav_shape(wav_path)
    target = max(60, min(600, int(target_seconds))) * SAMPLE_RATE
    overlap = max(0, min(target - 1, int(overlap_seconds * SAMPLE_RATE)))
    planned: list[tuple[int, int, int]] = []
    start = 0
    overlap_before = 0
    while start < total:
        end = min(total, start + target)
        planned.append((start, end, overlap_before))
        if end >= total:
            break
        next_start = end - overlap
        overlap_before = end - next_start
        start = next_start
    return planned


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
