from __future__ import annotations

import wave

from asr_client.audio.chunker import plan_chunks, write_chunks
from asr_client.audio.resample import StatefulResampler
from asr_client.jobs.realtime_job import recover_recording_metadata
from asr_client.models import SAMPLE_RATE


def make_wav(path, seconds: int, value: int = 1000) -> None:
    frame = int(value).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        for _ in range(seconds):
            output.writeframesraw(frame * SAMPLE_RATE)


def test_forced_chunks_have_overlap_and_exact_ranges(tmp_path) -> None:
    source = tmp_path / "long.wav"
    make_wav(source, 125)
    plan = plan_chunks(source, target_seconds=60, search_seconds=1)
    assert plan == [
        (0, 60 * SAMPLE_RATE, 0),
        (50 * SAMPLE_RATE, 110 * SAMPLE_RATE, 10 * SAMPLE_RATE),
        (100 * SAMPLE_RATE, 125 * SAMPLE_RATE, 10 * SAMPLE_RATE),
    ]
    chunks = write_chunks(source, tmp_path / "chunks", target_seconds=60)
    assert chunks[0].path.exists()
    with wave.open(str(chunks[-1].path), "rb") as audio:
        assert audio.getframerate() == SAMPLE_RATE


def test_four_minute_chunk_is_not_clamped_to_two_minutes(tmp_path) -> None:
    source = tmp_path / "four-minute-setting.wav"
    make_wav(source, 250)
    plan = plan_chunks(source, target_seconds=4 * 60)
    assert plan[0] == (0, 240 * SAMPLE_RATE, 0)
    assert plan[1] == (
        230 * SAMPLE_RATE,
        250 * SAMPLE_RATE,
        10 * SAMPLE_RATE,
    )


def test_stateful_resampler_preserves_duration() -> None:
    for input_rate in (44_100, 48_000):
        resampler = StatefulResampler(input_rate)
        source = b"\x01\x00" * (input_rate // 10)
        output = b"".join(resampler.process(source) for _ in range(10))
        assert abs(len(output) // 2 - SAMPLE_RATE) <= 1


def test_recover_metadata_uses_complete_samples(tmp_path) -> None:
    pcm = tmp_path / "recording.pcm"
    pcm.write_bytes(b"\x00" * 321)
    metadata = recover_recording_metadata(pcm)
    assert metadata["saved_samples"] == 160
