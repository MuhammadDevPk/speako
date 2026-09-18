"""Hud._apply / state transitions — exercised without opening a real Tk window.

We construct the Hud, then drive ``_apply`` and ``_transition`` directly
after stubbing out ``self._root`` / ``self._canvas``. This keeps the tests
headless (safe in CI) while still covering the state-machine logic.
"""

from __future__ import annotations

import queue
from typing import cast
from unittest.mock import MagicMock

from speako.config.models import UIConfig
from speako.ui.events import HudEvent, HudLevelEvent, HudShutdown, HudState, HudStateEvent
from speako.ui.hud import Hud


def _hud() -> Hud:
    cfg = UIConfig(enabled=True, position="bottom_center", margin_px=80, opacity=0.9)
    events: queue.Queue[HudEvent] = queue.Queue()
    hud = Hud(cfg, events)
    # Stub Tk internals so _transition can call withdraw/deiconify/lift.
    hud._root = cast("object", MagicMock())  # type: ignore[assignment]
    hud._canvas = cast("object", MagicMock())  # type: ignore[assignment]
    return hud


def test_state_event_shows_via_alpha() -> None:
    hud = _hud()
    hud._apply(HudStateEvent(HudState.LISTENING))
    assert hud._hud_state is HudState.LISTENING
    # Show is alpha-only: it must NOT withdraw/deiconify/lift, because
    # those steal focus and cross macOS Space boundaries.
    root_mock = cast("MagicMock", hud._root)
    root_mock.attributes.assert_any_call("-alpha", 0.9)
    assert not root_mock.deiconify.called
    assert not root_mock.lift.called
    assert not root_mock.withdraw.called


def test_hidden_state_hides_via_alpha_zero() -> None:
    hud = _hud()
    hud._apply(HudStateEvent(HudState.LISTENING))
    hud._apply(HudStateEvent(HudState.HIDDEN))
    assert hud._hud_state is HudState.HIDDEN
    root_mock = cast("MagicMock", hud._root)
    root_mock.attributes.assert_any_call("-alpha", 0.0)
    assert not root_mock.withdraw.called


def test_level_event_clamps_and_stores() -> None:
    hud = _hud()
    hud._apply(HudLevelEvent(level=2.0))
    assert hud._current_level == 1.0
    hud._apply(HudLevelEvent(level=-0.4))
    assert hud._current_level == 0.0
    hud._apply(HudLevelEvent(level=0.42))
    assert abs(hud._current_level - 0.42) < 1e-9


def test_pasted_transition_starts_hold_timer() -> None:
    hud = _hud()
    hud._apply(HudStateEvent(HudState.LISTENING))
    hud._apply(HudStateEvent(HudState.PASTED))
    assert hud._hud_state is HudState.PASTED
    assert hud._pasted_started_ms > 0


def test_shutdown_event_requests_teardown() -> None:
    hud = _hud()
    assert hud._apply(HudShutdown()) is True
    # Non-shutdown events do not.
    assert hud._apply(HudLevelEvent(level=0.1)) is False
    assert hud._apply(HudStateEvent(HudState.LISTENING)) is False
