from __future__ import annotations

import argparse
import tempfile
import tracemalloc
from pathlib import Path

from asr_client.jobs.realtime_job import RealtimeJob
from asr_client.models import AppConfig, SAMPLE_RATE, SessionStatus
from asr_client.providers.base import MockAsrProvider
from asr_client.storage.database import Database


class SyntheticRealtimeJob(RealtimeJob):
    def __init__(self, *args, minutes: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.minutes = minutes

    def _capture(self) -> None:
        self._input_rate = SAMPLE_RATE
        packet = b"\x10\x00" * (SAMPLE_RATE // 10)
        for _ in range(self.minutes * 60 * 10):
            self._audio_queue.put(packet)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行无麦克风、无云调用的等长录音模拟")
    parser.add_argument("--minutes", type=int, default=60)
    args = parser.parse_args()
    if args.minutes <= 0:
        parser.error("--minutes 必须大于零")
    with tempfile.TemporaryDirectory(prefix="justspeak-long-run-") as temporary:
        root = Path(temporary)
        database = Database(root / "simulation.sqlite3")
        config = AppConfig(data_dir=str(root))
        tracemalloc.start()
        job = SyntheticRealtimeJob(
            database,
            MockAsrProvider(),
            config,
            minutes=args.minutes,
            stop_timeout=1,
        )
        job.run()
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        row = database.get_session(job.session_id)
        expected = args.minutes * 60 * SAMPLE_RATE
        actual = int(row["saved_samples"])
        status = row["status"]
        print(f"status={status}")
        print(f"expected_samples={expected}")
        print(f"saved_samples={actual}")
        print(f"python_peak_mib={peak / 1024 / 1024:.2f}")
        database.close()
        if status != SessionStatus.COMPLETED or actual != expected:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

