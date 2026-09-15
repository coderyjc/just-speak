from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import asr_client.audio.ffmpeg as ffmpeg_module


def test_frozen_app_finds_bundled_ffmpeg(monkeypatch, tmp_path) -> None:
    bundle_root = tmp_path / "bundle"
    binary = bundle_root / "bin" / "ffmpeg.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    executable = tmp_path / "portable" / "JustSpeak.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert ffmpeg_module._candidate("", "ffmpeg") == str(binary)


def test_probe_lists_only_audio_tracks(monkeypatch, tmp_path) -> None:
    source = tmp_path / "movie.mkv"
    source.touch()
    payload = {
        "format": {"duration": "90.5"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "tags": {"language": "chi"},
            },
        ],
    }
    monkeypatch.setattr(ffmpeg_module, "find_ffmpeg", lambda _: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(
        ffmpeg_module,
        "_run",
        lambda args, timeout=None: SimpleNamespace(stdout=json.dumps(payload)),
    )
    info = ffmpeg_module.probe_audio(source)
    assert info.duration_seconds == 90.5
    assert len(info.tracks) == 1
    assert info.tracks[0].index == 1
    assert "48000 Hz" in info.tracks[0].label


def test_conversion_maps_selected_track_and_uses_atomic_replace(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "movie.mp4"
    source.touch()
    destination = tmp_path / "audio.wav"
    seen = []

    def fake_run(args, timeout=None):
        seen.extend(args)
        (tmp_path / "audio.part.wav").write_bytes(b"wave")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(ffmpeg_module, "find_ffmpeg", lambda _: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(ffmpeg_module, "_run", fake_run)
    ffmpeg_module.convert_to_standard_wav(source, destination, 3)
    assert ["-map", "0:3"] == seen[seen.index("-map") : seen.index("-map") + 2]
    assert "pcm_s16le" in seen
    assert destination.read_bytes() == b"wave"
