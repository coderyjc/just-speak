from __future__ import annotations

import ctypes
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent


def _write_startup_log(report: str) -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    candidates = []
    if local_app_data:
        candidates.append(Path(local_app_data) / "JustSpeak" / "logs")
    candidates.append(PROJECT_DIR / "logs")
    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "startup-error.log"
            path.write_text(report, encoding="utf-8")
            return path
        except OSError:
            continue
    return None


def _show_error(message: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(0, message, "JustSpeak 启动失败", 0x10)
        return
    print(message, file=sys.stderr)


def run() -> int:
    try:
        from asr_client.app import main

        return main()
    except BaseException:
        report = (
            f"JustSpeak startup failure at {datetime.now().isoformat(timespec='seconds')}\n\n"
            f"{traceback.format_exc()}"
        )
        log_path = _write_startup_log(report)
        message = "JustSpeak 启动失败。"
        if log_path is not None:
            message += f"\n\n错误详情已写入：\n{log_path}"
        else:
            message += "\n\n错误日志也无法写入，请检查目录权限。"
        _show_error(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
