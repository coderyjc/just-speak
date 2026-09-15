from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication

from asr_client.screen_context import (
    _encode_image,
    capture_screen_data_url,
    merge_screen_context,
)


def test_qt_image_can_be_encoded_for_ocr() -> None:
    image = QImage(24, 16, QImage.Format.Format_RGB32)
    image.fill(0xFFFAF7)

    encoded = _encode_image(image, b"PNG")

    assert encoded.startswith(b"\x89PNG\r\n\x1a\n")


def test_named_screen_can_be_captured() -> None:
    app = QApplication.instance() or QApplication([])
    screen = QGuiApplication.primaryScreen()
    assert screen is not None

    encoded = capture_screen_data_url(screen.name())

    assert encoded.startswith("data:image/png;base64,")
    app.processEvents()


def test_screen_text_is_normalized_and_appended_to_prompt() -> None:
    merged, included = merge_screen_context(
        "JustSpeak、Aurora", "  项目\n会议   客户万里  ", 100
    )

    assert merged == "JustSpeak、Aurora\n屏幕文字：项目 会议 客户万里"
    assert included == len("项目 会议 客户万里")


def test_existing_prompt_has_priority_within_asr_limit() -> None:
    merged, included = merge_screen_context("术" * 398, "屏幕内容", 300)

    assert merged == "术" * 398
    assert included == 0


def test_ocr_context_honors_its_own_limit() -> None:
    merged, included = merge_screen_context("", "123456789", 5, total_limit=400)

    assert merged == "屏幕文字：12345"
    assert included == 5
