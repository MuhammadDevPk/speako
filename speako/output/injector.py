"""Cross-platform text insertion.

Two strategies:
    * ``paste`` — write to clipboard, then send the OS paste chord
      (Cmd+V on macOS, Ctrl+V elsewhere). Fast and preserves formatting.
    * ``type``  — synthesize keystrokes one character at a time. Slower,
      but works in apps that block programmatic paste (some sandboxed
      inputs, browser password fields, VMs).

macOS requires the process to have Accessibility permission for
``pynput.keyboard.Controller`` to send keystrokes; the first failure
surfaces a clear message.
"""

from __future__ import annotations

import platform
import time
from typing import Final

import pyperclip
from pynput.keyboard import Controller, Key

from ..config import OutputConfig
from ..util.logging import get_logger
from ..util.notify import notify

_log: Final = get_logger(__name__)


class OutputInjector:
    def __init__(self, config: OutputConfig) -> None:
        self._config = config
        self._keyboard = Controller()
        self._paste_modifier: Key = (
            Key.cmd if platform.system() == "Darwin" else Key.ctrl
        )

    def insert(self, text: str) -> None:
        if not text:
            return
        if self._config.method == "paste":
            self._paste(text)
        else:
            self._type(text)

    # ------------------------------------------------------------------
    # Strategies
    # ------------------------------------------------------------------

    def _paste(self, text: str) -> None:
        previous: str | None = None
        if self._config.restore_clipboard:
            try:
                previous = pyperclip.paste()
            except pyperclip.PyperclipException as exc:
                _log.warning("clipboard_read_failed", error=str(exc))

        try:
            pyperclip.copy(text)
        except pyperclip.PyperclipException as exc:
            _log.error("clipboard_write_failed", error=str(exc))
            notify("speako", "Failed to write to clipboard.")
            return

        # Tiny yield so the OS reflects the new clipboard before we paste.
        time.sleep(0.01)
        try:
            with self._keyboard.pressed(self._paste_modifier):
                self._keyboard.press("v")
                self._keyboard.release("v")
        except Exception as exc:  # pynput surfaces platform errors here
            _log.error("paste_failed", error=str(exc))
            notify(
                "speako",
                "Paste failed — check Accessibility permissions.",
            )
            return

        if previous is not None:
            # Give the target app a moment to consume the paste event.
            time.sleep(0.05)
            try:
                pyperclip.copy(previous)
            except pyperclip.PyperclipException as exc:
                _log.warning("clipboard_restore_failed", error=str(exc))

    def _type(self, text: str) -> None:
        try:
            self._keyboard.type(text)
        except Exception as exc:
            _log.error("type_failed", error=str(exc))
            notify(
                "speako",
                "Typing failed — check Accessibility permissions.",
            )
