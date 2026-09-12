from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication, QIcon


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: generate_icon.py <source.svg> <destination.ico>")
        return 2
    source = Path(sys.argv[1]).resolve()
    destination = Path(sys.argv[2]).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication([])
    pixmap = QIcon(str(source)).pixmap(QSize(256, 256))
    if pixmap.isNull() or not pixmap.save(str(destination), "ICO"):
        print(f"unable to generate icon: {destination}")
        return 1
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
