from __future__ import annotations

import argparse
import tempfile
import tracemalloc
import wave
from pathlib import Path

from asr_client.audio.chunker import write_chunks
from asr_client.jobs.file_job import FileTranscriptionJob
from asr_client.models import AppConfig, SAMPLE_RATE, SessionStatus
from asr_client.providers.base import MockAsrProvider
from asr_client.storage.database import Database


def write_silence(path: Path, minutes: int) -> None:
    one_second = b"\x00\x00" * SAMPLE_RATE
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        for _ in range(minutes * 60):
            output.writeframesraw(one_second)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行无云调用的等长文件分段模拟")
    parser.add_argument("--minutes", type=int, default=60)
    args = parser.parse_args()
    if args.minutes <= 0:
        parser.error("--minutes 必须大于零")
    with tempfile.TemporaryDirectory(prefix="justspeak-long-file-") as temporary:
        root = Path(temporary)
        task_dir = root / "task"
        task_dir.mkdir()
        source = task_dir / "audio.wav"
        write_silence(source, args.minutes)
        tracemalloc.start()
        chunks = write_chunks(source, task_dir / "chunks", target_seconds=60)
        database = Database(root / "simulation.sqlite3")
        config = AppConfig(data_dir=str(root))
        session = database.create_session(
            "file", "synthetic.wav", task_dir, config.snapshot(), str(source)
        )
        database.add_units(session, "file_chunk", chunks)
        job = FileTranscriptionJob(
            database,
            MockAsrProvider(),
            config,
            source,
            0,
            session_id=session,
        )
        job.run()
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        row = database.get_session(session)
        expected = args.minutes * 60 * SAMPLE_RATE
        covered = chunks[-1].end_sample if chunks else 0
        print(f"status={row['status']}")
        print(f"chunks={len(chunks)}")
        print(f"expected_samples={expected}")
        print(f"covered_samples={covered}")
        print(f"python_peak_mib={peak / 1024 / 1024:.2f}")
        database.close()
        if row["status"] != SessionStatus.COMPLETED or covered != expected:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

