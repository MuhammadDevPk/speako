"""AudioClip: WAV serialization, mono downmix, duration."""

from __future__ import annotations

import io
import wave

import numpy as np

from speako.audio.clip import AudioClip


def test_duration_seconds_mono() -> None:
    samples = np.zeros((16000,), dtype=np.float32)
    clip = AudioClip(samples=samples, sample_rate=16000, channels=1)
    assert clip.duration_seconds == 1.0


def test_wav_bytes_roundtrip_mono() -> None:
    samples = np.sin(np.linspace(0, 2 * np.pi, 16000, dtype=np.float32))
    clip = AudioClip(samples=samples, sample_rate=16000, channels=1)
    wav_bytes = clip.to_wav_bytes()
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 16000


def test_wav_clips_out_of_range_samples() -> None:
    samples = np.array([2.0, -2.0, 0.0], dtype=np.float32)
    clip = AudioClip(samples=samples, sample_rate=16000, channels=1)
    wav_bytes = clip.to_wav_bytes()
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        raw = wav.readframes(wav.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16)
    assert pcm[0] == 32767
    assert pcm[1] == -32767
    assert pcm[2] == 0


def test_as_mono_from_stereo_averages_channels() -> None:
    # Interleaved-shaped stereo: shape (frames, channels).
    samples = np.array(
        [[1.0, -1.0], [0.5, 0.5], [0.2, 0.4]],
        dtype=np.float32,
    )
    clip = AudioClip(samples=samples, sample_rate=16000, channels=2)
    mono = clip.as_mono_float32()
    np.testing.assert_allclose(mono, [0.0, 0.5, 0.3], atol=1e-6)
