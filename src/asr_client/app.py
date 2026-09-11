from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from asr_client.storage.config import ApiKeyStore, ConfigStore, app_config_dir
from asr_client.storage.database import Database
from asr_client.ui.main_window import MainWindow
from asr_client.ui.theme import APP_STYLESHEET


STYLE = APP_STYLESHEET


def _open_database(config_store: ConfigStore) -> tuple[Database, str]:
    config = config_store.load()
    requested = Path(config.data_dir)
    candidates = [requested, app_config_dir() / "data", Path.cwd() / "data"]
    failures: list[str] = []
    visited: set[str] = set()
    for candidate in candidates:
        identity = str(candidate.resolve())
        if identity in visited:
            continue
        visited.add(identity)
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            database = Database(candidate / "justspeak.sqlite3")
            if candidate == requested:
                return database, ""
            config.data_dir = str(candidate)
            warning = (
                f"无法使用原数据目录 {requested}。\n\n"
                f"本次已使用 {candidate}。可在设置中选择其他目录并保存。"
            )
            return database, warning
        except (OSError, sqlite3.Error) as exc:
            failures.append(f"{candidate}: {exc}")
    raise RuntimeError("无法创建应用数据库：\n" + "\n".join(failures))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("JustSpeak")
    app.setOrganizationName("JustSpeak")
    app.setWindowIcon(
        QIcon(str(Path(__file__).resolve().parent / "ui" / "assets" / "justspeak.svg"))
    )
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    config_store = ConfigStore()
    database, warning = _open_database(config_store)
    log_dir = app_config_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "justspeak.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    window = MainWindow(database, config_store, ApiKeyStore(), warning)
    window.show()
    try:
        return app.exec()
    finally:
        window.database.close()


if __name__ == "__main__":
    raise SystemExit(main())
