"""Hotkey name resolution: aliases, pynput names, single chars, errors."""

from __future__ import annotations

import pytest
from pynput import keyboard

from speako.audio.hotkey import _matches, _parse_key


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


def test_matches_option_key_family() -> None:
    target_option = keyboard.Key.alt
    # Pressing Left Option or Right Option or Alt matches option
    assert _matches(keyboard.Key.alt, target_option)
    assert _matches(keyboard.Key.alt_l, target_option)
    assert _matches(keyboard.Key.alt_r, target_option)
    assert not _matches(keyboard.Key.cmd, target_option)
    assert not _matches(None, target_option)


def test_matches_cmd_and_ctrl_families() -> None:
    assert _matches(keyboard.Key.cmd_l, keyboard.Key.cmd)
    assert _matches(keyboard.Key.cmd_r, keyboard.Key.cmd)
    assert _matches(keyboard.Key.ctrl_l, keyboard.Key.ctrl)
    assert _matches(keyboard.Key.ctrl_r, keyboard.Key.ctrl)
    assert _matches(keyboard.Key.shift_l, keyboard.Key.shift)
    assert _matches(keyboard.Key.shift_r, keyboard.Key.shift)
