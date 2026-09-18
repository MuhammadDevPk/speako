"""In-memory audio clip and WAV serialization.

Kept dependency-free of any SDK. Provider strategies convert the clip to
whatever wire format their SDK expects.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class AudioClip:
    """Mono or stereo float32 PCM in range [-1, 1]."""

    samples: NDArray[np.float32]
    sample_rate: int
    channels: int

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate == 0:
            return 0.0
        return float(self.samples.shape[0]) / float(self.sample_rate)

    def to_wav_bytes(self) -> bytes:
        """Encode as 16-bit PCM WAV. Suitable for Groq/Gemini upload."""
        pcm16 = _float32_to_pcm16(self.samples)
        buf = io.BytesIO()
        # Pylint can't follow wave.open's mode-based return-type overload
        # (it always infers Wave_read); mypy's overload for "wb" resolves
        # to Wave_write correctly, so those calls really do exist.
        # pylint: disable=no-member
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)  # 16-bit
            wav.setframerate(self.sample_rate)
            wav.writeframes(pcm16.tobytes())
        # pylint: enable=no-member
        return buf.getvalue()

    def as_mono_float32(self) -> NDArray[np.float32]:
        """Return a mono float32 view (averaging channels if needed)."""
        if self.channels == 1:
            return self.samples.reshape(-1).astype(np.float32, copy=False)
        reshaped = self.samples.reshape(-1, self.channels)
        return reshaped.mean(axis=1).astype(np.float32, copy=False)


def _float32_to_pcm16(samples: NDArray[np.float32]) -> NDArray[np.int16]:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16)
