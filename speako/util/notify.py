"""Lightweight, non-focus-stealing user notifications.

Falls back silently to a log line when no OS-native mechanism is available.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Final

from .logging import get_logger

_log: Final = get_logger(__name__)


def notify(title: str, message: str) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            _notify_macos(title, message)
        elif system == "Linux" and shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", title, message],
                check=False,
                timeout=2,
            )
        else:
            _log.info("notify", title=title, message=message)
    except (subprocess.SubprocessError, OSError) as exc:
        _log.warning("notify_failed", title=title, error=str(exc))


def _notify_macos(title: str, message: str) -> None:
    safe_title = title.replace('"', "'")
    safe_msg = message.replace('"', "'")
    script = f'display notification "{safe_msg}" with title "{safe_title}"'
    subprocess.run(["osascript", "-e", script], check=False, timeout=2)
