"""RmsLevelMeter: silence, loud speech, floor adaptation, smoothing."""

from __future__ import annotations

import numpy as np

from speako.audio.level import RmsLevelMeter


def _silence(n: int = 1024) -> np.ndarray:
    # Not literal zero — noise-floor code takes log10 and needs a positive epsilon.
    return np.random.default_rng(42).normal(0.0, 1e-6, n).astype(np.float32)


def _loud(n: int = 1024, amplitude: float = 0.35) -> np.ndarray:
    t = np.linspace(0, 1, n, dtype=np.float32)
    return (amplitude * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def test_silence_produces_near_zero_level() -> None:
    meter = RmsLevelMeter()
    for _ in range(50):
        level = meter.observe(_silence())
    assert 0.0 <= level < 0.1


def test_loud_speech_reaches_full_scale() -> None:
    meter = RmsLevelMeter()
    for _ in range(30):
        level = meter.observe(_loud())
    assert level > 0.85


def test_floor_adapts_downward_over_time() -> None:
    meter = RmsLevelMeter()
    for _ in range(200):
        meter.observe(_silence())
    # Floor should have moved below the initial -55dB toward true silence.
    assert meter.noise_floor_db < -55.0


def test_attack_faster_than_release() -> None:
    meter = RmsLevelMeter()
    # Prime with silence to establish floor.
    for _ in range(20):
        meter.observe(_silence())
    # One loud chunk — level should already be substantially non-zero.
    level_attack = meter.observe(_loud())
    assert level_attack > 0.3
    # Then silence — release is slow, level shouldn't collapse in one step.
    level_release = meter.observe(_silence())
    assert level_release > 0.2 * level_attack


def test_level_stays_in_unit_interval() -> None:
    meter = RmsLevelMeter()
    for chunk in [_silence(), _loud(amplitude=0.99), _silence(), _loud(amplitude=2.0)]:
        level = meter.observe(chunk.astype(np.float32))
        assert 0.0 <= level <= 1.0


def test_reset_clears_state() -> None:
    meter = RmsLevelMeter()
    for _ in range(30):
        meter.observe(_loud())
    assert meter.current > 0.5
    meter.reset()
    assert meter.current == 0.0
    assert meter.noise_floor_db == -55.0
