"""AudioCapturer buffering policy — regression tests for the drop-oldest bug.

The audio-driver callback used to drop OLDEST chunks on queue overflow,
which silently truncated the *start* of long captures. The current
policy is: unbounded queue during a single capture, drop-NEWEST once
``config.max_seconds`` worth of samples has been buffered.

These tests drive ``_on_audio`` directly with fake chunks so they run
in-process with no audio device.
"""

from __future__ import annotations

import numpy as np

from speako.audio.capturer import AudioCapturer
from speako.config.models import AudioConfig


def _cfg(sample_rate: int = 16000, max_seconds: int = 120) -> AudioConfig:
    return AudioConfig(
        sample_rate=sample_rate,
        channels=1,
        device=None,
        max_seconds=max_seconds,
    )


def _push(capturer: AudioCapturer, marker: float, n_samples: int = 256) -> None:
    chunk = np.full((n_samples, 1), marker, dtype=np.float32)
    capturer._on_audio(chunk, n_samples, None, 0)


def test_long_capture_preserves_the_start() -> None:
    """Regression: user speaking for ~100 s should not lose the first 60 s."""
    capturer = AudioCapturer(_cfg())
    capturer.begin_capture()
    # 100 s at 16 kHz with 256-sample chunks = ~6250 chunks — well beyond
    # the old drop-oldest ceiling (~2400) but under the 120 s cap.
    n = 6250
    for i in range(n):
        _push(capturer, marker=float(i))
    clip = capturer.end_capture()

    assert clip.samples.shape[0] == n * 256
    assert clip.samples[0, 0] == 0.0                # first chunk survived
    assert clip.samples[-1, 0] == float(n - 1)      # last chunk survived


def test_cap_drops_newest_not_oldest() -> None:
    """Past ``max_seconds`` we should keep the beginning of the recording."""
    # 1-second cap at 1 kHz → max 1000 samples.
    capturer = AudioCapturer(_cfg(sample_rate=1000, max_seconds=1))
    capturer.begin_capture()

    # Push 2 s worth of 100-sample chunks (20 chunks, markers 0..19).
    for i in range(20):
        _push(capturer, marker=float(i), n_samples=100)
    clip = capturer.end_capture()

    # Cap check compares BEFORE appending: chunks 0..9 (10 chunks × 100 =
    # 1000 samples) are accepted; chunk 10 is the first one seen with
    # buffered_samples >= max_samples, so 10..19 are dropped.
    assert clip.samples.shape[0] == 10 * 100
    assert clip.samples[0, 0] == 0.0
    assert clip.samples[-1, 0] == 9.0     # last accepted chunk is #9, not #19


def test_cap_counter_reflects_dropped_chunks() -> None:
    capturer = AudioCapturer(_cfg(sample_rate=1000, max_seconds=1))
    capturer.begin_capture()
    # 15 chunks × 100 samples = 5 chunks past the cap.
    for i in range(15):
        _push(capturer, marker=float(i), n_samples=100)
    _ = capturer.end_capture()
    assert capturer._dropped_at_cap == 5


def test_begin_capture_resets_counters_and_drains_residual() -> None:
    capturer = AudioCapturer(_cfg(sample_rate=1000, max_seconds=1))
    # First capture — hit the cap so counters are dirty.
    capturer.begin_capture()
    for i in range(15):
        _push(capturer, marker=float(i), n_samples=100)
    capturer.end_capture()
    # Prime some stale chunks that arrive between captures (they'll be
    # ignored because _capturing is clear, but simulate anyway via a
    # direct queue put to exercise the drain).
    capturer._queue.put_nowait(np.full((50, 1), 99.0, dtype=np.float32))

    # Second capture — counters should reset and residual should not
    # appear in the new clip.
    capturer.begin_capture()
    assert capturer._buffered_samples == 0
    assert capturer._dropped_at_cap == 0
    assert not capturer._cap_hit_logged

    _push(capturer, marker=1.0, n_samples=100)
    clip = capturer.end_capture()
    assert clip.samples.shape[0] == 100
    assert clip.samples[0, 0] == 1.0    # residual 99.0 was drained pre-capture


def test_chunks_ignored_when_not_capturing() -> None:
    capturer = AudioCapturer(_cfg())
    # No begin_capture — _capturing.is_set() is False.
    for i in range(50):
        _push(capturer, marker=float(i))
    # Even after starting, we shouldn't see any of those 50 stale chunks.
    capturer.begin_capture()
    _push(capturer, marker=999.0)
    clip = capturer.end_capture()
    assert clip.samples.shape[0] == 256
    assert clip.samples[0, 0] == 999.0
