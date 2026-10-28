"""In-memory audio clip and WAV serialization.

Kept dependency-free of any SDK. Provider strategies convert the clip to
whatever wire format their SDK expects.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

_MIN_AMPLITUDE_THRESHOLD: Final[float] = 1e-4


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

    def normalize(self, target_peak: float = 0.95, max_gain: float = 20.0) -> AudioClip:
        """Return a gain-normalized copy of the clip with DC bias removed."""
        if self.samples.size == 0:
            return self
        samples = self.samples.copy()
        # Remove DC offset (center around 0.0)
        mean_val = np.mean(samples, axis=0, keepdims=True)
        centered = samples - mean_val
        max_amp = float(np.max(np.abs(centered)))
        if max_amp > _MIN_AMPLITUDE_THRESHOLD:
            # Boost quiet speech up to target_peak (cap gain to avoid noise floor)
            gain = min(target_peak / max_amp, max_gain)
            centered = centered * gain
        return AudioClip(
            samples=centered.astype(np.float32),
            sample_rate=self.sample_rate,
            channels=self.channels,
        )

    def split_chunks(self, max_chunk_seconds: float = 600.0) -> list[AudioClip]:
        """Split long audio into chunks not exceeding max_chunk_seconds.

        Searches for silence / low energy near boundaries to avoid splitting mid-word.
        """
        if self.duration_seconds <= max_chunk_seconds:
            return [self]

        chunk_samples = int(max_chunk_seconds * self.sample_rate)
        total_samples = self.samples.shape[0]
        chunks: list[AudioClip] = []

        start = 0
        while start < total_samples:
            end = min(start + chunk_samples, total_samples)
            split_point = end

            # If not at the end of audio, search for lowest amplitude window within +/- 5 seconds
            if end < total_samples and (end - start) > int(10 * self.sample_rate):
                window_size = int(5 * self.sample_rate)
                search_start = max(start, end - window_size)
                search_end = min(total_samples, end + window_size)
                search_slice = self.samples[search_start:search_end]
                block_len = int(0.1 * self.sample_rate)
                if search_slice.shape[0] >= block_len:
                    reshaped = np.abs(
                        search_slice[: (search_slice.shape[0] // block_len) * block_len]
                    )
                    reshaped = (
                        reshaped.mean(axis=1)
                        if self.channels > 1
                        else reshaped.reshape(-1)
                    )
                    blocks = reshaped.reshape(-1, block_len).mean(axis=1)
                    min_idx = int(np.argmin(blocks))
                    split_point = search_start + (min_idx * block_len)

            chunk_clip = AudioClip(
                samples=self.samples[start:split_point],
                sample_rate=self.sample_rate,
                channels=self.channels,
            )
            chunks.append(chunk_clip)
            start = split_point

        return chunks


def _float32_to_pcm16(samples: NDArray[np.float32]) -> NDArray[np.int16]:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16)
