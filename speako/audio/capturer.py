"""Non-blocking microphone capture on top of ``sounddevice``.

The sounddevice callback runs on the audio driver's realtime thread. It
MUST NOT allocate slowly, block, or raise. Two queues are involved:

* **Main audio queue** (``_queue``, unbounded). ``put_nowait`` on an
  unbounded queue never raises ``queue.Full``. Memory is bounded instead
  by counting samples in the callback and *dropping newest* chunks once
  ``config.max_seconds`` worth is buffered. Drop-newest is deliberate:
  the earlier drop-oldest policy silently truncated the *start* of long
  captures, so a user speaking for 60 seconds only got the last ~20
  transcribed.
* **Level sink** (``_level_sink``, tiny bounded queue). UI-only. Losing
  the oldest reading is fine — the meter converges quickly to the current
  volume and a skipped level tick is invisible.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Final

import numpy as np
import sounddevice as sd
from numpy.typing import NDArray

from ..config import AudioConfig
from ..util.logging import get_logger
from .clip import AudioClip
from .level import RmsLevelMeter

_log: Final = get_logger(__name__)

# Level sink can lose the oldest reading with no visible artifact — the
# meter always converges quickly to the current volume. Keep the queue
# small so a slow UI thread can never accumulate stale readings.
_LEVEL_QUEUE_MAX: Final[int] = 8


class AudioDeviceError(RuntimeError):
    """Raised for anything the audio device rejects at open/start time."""


class AudioCapturer:
    """Owns a single ``sounddevice.InputStream``.

    Lifecycle:
        capturer = AudioCapturer(cfg)
        capturer.start()          # opens the stream
        capturer.begin_capture()  # begins buffering frames
        ...user talks...
        clip = capturer.end_capture()  # drains queue → AudioClip
        capturer.close()          # closes the stream

    ``start`` / ``close`` are called once per app run. ``begin_capture`` /
    ``end_capture`` are called once per hotkey press/release.
    """

    def __init__(
        self,
        config: AudioConfig,
        level_sink: queue.Queue[float] | None = None,
    ) -> None:
        self._config = config
        self._stream: sd.InputStream | None = None
        # Unbounded during a single capture. Memory is naturally bounded
        # by the callback's sample-count cap (see _on_audio). Drain empties
        # it back to zero between captures.
        self._queue: queue.Queue[NDArray[np.float32]] = queue.Queue()
        self._capturing = threading.Event()
        self._max_samples: int = config.sample_rate * config.max_seconds
        # Counter is written only by the audio-driver thread and read only
        # by begin_capture / end_capture on the main thread. Python's GIL
        # makes single-word int reads/writes atomic; no lock needed.
        self._buffered_samples: int = 0
        self._dropped_at_cap: int = 0
        self._cap_hit_logged: bool = False
        self._meter = RmsLevelMeter()
        self._level_sink = level_sink

    def start(self) -> None:
        if self._stream is not None:
            return
        try:
            self._stream = sd.InputStream(
                samplerate=self._config.sample_rate,
                channels=self._config.channels,
                dtype="float32",
                device=self._config.device,
                callback=self._on_audio,
            )
            self._stream.start()
        except (sd.PortAudioError, ValueError) as exc:
            raise AudioDeviceError(f"failed to open audio device: {exc}") from exc
        _log.info(
            "audio_stream_open",
            sample_rate=self._config.sample_rate,
            channels=self._config.channels,
            device=self._config.device,
        )

    def close(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
        _log.info("audio_stream_closed")

    def begin_capture(self) -> None:
        # Drain any stale frames from before the press.
        self._drain_queue()
        self._buffered_samples = 0
        self._dropped_at_cap = 0
        self._cap_hit_logged = False
        self._meter.reset()
        self._capturing.set()

    def end_capture(self) -> AudioClip:
        self._capturing.clear()
        chunks = self._drain_queue()
        if self._dropped_at_cap:
            _log.warning(
                "audio_capture_truncated_at_max_seconds",
                dropped_chunks=self._dropped_at_cap,
                max_seconds=self._config.max_seconds,
            )
        samples = (
            np.concatenate(chunks, axis=0)
            if chunks
            else np.zeros((0, self._config.channels), dtype=np.float32)
        )
        return AudioClip(
            samples=samples,
            sample_rate=self._config.sample_rate,
            channels=self._config.channels,
        )

    # ------------------------------------------------------------------
    # sounddevice callback — runs on the audio driver thread. Must be fast
    # and allocation-light. NEVER block, NEVER raise.
    # ------------------------------------------------------------------

    def _on_audio(
        self,
        indata: NDArray[np.float32],
        _frames: int,
        _time: Any,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            # Non-fatal — usually overflow. Log lazily; do not raise.
            _log.debug("audio_callback_status", status=str(status))
        if not self._capturing.is_set():
            return

        # Drop-NEWEST at the max-seconds cap. The earlier drop-oldest
        # policy silently truncated the start of long captures, so a
        # user speaking for a minute got only the tail transcribed.
        if self._buffered_samples >= self._max_samples:
            if not self._cap_hit_logged:
                _log.warning(
                    "audio_max_seconds_reached",
                    max_seconds=self._config.max_seconds,
                )
                self._cap_hit_logged = True
            self._dropped_at_cap += 1
        else:
            # Copy: sounddevice reuses the buffer.
            chunk = indata.copy()
            self._queue.put_nowait(chunk)
            self._buffered_samples += chunk.shape[0]

        # Level publication — always run (even past the cap) so the HUD
        # bars keep responding to the user's voice. UI-only, so drop-oldest
        # here is fine.
        if self._level_sink is not None:
            level = self._meter.observe(indata)
            try:
                self._level_sink.put_nowait(level)
            except queue.Full:
                try:
                    self._level_sink.get_nowait()
                    self._level_sink.put_nowait(level)
                except (queue.Empty, queue.Full):
                    pass

    def _drain_queue(self) -> list[NDArray[np.float32]]:
        chunks: list[NDArray[np.float32]] = []
        while True:
            try:
                chunks.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return chunks
