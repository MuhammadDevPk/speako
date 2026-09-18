"""Hotkey name resolution: aliases, pynput names, single chars, errors."""

from __future__ import annotations

import pytest
from pynput import keyboard

from speako.audio.hotkey import _parse_key


def test_pynput_name_direct() -> None:
    assert _parse_key("alt_r") is keyboard.Key.alt_r
    assert _parse_key("f19") is keyboard.Key.f19
    assert _parse_key("caps_lock") is keyboard.Key.caps_lock


def test_alias_right_option_resolves_to_alt_r() -> None:
    assert _parse_key("right_option") is keyboard.Key.alt_r


def test_alias_right_cmd_resolves_to_cmd_r() -> None:
    assert _parse_key("right_cmd") is keyboard.Key.cmd_r


def test_alias_is_case_insensitive() -> None:
    assert _parse_key("Right_Option") is keyboard.Key.alt_r
    assert _parse_key("OPTION") is keyboard.Key.alt


def test_single_char_becomes_keycode() -> None:
    parsed = _parse_key("a")
    assert isinstance(parsed, keyboard.KeyCode)
    assert parsed.char == "a"


def test_unknown_name_raises_with_hint() -> None:
    with pytest.raises(ValueError, match="unknown hotkey"):
        _parse_key("nope_key_9000")
