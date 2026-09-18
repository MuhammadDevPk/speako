"""Main loop: consume hotkey events, drive capture → transcribe → insert.

Runs in the main thread. All heavy lifting happens on this thread (blocking
network I/O for cloud providers, blocking local inference). The hotkey and
audio threads only ever signal us — see PATTERNS.md §3, §4.
"""

from __future__ import annotations

import queue
import threading
from typing import Final

from .app import AppContext
from .audio import RecordEvent, RecordEventKind
from .audio.clip import AudioClip
from .transcribe import DispatchError, TranscribeError
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
            elif event.kind is RecordEventKind.RELEASE and recording:
                recording = False
                clip = app.capturer.end_capture()
                _log.info("capture_end", duration_s=round(clip.duration_seconds, 3))
                if clip.duration_seconds < _MIN_CLIP_SECONDS:
                    _log.info("clip_too_short_skipped")
                    continue
                _process(app, clip)
    finally:
        app.hotkey.stop()
        app.capturer.close()


def _process(app: AppContext, clip: AudioClip) -> None:
    # NoLiveKeysError is a TranscribeError subclass; DispatchError is a
    # RuntimeError. Together they cover every miss the dispatcher raises.
    try:
        transcript = app.dispatcher.transcribe(clip)
    except (DispatchError, TranscribeError) as exc:
        _log.error("transcribe_failed", error=str(exc))
        notify("speako", "Transcription failed. See logs.")
        return
    if not transcript.text:
        _log.info("empty_transcript")
        return
    app.injector.insert(transcript.text)
