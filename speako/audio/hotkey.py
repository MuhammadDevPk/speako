"""Global hotkey listener built on ``pynput``.

Publishes ``RecordEvent`` values onto a queue. The pynput callback thread
does nothing but resolve the event and call ``put_nowait``; no I/O, no
locking, no sleeps.

Two modes:
    * ``hold``   — PRESS on key-down, RELEASE on key-up.
    * ``toggle`` — first tap emits PRESS, next tap emits RELEASE.
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from enum import Enum
from typing import Final

from pynput import keyboard

from ..config import HotkeyConfig
from ..util.logging import get_logger

_log: Final = get_logger(__name__)

# Friendly aliases → canonical pynput.keyboard.Key names. macOS users
# naturally write "right_option" / "right_cmd"; pynput calls those
# "alt_r" / "cmd_r". Add lowercase, underscore-form entries only.
_KEY_ALIASES: Final[dict[str, str]] = {
    "option": "alt",
    "left_option": "alt_l",
    "right_option": "alt_r",
    "left_cmd": "cmd_l",
    "right_cmd": "cmd_r",
    "left_command": "cmd_l",
    "right_command": "cmd_r",
    "command": "cmd",
    "left_control": "ctrl_l",
    "right_control": "ctrl_r",
    "control": "ctrl",
    "left_ctrl": "ctrl_l",
    "right_ctrl": "ctrl_r",
    "left_shift": "shift_l",
    "right_shift": "shift_r",
    "left_alt": "alt_l",
    "right_alt": "alt_r",
    "return": "enter",
    "escape": "esc",
}


class RecordEventKind(Enum):
    PRESS = "press"
    RELEASE = "release"


@dataclass(frozen=True, slots=True)
class RecordEvent:
    kind: RecordEventKind
    at_monotonic_ns: int


class HotkeyListener:
    """Listens for a single configured key and publishes press/release events.

    Args:
        config: hotkey config (key name and mode).
        sink: bounded queue that receives ``RecordEvent`` values. Writes are
            ``put_nowait`` — if the consumer is slow, events are dropped
            with a warning rather than blocking the pynput thread.
    """

    def __init__(self, config: HotkeyConfig, sink: queue.Queue[RecordEvent]) -> None:
        self._config = config
        self._sink = sink
        self._target = _parse_key(config.key)
        self._listener: keyboard.Listener | None = None
        self._toggle_state: bool = False  # only used in "toggle" mode
        self._pressed: bool = False  # de-bounce repeat events in "hold" mode

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=False,
        )
        self._listener.start()
        _log.info("hotkey_started", key=self._config.key, mode=self._config.mode)

    def stop(self) -> None:
        if self._listener is None:
            return
        try:
            self._listener.stop()
        finally:
            self._listener = None
        _log.info("hotkey_stopped")

    # ------------------------------------------------------------------
    # pynput callbacks — must be fast; never raise.
    # ------------------------------------------------------------------

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        if not _matches(key, self._target):
            return
        if self._config.mode == "hold":
            if self._pressed:
                return  # ignore OS auto-repeat
            self._pressed = True
            self._emit(RecordEventKind.PRESS)
        else:  # toggle: only care about key-down transitions
            if self._pressed:
                return
            self._pressed = True
            self._toggle_state = not self._toggle_state
            self._emit(
                RecordEventKind.PRESS if self._toggle_state else RecordEventKind.RELEASE
            )

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        if not _matches(key, self._target):
            return
        self._pressed = False
        if self._config.mode == "hold":
            self._emit(RecordEventKind.RELEASE)

    def _emit(self, kind: RecordEventKind) -> None:
        event = RecordEvent(kind=kind, at_monotonic_ns=time.monotonic_ns())
        try:
            self._sink.put_nowait(event)
        except queue.Full:
            _log.warning("hotkey_event_dropped", kind=kind.value)


def _parse_key(name: str) -> keyboard.Key | keyboard.KeyCode:
    """Resolve a config key name to a pynput target.

    Accepts:
        - Named ``pynput.keyboard.Key`` values ("alt_r", "f19", ...)
        - Friendly aliases ("right_option", "right_cmd", "option", ...)
        - Single-character literals ("a", "'")
    """
    lowered = name.strip().lower()
    resolved = _KEY_ALIASES.get(lowered, lowered)
    named = getattr(keyboard.Key, resolved, None)
    if isinstance(named, keyboard.Key):
        return named
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(
        f"unknown hotkey name: {name!r}. "
        "Try a pynput Key name (e.g. 'alt_r', 'f19') or an alias "
        "('right_option', 'right_cmd', 'right_ctrl')."
    )


def _matches(
    key: keyboard.Key | keyboard.KeyCode | None,
    target: keyboard.Key | keyboard.KeyCode,
) -> bool:
    if key is None:
        return False
    if isinstance(target, keyboard.Key):
        return bool(key == target)
    if isinstance(key, keyboard.KeyCode) and isinstance(target, keyboard.KeyCode):
        return bool(key.char == target.char)
    return False
