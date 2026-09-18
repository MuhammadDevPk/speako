"""API key pool + circuit breaker state machine.

All state is guarded by a single ``threading.RLock``. Selection is O(N) in
the pool size, N is small (typically 1–5), so the naive scan is fine.

State transitions (see ARCHITECTURE.md §5):

    ACTIVE   --transient failure-->     DEGRADED
    DEGRADED --2nd transient failure--> COOLDOWN
    ANY      --auth failure-->          INACTIVE
    ANY      --quota exhausted-->       COOLDOWN (long)
    COOLDOWN --cooldown elapsed + probe success--> ACTIVE
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from ..config import CooldownConfig, ProviderId, ProvidersConfig
from ..util.logging import get_logger
from .base import (
    AuthError,
    KeyHandle,
    NoLiveKeysError,
    ProviderError,
    QuotaExhaustedError,
    RateLimitError,
    TransientProviderError,
)

_log: Final = get_logger(__name__)


class KeyState(Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    COOLDOWN = "cooldown"
    INACTIVE = "inactive"


@dataclass(slots=True)
class _KeyRecord:
    handle: KeyHandle
    state: KeyState = KeyState.ACTIVE
    cooldown_until: float = 0.0        # monotonic seconds
    consecutive_failures: int = 0
    backoff_step: int = 0              # index into the exponential ladder
    last_used_at: float = 0.0          # monotonic seconds; for round-robin


@dataclass(slots=True)
class _Pool:
    records: list[_KeyRecord] = field(default_factory=list)


class KeyManager:
    """Per-provider key pools with a shared cooldown policy.

    All public methods are thread-safe.
    """

    def __init__(self, providers: ProvidersConfig, cooldown: CooldownConfig) -> None:
        self._cooldown = cooldown
        self._lock = threading.RLock()
        self._pools: dict[ProviderId, _Pool] = {}
        self._register(providers)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_pool(self, provider: ProviderId) -> bool:
        return provider in self._pools

    def acquire(self, provider: ProviderId) -> KeyHandle:
        """Return the best eligible key or raise ``NoLiveKeysError``.

        Preference order:
            1. ACTIVE keys, oldest ``last_used_at`` first (round-robin).
            2. DEGRADED keys whose cooldown has elapsed.
            3. COOLDOWN keys whose cooldown has elapsed (treated as a probe).
        """
        with self._lock:
            pool = self._pools.get(provider)
            if pool is None or not pool.records:
                raise NoLiveKeysError(provider)

            now = time.monotonic()
            eligible = [
                r for r in pool.records
                if r.state is not KeyState.INACTIVE and r.cooldown_until <= now
            ]
            if not eligible:
                raise NoLiveKeysError(provider)

            eligible.sort(key=_selection_sort_key)
            chosen = eligible[0]
            chosen.last_used_at = now
            return chosen.handle

    def report_success(self, key: KeyHandle) -> None:
        with self._lock:
            record = self._find(key)
            if record is None:
                return
            if record.state is not KeyState.ACTIVE:
                _log.info(
                    "key_recovered",
                    provider=key.provider.value,
                    key=key.fingerprint,
                    from_state=record.state.value,
                )
            record.state = KeyState.ACTIVE
            record.consecutive_failures = 0
            record.backoff_step = 0
            record.cooldown_until = 0.0

    def report_failure(self, key: KeyHandle, error: ProviderError) -> None:
        with self._lock:
            record = self._find(key)
            if record is None:
                return
            record.consecutive_failures += 1
            prev_state = record.state
            new_state, cooldown_seconds = self._classify(error, record)
            record.state = new_state
            if cooldown_seconds > 0:
                record.cooldown_until = time.monotonic() + cooldown_seconds
                record.backoff_step += 1
            _log.warning(
                "key_state_transition",
                provider=key.provider.value,
                key=key.fingerprint,
                from_state=prev_state.value,
                to_state=new_state.value,
                cooldown_s=cooldown_seconds,
                error=type(error).__name__,
            )

    def snapshot(self, provider: ProviderId) -> list[tuple[str, KeyState, float]]:
        """Debug / test view: (fingerprint, state, cooldown_remaining_s)."""
        with self._lock:
            pool = self._pools.get(provider)
            if pool is None:
                return []
            now = time.monotonic()
            return [
                (r.handle.fingerprint, r.state, max(0.0, r.cooldown_until - now))
                for r in pool.records
            ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _register(self, providers: ProvidersConfig) -> None:
        if providers.groq is not None:
            self._pools[ProviderId.GROQ] = _make_pool(
                ProviderId.GROQ, providers.groq.api_keys
            )
        if providers.gemini is not None:
            self._pools[ProviderId.GEMINI] = _make_pool(
                ProviderId.GEMINI, providers.gemini.api_keys
            )
        # LOCAL has no keys and is intentionally absent from _pools.

    def _find(self, key: KeyHandle) -> _KeyRecord | None:
        pool = self._pools.get(key.provider)
        if pool is None:
            return None
        for record in pool.records:
            if record.handle.index == key.index:
                return record
        return None

    def _classify(
        self,
        error: ProviderError,
        record: _KeyRecord,
    ) -> tuple[KeyState, float]:
        if isinstance(error, AuthError):
            return KeyState.INACTIVE, 0.0
        if isinstance(error, QuotaExhaustedError):
            return KeyState.COOLDOWN, self._cooldown.max_seconds
        if isinstance(error, (RateLimitError, TransientProviderError)):
            if record.state is KeyState.ACTIVE:
                # First strike — deprioritize but stay usable.
                return KeyState.DEGRADED, 0.0
            # Repeat strike — quarantine with exponential backoff.
            return KeyState.COOLDOWN, self._compute_backoff(record.backoff_step)
        # Fatal / unknown: send to cooldown; do not INACTIVE (could be transient).
        return KeyState.COOLDOWN, self._compute_backoff(record.backoff_step)

    def _compute_backoff(self, step: int) -> float:
        seconds = self._cooldown.initial_seconds * (self._cooldown.factor ** step)
        return min(seconds, self._cooldown.max_seconds)


def _make_pool(provider: ProviderId, keys: tuple[str, ...]) -> _Pool:
    return _Pool(
        records=[
            _KeyRecord(handle=KeyHandle(provider=provider, index=i, value=k))
            for i, k in enumerate(keys)
        ]
    )


def _selection_sort_key(record: _KeyRecord) -> tuple[int, float]:
    """Lower is better. ACTIVE(0) beats DEGRADED(1) beats COOLDOWN(2).

    Within the same tier, oldest ``last_used_at`` wins (round-robin).
    """
    tier_rank: Final[dict[KeyState, int]] = {
        KeyState.ACTIVE: 0,
        KeyState.DEGRADED: 1,
        KeyState.COOLDOWN: 2,
        KeyState.INACTIVE: 99,
    }
    return (tier_rank[record.state], record.last_used_at)
