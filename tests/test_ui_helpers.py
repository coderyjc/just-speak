from __future__ import annotations

from asr_client.ui.main_window import _format_history_time


def test_history_time_is_displayed_in_utc_plus_eight() -> None:
    assert _format_history_time("2026-09-11T00:15:00+00:00") == "09-11 08:15"
