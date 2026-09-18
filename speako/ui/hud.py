"""Floating HUD overlay (Tkinter).

Tkinter must be created and driven on the main thread — mandatory on
macOS because ``Tk()`` under the hood calls into Cocoa, which rejects
non-main-thread access. ``Hud.run()`` is therefore blocking; run the
rest of the app on a worker thread and let the worker signal back
through the ``events`` queue passed to ``Hud``.

Design choices:

* A single ``queue.Queue`` carries three kinds of events (state, level,
  shutdown). Producers never block; overflow drops the oldest reading.
* One Tk timer ticks at ``_FRAME_MS`` (≈30 fps) and drains the queue,
  runs animation frames, and re-schedules itself.
* Rendering is Canvas-only — no per-pixel transparency needed. The
  window uses whole-window alpha (``wm_attributes -alpha``) which is
  supported on macOS, Windows, and most Linux compositors.
* Show / hide toggles alpha only — we never ``withdraw`` / ``deiconify``.
  Rationale: on macOS ``deiconify`` triggers window activation which
  steals focus from the frontmost app (breaking paste) and switches
  Spaces if the HUD was created on a different one. Alpha-based hiding
  is invisible to the window server and preserves the user's Space.
* On macOS we call into ``pyobjc`` (available as a transitive pynput
  dep) to set the underlying ``NSWindow`` collectionBehavior so the
  HUD appears on every Space and over fullscreen apps, and to enable
  ``ignoresMouseEvents`` so clicks pass through to whatever is below.
"""

from __future__ import annotations

import contextlib
import math
import platform
import queue
import time
import tkinter as tk
from typing import Any, Final

from ..config import UIConfig
from ..util.logging import get_logger
from .events import HudEvent, HudLevelEvent, HudState, HudStateEvent

_log: Final = get_logger(__name__)

_WIDTH: Final[int] = 240
_HEIGHT: Final[int] = 64
_CORNER_RADIUS: Final[int] = 26
_FRAME_MS: Final[int] = 33                # ~30 fps
_MAX_DRAIN_PER_TICK: Final[int] = 32
_PASTED_HOLD_MS: Final[int] = 400         # visible → fade → hidden

_BG: Final[str] = "#0f0f12"
_PILL: Final[str] = "#1c1c22"
_PILL_HI: Final[str] = "#26262e"
_REC_RED: Final[str] = "#ff3b30"
_REC_RED_GLOW: Final[str] = "#5a1414"
_BAR_LO: Final[str] = "#ff5a4d"
_BAR_HI: Final[str] = "#ffb0a8"
_SPIN_COLOR: Final[str] = "#7c4dff"
_SPIN_GLOW: Final[str] = "#3a1e6b"
_CHECK_COLOR: Final[str] = "#34c759"
_CHECK_GLOW: Final[str] = "#153f21"
_TEXT_MUTED: Final[str] = "#d0d0d8"

_BAR_COUNT: Final[int] = 5
_BAR_WIDTH: Final[int] = 6
_BAR_GAP: Final[int] = 8
_BAR_MIN_H: Final[float] = 4.0
_BAR_MAX_H: Final[float] = 38.0

# Match the unique window title so we can locate the NSWindow via NSApp.windows().
_WINDOW_TITLE: Final[str] = "__speako_hud__"


class HudUnavailableError(RuntimeError):
    """The Tk runtime is missing or broken on this Python.

    Common causes:
      * ``uv``'s bundled cpython omits Tcl/Tk libraries — either use system
        Python (`brew install python-tk`) or disable the HUD (`--no-hud`).
      * A headless environment (SSH without X11 forwarding) — set
        ``ui.enabled: false``.
    """


