"""Main loop: consume hotkey events, drive capture → transcribe → insert.

When the HUD is enabled, ``run`` is launched on a worker thread and the
Tk mainloop owns the actual process main thread — see ``__main__``.
Either way, all heavy lifting (network I/O, local inference) happens on
this thread; the hotkey and audio driver threads only ever signal us.
"""

from __future__ import annotations

import contextlib
import queue
import threading
from typing import Final

from .app import AppContext
from .audio import RecordEvent, RecordEventKind
from .audio.clip import AudioClip
from .transcribe import DispatchError, TranscribeError
from .ui import HudShutdown, HudState, HudStateEvent
from .util.logging import get_logger
from .util.notify import notify

_log: Final = get_logger(__name__)

_EVENT_POLL_SECONDS: Final[float] = 0.25   # max latency to notice stop.is_set()
_MIN_CLIP_SECONDS: Final[float] = 0.15     # ignore accidental taps shorter than this


def run(app: AppContext, events: queue.Queue[RecordEvent], stop: threading.Event) -> None:
    app.capturer.start()
    app.hotkey.start()
    _log.info("speako_ready", hotkey=app.config.hotkey.key, mode=app.config.hotkey.mode)
    notify("speako", f"Ready. Hold {app.config.hotkey.key} to record.")

    recording = False
    try:
        while not stop.is_set():
            try:
                event = events.get(timeout=_EVENT_POLL_SECONDS)
            except queue.Empty:
                continue

            if event.kind is RecordEventKind.PRESS and not recording:
                app.capturer.begin_capture()
                recording = True
                _log.debug("capture_begin")
                _hud(app, HudState.LISTENING)
            elif event.kind is RecordEventKind.RELEASE and recording:
                recording = False
                clip = app.capturer.end_capture()
                _log.info("capture_end", duration_s=round(clip.duration_seconds, 3))
                if clip.duration_seconds < _MIN_CLIP_SECONDS:
                    _log.info("clip_too_short_skipped")
                    _hud(app, HudState.HIDDEN)
                    continue
                _hud(app, HudState.TRANSCRIBING)
                _process(app, clip)
    finally:
        app.hotkey.stop()
        app.capturer.close()
        # Signal the HUD to exit so the main thread's Tk mainloop unblocks.
        if app.hud_enabled:
            # Best-effort — HUD will also observe stop via its poller.
            with contextlib.suppress(queue.Full):
                app.hud_events.put_nowait(HudShutdown())


def _process(app: AppContext, clip: AudioClip) -> None:
    # NoLiveKeysError is a TranscribeError subclass; DispatchError is a
    # RuntimeError. Together they cover every miss the dispatcher raises.
    try:
        transcript = app.dispatcher.transcribe(clip)
    except (DispatchError, TranscribeError) as exc:
        _log.error("transcribe_failed", error=str(exc))
        notify("speako", "Transcription failed. See logs.")
        _hud(app, HudState.HIDDEN)
        return
    if not transcript.text:
        _log.info("empty_transcript")
        _hud(app, HudState.HIDDEN)
        return
    app.injector.insert(transcript.text)
    _hud(app, HudState.PASTED)


def _hud(app: AppContext, state: HudState) -> None:
    if not app.hud_enabled:
        return
    try:
        app.hud_events.put_nowait(HudStateEvent(state=state))
    except queue.Full:
        _log.debug("hud_state_dropped", state=state.value)
