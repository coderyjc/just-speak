from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from asr_client.ui.pages import HistoryPage


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _stage(stage: str, text: str) -> dict[str, str]:
    return {"stage": stage, "text": text, "status": "completed"}


def test_loading_history_records_does_not_leak_previous_text() -> None:
    app = _app()
    page = HistoryPage()

    page.set_pipeline(
        "record1", "record1", [_stage("asr", "record1 内容")], "record1 内容", True
    )
    assert page.text.toPlainText() == "record1 内容"
    assert page.detail_panel.graphicsEffect() is not None

    page.set_pipeline(
        "record2", "record2", [_stage("asr", "record2 内容")], "record2 内容", True
    )
    assert page.text.toPlainText() == "record2 内容"

    page.set_pipeline(
        "record1", "record1", [_stage("asr", "record1 内容")], "record1 内容", True
    )
    assert page.text.toPlainText() == "record1 内容"
    page.deleteLater()
    app.processEvents()


def test_pipeline_navigation_keeps_unsaved_edit_within_same_record() -> None:
    app = _app()
    page = HistoryPage()
    page.set_pipeline(
        "record1",
        "record1",
        [_stage("asr", "原始文本"), _stage("polish", "修复文本")],
        "修复文本",
        True,
    )

    page.text.setPlainText("用户修改")
    page.pipeline.buttons["asr"].click()
    assert page.text.graphicsEffect() is not None
    page.pipeline.buttons["polish"].click()

    assert page.text.toPlainText() == "用户修改"
    page.deleteLater()
    app.processEvents()
