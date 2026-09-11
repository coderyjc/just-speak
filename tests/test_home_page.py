from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from asr_client.ui.pages import HomePage


def test_home_page_displays_all_usage_metrics() -> None:
    app = QApplication.instance() or QApplication([])
    page = HomePage()
    page.set_statistics(
        {
            "total_chars": 12_345,
            "realtime_chars": 8_000,
            "file_chars": 4_345,
            "realtime_seconds": 3_600,
            "file_seconds": 7_200,
            "total_seconds": 10_800,
            "cost_yuan": 3.564,
            "token_count": 98_765,
            "realtime_count": 20,
            "file_count": 4,
            "realtime_average_chars": 400,
            "realtime_last_7_days": 6,
            "longest_chars": 2_048,
            "peak_date": "2026-09-10",
            "peak_chars": 3_000,
            "active_days": 18,
            "daily_chars": {"2026-09-10": 3_000},
            "as_of_date": "2026-09-11",
        }
    )

    assert page.total_chars.text() == "12,345"
    assert page.total_meta.text() == "语音 3 小时 · 费用 ¥3.5640"
    assert page.realtime_chars.text() == "8,000 字"
    assert page.file_chars.text() == "4,345 字"
    assert page.realtime_meta.text() == "1 小时 · 20 条"
    assert page.file_meta.text() == "2 小时 · 4 个文件"
    assert page.metrics["longest"].value.text() == "2,048 字"
    assert page.metrics["peak"].value.text() == "09.10"
    assert page.metrics["peak"].detail.text() == "3,000 字"
    assert page.metrics["active"].value.text() == "18 天"
    assert page.metrics["realtime_count"].value.text() == "20 条"
    assert page.metrics["average"].value.text() == "400 字"
    assert page.metrics["recent"].value.text() == "6 条"
    assert page.token_badge.text() == "98,765 Token"
    assert not page.token_badge.isHidden()
    assert page.heatmap_range.text() == "12 个月 · 至 09.11"
    assert page.page_status.text() == "已统计 24 条有效文稿"
    page.set_statistics({})
    assert page.token_badge.isHidden()
    page.deleteLater()
    app.processEvents()
