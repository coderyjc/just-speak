from __future__ import annotations

import os
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from asr_client.single_instance import SingleInstanceGuard


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_second_instance_notifies_primary_and_exits() -> None:
    app = _app()
    name = f"JustSpeak.Tests.{uuid.uuid4().hex}"
    primary = SingleInstanceGuard(name)
    secondary = SingleInstanceGuard(name)
    activations = []
    primary.activation_requested.connect(lambda: activations.append(True))

    try:
        assert primary.acquire()
        assert not secondary.acquire()
        for _ in range(5):
            app.processEvents()
        assert activations == [True]
    finally:
        secondary.close()
        primary.close()
        secondary.deleteLater()
        primary.deleteLater()
        app.processEvents()
