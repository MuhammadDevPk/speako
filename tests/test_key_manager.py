"""KeyManager: state transitions, round-robin, cooldown, exhaustion."""

from __future__ import annotations

import time

import pytest

from speako.config.models import (
    CooldownConfig,
    GroqProviderConfig,
    LocalProviderConfig,
    ProviderId,
    ProvidersConfig,
)
from speako.transcribe.base import (
    AuthError,
    NoLiveKeysError,
    QuotaExhaustedError,
    RateLimitError,
    TransientProviderError,
)
from speako.transcribe.keys import KeyManager, KeyState


def _providers(groq_keys: tuple[str, ...] = ("gsk_1111", "gsk_2222", "gsk_3333")) -> ProvidersConfig:
    return ProvidersConfig(
        priority=(ProviderId.GROQ, ProviderId.LOCAL),
        groq=GroqProviderConfig(
            model="whisper-large-v3-turbo",
            api_keys_env="GROQ_API_KEYS",
            api_keys=groq_keys,
        ),
        gemini=None,
        local=LocalProviderConfig(model="large-v3-turbo", compute_type="int8"),
    )


def _cooldown(initial: float = 30.0, factor: float = 2.0, max_s: float = 300.0) -> CooldownConfig:
    return CooldownConfig(initial_seconds=initial, factor=factor, max_seconds=max_s)


def test_round_robin_across_active_keys() -> None:
    km = KeyManager(_providers(), _cooldown())
    seen: list[int] = []
    for _ in range(6):
        k = km.acquire(ProviderId.GROQ)
        seen.append(k.index)
        km.report_success(k)
    # Every key should have been used at least once — no starvation.
    assert set(seen) == {0, 1, 2}


def test_transient_failure_degrades_then_cooldowns() -> None:
    km = KeyManager(_providers(("k_only",)), _cooldown())
    k = km.acquire(ProviderId.GROQ)
    km.report_failure(k, RateLimitError("429", ProviderId.GROQ))
    (_, state1, _) = km.snapshot(ProviderId.GROQ)[0]
    assert state1 is KeyState.DEGRADED

    k2 = km.acquire(ProviderId.GROQ)  # still eligible when degraded
    km.report_failure(k2, RateLimitError("429", ProviderId.GROQ))
    (_, state2, remaining) = km.snapshot(ProviderId.GROQ)[0]
    assert state2 is KeyState.COOLDOWN
    assert remaining > 0

    with pytest.raises(NoLiveKeysError):
        km.acquire(ProviderId.GROQ)


def test_auth_failure_goes_inactive_immediately() -> None:
    km = KeyManager(_providers(("bad_key",)), _cooldown())
    k = km.acquire(ProviderId.GROQ)
    km.report_failure(k, AuthError("401", ProviderId.GROQ))
    (_, state, _) = km.snapshot(ProviderId.GROQ)[0]
    assert state is KeyState.INACTIVE
    with pytest.raises(NoLiveKeysError):
        km.acquire(ProviderId.GROQ)


def test_quota_exhausted_uses_max_cooldown() -> None:
    km = KeyManager(_providers(("k",)), _cooldown(initial=10.0, factor=2.0, max_s=600.0))
    k = km.acquire(ProviderId.GROQ)
    km.report_failure(k, QuotaExhaustedError("402", ProviderId.GROQ))
    (_, state, remaining) = km.snapshot(ProviderId.GROQ)[0]
    assert state is KeyState.COOLDOWN
    # Uses the max cap directly, not the exponential ladder.
    assert 500 < remaining <= 600


def test_success_recovers_from_cooldown() -> None:
    km = KeyManager(_providers(("k",)), _cooldown(initial=0.01, factor=2.0, max_s=1.0))
    k = km.acquire(ProviderId.GROQ)
    km.report_failure(k, TransientProviderError("blip", ProviderId.GROQ))  # -> DEGRADED
    km.report_failure(k, TransientProviderError("blip", ProviderId.GROQ))  # -> COOLDOWN
    time.sleep(0.05)  # let cooldown expire; policy is monotonic-clock based
    k2 = km.acquire(ProviderId.GROQ)
    km.report_success(k2)
    (_, state, _) = km.snapshot(ProviderId.GROQ)[0]
    assert state is KeyState.ACTIVE


def test_empty_pool_raises_no_live_keys() -> None:
    km = KeyManager(_providers(()), _cooldown())
    with pytest.raises(NoLiveKeysError):
        km.acquire(ProviderId.GROQ)


def test_unregistered_provider_raises() -> None:
    km = KeyManager(_providers(()), _cooldown())
    with pytest.raises(NoLiveKeysError):
        km.acquire(ProviderId.GEMINI)
