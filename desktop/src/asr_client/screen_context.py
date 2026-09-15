from __future__ import annotations

import base64
import math

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QImageWriter

from asr_client.models import MAX_TRANSCRIPTION_PROMPT_LENGTH


MAX_OCR_IMAGE_PIXELS = 15_000_000
MAX_OCR_IMAGE_BYTES = 19 * 1024 * 1024


def _fit_image(image: QImage, max_pixels: int = MAX_OCR_IMAGE_PIXELS) -> QImage:
    pixels = image.width() * image.height()
    if pixels <= max_pixels:
        return image
    scale = math.sqrt(max_pixels / pixels)
    return image.scaled(
        max(1, int(image.width() * scale)),
        max(1, int(image.height() * scale)),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _encode_image(image: QImage, image_format: bytes, quality: int = -1) -> bytes:
    output = QBuffer()
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("无法创建屏幕截图缓冲区")
    writer = QImageWriter(output, image_format)
    if quality >= 0:
        writer.setQuality(quality)
    if not writer.write(image):
        raise RuntimeError(f"屏幕截图编码失败：{writer.errorString()}")
    return bytes(output.data())


def capture_screen_data_url(screen_name: str = "") -> str:
    """截取选定显示器的完整画面，仅在内存中编码。"""
    app = QGuiApplication.instance()
    if app is None:
        raise RuntimeError("应用图形环境尚未就绪")
    requested = screen_name.strip()
    screen = next(
        (item for item in QGuiApplication.screens() if item.name() == requested),
        None,
    )
    screen = (
        screen
        or QGuiApplication.screenAt(QCursor.pos())
        or QGuiApplication.primaryScreen()
    )
    if screen is None:
        raise RuntimeError("没有找到可截取的显示器")
    pixmap = screen.grabWindow(0)
    if pixmap.isNull():
        raise RuntimeError("屏幕截图为空，请检查系统截屏权限")
    image = _fit_image(pixmap.toImage())
    encoded = _encode_image(image, b"PNG")
    mime = "image/png"
    if len(encoded) > MAX_OCR_IMAGE_BYTES:
        encoded = _encode_image(image, b"JPEG", 90)
        mime = "image/jpeg"
    if len(encoded) > MAX_OCR_IMAGE_BYTES:
        raise RuntimeError("屏幕截图超过 OCR 单图 20 MB 限制")
    return f"data:{mime};base64,{base64.b64encode(encoded).decode('ascii')}"


def capture_pointer_screen_data_url() -> str:
    """保留默认按鼠标位置选屏的调用入口。"""
    return capture_screen_data_url()


def merge_screen_context(
    prompt: str,
    ocr_text: str,
    ocr_max_chars: int,
    total_limit: int = MAX_TRANSCRIPTION_PROMPT_LENGTH,
) -> tuple[str, int]:
    """在 ASR 的 400 字符限制内优先保留用户原有提示词。"""
    base = prompt.strip()[:total_limit]
    normalized = " ".join(ocr_text.split())[: max(0, int(ocr_max_chars))]
    if not normalized:
        return base, 0
    prefix = "屏幕文字："
    separator = "\n" if base else ""
    available = total_limit - len(base) - len(separator) - len(prefix)
    if available <= 0:
        return base, 0
    included = normalized[:available]
    return f"{base}{separator}{prefix}{included}", len(included)
