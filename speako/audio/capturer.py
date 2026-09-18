"""Non-blocking microphone capture on top of ``sounddevice``.

The sounddevice callback runs on the audio driver's realtime thread. It
MUST NOT allocate slowly, block, or raise. Here it only calls
``Queue.put_nowait`` on a bounded queue; when the queue is full it drops
the oldest frame (via ``get_nowait``) and increments a counter. That policy
guarantees the audio thread never blocks and never grows memory unbounded.
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

# One frame chunk from sounddevice defaults to a few thousand samples; we
# cap the queue at enough chunks to buffer ~audio.max_seconds of audio at
# small block sizes. The exact number is not load-bearing — the safety
# valve is the drop-oldest policy.
_QUEUE_CHUNKS_PER_SECOND: Final[int] = 20

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
        self._queue: queue.Queue[NDArray[np.float32]] = queue.Queue(
            maxsize=max(_QUEUE_CHUNKS_PER_SECOND * config.max_seconds, 32)
        )
        self._capturing = threading.Event()
        self._drop_count: int = 0
        self._max_samples: int = config.sample_rate * config.max_seconds
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
        self._drop_count = 0
        self._meter.reset()
        self._capturing.set()

    def end_capture(self) -> AudioClip:
        self._capturing.clear()
        chunks = self._drain_queue()
        if self._drop_count:
            _log.warning("audio_frames_dropped", count=self._drop_count)
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
        # Copy: sounddevice reuses the buffer.
        chunk = indata.copy()
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(chunk)
                self._drop_count += 1
            except (queue.Empty, queue.Full):
                # Racing with drainer — a dropped frame is acceptable.
                self._drop_count += 1

        # Level publication: never allowed to block or throw. Drop the
        # oldest reading if the UI consumer is behind — the meter smooths
        # naturally so a skipped chunk is invisible.
        if self._level_sink is not None:
            level = self._meter.observe(chunk)
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
        total_samples = 0
        while True:
            try:
                chunk = self._queue.get_nowait()
            except queue.Empty:
                break
            chunks.append(chunk)
            total_samples += chunk.shape[0]
            if total_samples >= self._max_samples:
                _log.warning(
                    "audio_max_seconds_hit",
                    max_seconds=self._config.max_seconds,
                )
                break
        return chunks
