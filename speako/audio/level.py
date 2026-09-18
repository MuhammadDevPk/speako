"""RMS-based level meter with a rolling noise floor.

Fed one audio chunk at a time from the sounddevice callback; produces a
normalized level in ``[0.0, 1.0]`` suitable for driving a VU-style
visualization. Pure NumPy, no allocations beyond a scalar float.

The noise floor tracks the quietest recent chunks (via a slow EMA on
sub-floor observations). Full-scale is a fixed span above the floor. That
combination adapts to a quiet room automatically while keeping the top of
the meter stable — loud speech reliably drives the meter to 1.0.
"""

from __future__ import annotations

from typing import Final

import numpy as np
from numpy.typing import NDArray

_EPS: Final[float] = 1e-8
_INITIAL_FLOOR_DB: Final[float] = -55.0
_FLOOR_ADAPTATION: Final[float] = 0.03    # slow — only tracks true silence
_SPAN_DB: Final[float] = 35.0             # floor + 35 dB → level 1.0
_LEVEL_ATTACK: Final[float] = 0.55        # rise responsiveness
_LEVEL_RELEASE: Final[float] = 0.18       # fall smoothing


class RmsLevelMeter:
    """Converts one audio chunk into a smoothed 0..1 level."""

    __slots__ = ("_floor_db", "_level")

    def __init__(self) -> None:
        self._floor_db: float = _INITIAL_FLOOR_DB
        self._level: float = 0.0

    def observe(self, chunk: NDArray[np.float32]) -> float:
        # RMS on float32 mono or interleaved stereo — squaring flattens phase.
        rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2, dtype=np.float32)))
        db = 20.0 * float(np.log10(rms + _EPS))

        # Adapt the floor slowly toward any chunk quieter than we've seen.
        if db < self._floor_db:
            self._floor_db += _FLOOR_ADAPTATION * (db - self._floor_db)

        raw = max(0.0, min(1.0, (db - self._floor_db) / _SPAN_DB))

        # Asymmetric smoothing: fast attack for responsiveness, slow release.
        alpha = _LEVEL_ATTACK if raw > self._level else _LEVEL_RELEASE
        self._level += alpha * (raw - self._level)
        return self._level

    def reset(self) -> None:
        self._floor_db = _INITIAL_FLOOR_DB
        self._level = 0.0

    @property
    def noise_floor_db(self) -> float:
        return self._floor_db

    @property
    def current(self) -> float:
        return self._level