class Hud:
    """Frameless topmost pill overlay.

    ``events`` is the single producer→consumer channel. The worker thread
    posts ``HudStateEvent`` when the app transitions states, the audio
    callback posts ``HudLevelEvent`` roughly once per audio chunk, and
    the entrypoint posts ``HudShutdown`` when it's time to quit.
    """

    def __init__(self, config: UIConfig, events: queue.Queue[HudEvent]) -> None:
        self._config = config
        self._events = events
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None

        # Live UI state (owned by the Tk thread once run() is called).
        self._hud_state: HudState = HudState.HIDDEN
        self._bar_values: list[float] = [0.0] * _BAR_COUNT
        self._bar_phase: list[float] = [i * 0.7 for i in range(_BAR_COUNT)]
        self._current_level: float = 0.0
        self._spin_angle: float = 0.0
        self._pasted_started_ms: int = 0
        self._frame_index: int = 0

    # ------------------------------------------------------------------
    # Public API — main thread only.
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Block until a ``HudShutdown`` is received (or Tk closes)."""
        try:
            self._root = tk.Tk()
        except tk.TclError as exc:
            raise HudUnavailableError(
                f"Tk failed to initialize: {exc}. "
                "The HUD needs a working Tcl/Tk runtime. "
                "Options: (1) install system Tk (macOS: `brew install python-tk`; "
                "Debian/Ubuntu: `sudo apt install python3-tk`), "
                "(2) run with `--no-hud`, "
                "or (3) set `ui.enabled: false` in config."
            ) from exc
        self._configure_window(self._root)
        self._canvas = tk.Canvas(
            self._root,
            width=_WIDTH,
            height=_HEIGHT,
            bg=_BG,
            highlightthickness=0,
            borderwidth=0,
        )
        self._canvas.pack()
        self._position(self._root)

        # The window starts fully transparent — it exists in the window
        # server but is invisible. State transitions modulate alpha only,
        # so we never trigger the focus-stealing withdraw/deiconify cycle
        # or the Space-switch that goes with it. ``update()`` forces the
        # underlying NSWindow to be created so our pyobjc lookup can find it.
        with contextlib.suppress(tk.TclError):
            self._root.attributes("-alpha", 0.0)
        self._root.update_idletasks()
        self._root.update()

        _apply_platform_hud_behavior(self._root)

        self._root.after(_FRAME_MS, self._tick)
        try:
            self._root.mainloop()
        finally:
            self._root = None
            self._canvas = None
            _log.info("hud_closed")

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _configure_window(self, root: tk.Tk) -> None:
        # Unique title so ``_apply_platform_hud_behavior`` can find the
        # underlying NSWindow via ``NSApp.windows()``. Never shown because
        # ``overrideredirect(True)`` removes the title bar entirely.
        root.title(_WINDOW_TITLE)
        root.overrideredirect(True)
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            _log.warning("hud_topmost_unsupported")
        # macOS: request a floating utility class that doesn't activate.
        with contextlib.suppress(tk.TclError):
            root.tk.call(
                "::tk::unsupported::MacWindowStyle", "style", root, "help", "noActivates"
            )
        # Linux (X11): "dock" type keeps the window above others without
        # entering the taskbar or accepting focus. Wayland compositors
        # generally ignore this — Wayland users get best-effort behavior.
        if platform.system() == "Linux":
            with contextlib.suppress(tk.TclError):
                root.attributes("-type", "dock")

    def _position(self, root: tk.Tk) -> None:
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - _WIDTH) // 2
        if self._config.position == "top_center":
            y = self._config.margin_px
        else:
            y = sh - _HEIGHT - self._config.margin_px
        root.geometry(f"{_WIDTH}x{_HEIGHT}+{x}+{y}")

    # ------------------------------------------------------------------
    # Event pump + animation loop
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        if self._root is None:
            return

        # Drain the event bus. Level events are frequent — bound the drain
        # so a flood cannot starve the animation frame.
        shutdown = False
        drained = 0
        while drained < _MAX_DRAIN_PER_TICK:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            drained += 1
            shutdown = self._apply(event) or shutdown

        # PASTED auto-fades and returns to HIDDEN after the hold window.
        if self._hud_state is HudState.PASTED:
            elapsed_ms = _now_ms() - self._pasted_started_ms
            if elapsed_ms >= _PASTED_HOLD_MS:
                self._transition(HudState.HIDDEN)

        self._frame_index += 1
        if self._hud_state is not HudState.HIDDEN:
            self._render()

        if shutdown:
            self._destroy_root()
            return
        self._root.after(_FRAME_MS, self._tick)

    def _apply(self, event: HudEvent) -> bool:
        """Return True to request shutdown after this drain pass."""
        if isinstance(event, HudStateEvent):
            self._transition(event.state)
            return False
        if isinstance(event, HudLevelEvent):
            self._current_level = max(0.0, min(1.0, event.level))
            return False
        # HudEvent is a closed union — the remaining case is HudShutdown.
        return True

    def _transition(self, target: HudState) -> None:
        if target is self._hud_state:
            return
        self._hud_state = target
        assert self._root is not None
        # Alpha-only visibility. Never withdraw/deiconify and never lift() —
        # those would (a) steal focus from the app the user is typing into
        # and (b) drag the current macOS Space back to wherever the HUD lives.
        alpha = 0.0 if target is HudState.HIDDEN else self._config.opacity
        with contextlib.suppress(tk.TclError):
            self._root.attributes("-alpha", alpha)
        if target is HudState.PASTED:
            self._pasted_started_ms = _now_ms()

    def _destroy_root(self) -> None:
        assert self._root is not None
        with contextlib.suppress(tk.TclError):
            self._root.destroy()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self) -> None:
        assert self._canvas is not None
        c = self._canvas
        c.delete("all")
        _draw_rounded_rect(c, 2, 2, _WIDTH - 2, _HEIGHT - 2, _CORNER_RADIUS, _PILL_HI)
        _draw_rounded_rect(c, 4, 4, _WIDTH - 4, _HEIGHT - 4, _CORNER_RADIUS - 2, _PILL)

        if self._hud_state is HudState.LISTENING:
            self._render_listening(c)
        elif self._hud_state is HudState.TRANSCRIBING:
            self._render_transcribing(c)
        elif self._hud_state is HudState.PASTED:
            self._render_pasted(c)

    def _render_listening(self, c: tk.Canvas) -> None:
        # Glowing red dot on the left.
        cx, cy = 26, _HEIGHT // 2
        pulse = 0.5 + 0.5 * math.sin(self._frame_index * 0.18)
        glow_r = 12 + 3 * pulse
        c.create_oval(cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
                      fill=_REC_RED_GLOW, outline="")
        c.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill=_REC_RED, outline="")

        # Five dancing bars driven by RMS level + per-bar sinusoidal phase.
        self._advance_bars()
        total_w = _BAR_COUNT * _BAR_WIDTH + (_BAR_COUNT - 1) * _BAR_GAP
        x0 = _WIDTH - 20 - total_w
        cy = _HEIGHT // 2
        for i, v in enumerate(self._bar_values):
            h = _BAR_MIN_H + v * (_BAR_MAX_H - _BAR_MIN_H)
            x = x0 + i * (_BAR_WIDTH + _BAR_GAP)
            y1 = cy - h / 2
            y2 = cy + h / 2
            color = _interpolate_color(_BAR_LO, _BAR_HI, v)
            _draw_rounded_rect(c, int(x), int(y1), int(x + _BAR_WIDTH), int(y2), 3, color)

    def _advance_bars(self) -> None:
        base = self._current_level
        # Introduce per-bar phase + a slight envelope so bars don't lockstep.
        for i in range(_BAR_COUNT):
            self._bar_phase[i] += 0.35 + 0.05 * i
            wobble = 0.5 + 0.5 * math.sin(self._bar_phase[i])
            envelope = 0.55 + 0.45 * wobble
            target = base * envelope
            # Fast attack, slower release.
            alpha = 0.55 if target > self._bar_values[i] else 0.25
            self._bar_values[i] += alpha * (target - self._bar_values[i])

    def _render_transcribing(self, c: tk.Canvas) -> None:
        # Central spinner: rotating arc segment with a soft halo.
        cx, cy = _WIDTH // 2, _HEIGHT // 2
        r = 18
        halo_r = 22
        breath = 0.5 + 0.5 * math.sin(self._frame_index * 0.14)
        c.create_oval(cx - halo_r - breath, cy - halo_r - breath,
                      cx + halo_r + breath, cy + halo_r + breath,
                      fill=_SPIN_GLOW, outline="")
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=_PILL_HI, width=3)
        self._spin_angle = (self._spin_angle + 12) % 360
        c.create_arc(cx - r, cy - r, cx + r, cy + r,
                     start=self._spin_angle, extent=110,
                     style=tk.ARC, outline=_SPIN_COLOR, width=4)
        c.create_text(cx, cy + 26, text="transcribing", fill=_TEXT_MUTED,
                      font=("Helvetica", 9))

    def _render_pasted(self, c: tk.Canvas) -> None:
        cx, cy = _WIDTH // 2, _HEIGHT // 2
        elapsed = _now_ms() - self._pasted_started_ms
        # Ease-in glow, ease-out fade — cheap symmetric bell.
        t = min(1.0, elapsed / _PASTED_HOLD_MS)
        opacity_boost = 1.0 - abs(2.0 * t - 1.0)
        halo_r = 20 + 6 * opacity_boost
        c.create_oval(cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r,
                      fill=_CHECK_GLOW, outline="")
        c.create_oval(cx - 16, cy - 16, cx + 16, cy + 16,
                      outline=_CHECK_COLOR, width=2)
        # Check mark: two strokes.
        c.create_line(cx - 8, cy + 1, cx - 2, cy + 7, fill=_CHECK_COLOR, width=3,
                      capstyle=tk.ROUND)
        c.create_line(cx - 2, cy + 7, cx + 9, cy - 6, fill=_CHECK_COLOR, width=3,
                      capstyle=tk.ROUND)


# ---------------------------------------------------------------------------
# Platform-native HUD behavior
# ---------------------------------------------------------------------------


def _apply_platform_hud_behavior(root: tk.Tk) -> None:
    """Configure the underlying OS window as a proper HUD.

    On macOS this does two things:

    1. **Downgrades the entire Python process** to ``Accessory`` via
       ``NSApplication.setActivationPolicy_``. Without this, the process
       is a ``Regular`` GUI app (Cocoa upgrades any Tk program to
       Regular the moment ``Tk()`` runs) and macOS treats every HUD
       update as a foreground-app event that must occur on the app's
       origin Space — dragging the user back. ``Accessory`` = background
       agent: no Dock icon, no Cmd-Tab entry, no Space bindings.
    2. **Sets ``NSWindow`` collectionBehavior + level + ignoresMouseEvents**
       via ``pyobjc`` (installed transitively via pynput) so the HUD
       joins every Space, floats over fullscreen apps, and passes
       clicks through to whatever is below.

    The activation-policy change is applied *before* the window config
    because it affects the whole process; the window config only
    applies to our one NSWindow.

    On other platforms this is a no-op — Linux/Windows already get the
    right behavior from ``overrideredirect`` + ``-topmost`` + ``-type``.
    """
    if platform.system() != "Darwin":
        return

    try:
        from AppKit import (
            NSApp,
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSPopUpMenuWindowLevel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorStationary,
        )
    except ImportError:
        _log.warning("pyobjc_missing_hud_will_not_follow_spaces")
        return

    # Step 1 — process-wide: become a background agent so macOS stops
    # yanking the current Space back to the HUD's origin on every update.
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    resolved_policy = int(app.activationPolicy())

    # Step 2 — window-specific: join every Space, sit above every other
    # app's windows *including their fullscreen windows*.
    #
    # Level choice: NSStatusWindowLevel (25) is enough for regular windows,
    # but macOS puts another app's fullscreen Space above status level.
    # NSPopUpMenuWindowLevel (101) is the standard trick used by menu-bar
    # utilities (Rectangle, Bartender, Karabiner HUD, ...) to render over
    # any other app's fullscreen window. Going higher (screensaver = 1000)
    # would also cover the system menu bar, which we don't want.
    ns_window = _find_ns_window(NSApp, _WINDOW_TITLE)
    if ns_window is None:
        _log.warning("hud_ns_window_not_found", activation_policy=resolved_policy)
        return

    behavior = (
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorIgnoresCycle
        | NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    ns_window.setCollectionBehavior_(behavior)
    ns_window.setLevel_(NSPopUpMenuWindowLevel)
    ns_window.setIgnoresMouseEvents_(True)
    _log.info(
        "hud_macos_configured",
        activation_policy=resolved_policy,   # 1 = Accessory, 0 = Regular, 2 = Prohibited
        level=int(ns_window.level()),
        behavior=int(behavior),
    )


def _find_ns_window(ns_app: Any, title: str) -> Any:
    # NSApp / NSWindow are ObjC proxies whose methods are resolved at
    # runtime by pyobjc — no static types. Any is honest here.
    for window in ns_app.windows():
        if window.title() == title:
            return window
    return None


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def _draw_rounded_rect(
    c: tk.Canvas,
    x1: int, y1: int, x2: int, y2: int,
    r: int, fill: str,
) -> None:
    """Compose a solid rounded rectangle out of two rectangles + four pies."""
    r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    if r == 0:
        c.create_rectangle(x1, y1, x2, y2, fill=fill, outline=fill)
        return
    c.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill)
    c.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill)
    c.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r,
                 start=90, extent=90, fill=fill, outline=fill, style=tk.PIESLICE)
    c.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r,
                 start=0, extent=90, fill=fill, outline=fill, style=tk.PIESLICE)
    c.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2,
                 start=180, extent=90, fill=fill, outline=fill, style=tk.PIESLICE)
    c.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2,
                 start=270, extent=90, fill=fill, outline=fill, style=tk.PIESLICE)


def _interpolate_color(lo: str, hi: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    lr, lg, lb = _hex_to_rgb(lo)
    hr, hg, hb = _hex_to_rgb(hi)
    r = int(lr + (hr - lr) * t)
    g = int(lg + (hg - lg) * t)
    b = int(lb + (hb - lb) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    s = color.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
