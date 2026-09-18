"""Chain providers in priority order; rotate keys within a provider on failure.

Contract:
    * The audio clip is never lost while we retry — same ``AudioClip`` is
      passed through every attempt.
    * Each provider gets up to ``max_attempts_per_provider`` tries. Each
      attempt uses a different key (via ``KeyManager.acquire``).
    * On exhaustion of a provider (all keys quarantined or repeated hard
      failures) we fall through to the next provider in the chain.
    * The local engine has no keys; it is invoked once with ``key=None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns
from typing import Final

from ..audio.clip import AudioClip
from ..config import ProviderId
from ..util.logging import get_logger
from .base import (
    BaseTranscriber,
    KeyHandle,
    NoLiveKeysError,
    ProviderError,
    Transcript,
)
from .keys import KeyManager

_log: Final = get_logger(__name__)


class DispatchError(RuntimeError):
    """All configured providers failed to transcribe the clip."""


@dataclass(frozen=True, slots=True)
class _Attempt:
    provider: ProviderId
    error: str


class TranscriberDispatcher:
    def __init__(
        self,
        priority: tuple[ProviderId, ...],
        transcribers: dict[ProviderId, BaseTranscriber],
        keys: KeyManager,
        max_attempts_per_provider: int = 3,
    ) -> None:
        self._priority = priority
        self._transcribers = transcribers
        self._keys = keys
        self._max_attempts = max_attempts_per_provider

    def transcribe(self, clip: AudioClip) -> Transcript:
        history: list[_Attempt] = []
        for provider in self._priority:
            transcriber = self._transcribers.get(provider)
            if transcriber is None:
                history.append(_Attempt(provider, "not configured"))
                continue
            try:
                return self._try_provider(provider, transcriber, clip, history)
            except _ProviderExhausted as exc:
                history.append(_Attempt(provider, exc.reason))
                continue
        raise DispatchError(_format_history(history))

    def _try_provider(
        self,
        provider: ProviderId,
        transcriber: BaseTranscriber,
        clip: AudioClip,
        history: list[_Attempt],
    ) -> Transcript:
        if provider is ProviderId.LOCAL:
            return self._run_once(transcriber, clip, key=None)

        for attempt in range(1, self._max_attempts + 1):
            try:
                key = self._keys.acquire(provider)
            except NoLiveKeysError as exc:
                raise _ProviderExhausted(f"no live keys ({exc})") from exc

            try:
                transcript = self._run_once(transcriber, clip, key=key)
            except ProviderError as exc:
                # KeyManager decides the consequence (INACTIVE for AuthError,
                # DEGRADED/COOLDOWN for the rest). We just loop for another key.
                self._keys.report_failure(key, exc)
                history.append(_Attempt(provider, f"attempt {attempt}: {exc}"))
                continue
            self._keys.report_success(key)
            return transcript
        raise _ProviderExhausted(f"exhausted {self._max_attempts} attempts")

    @staticmethod
    def _run_once(
        transcriber: BaseTranscriber,
        clip: AudioClip,
        key: KeyHandle | None,
    ) -> Transcript:
        started = monotonic_ns()
        transcript = transcriber.transcribe(clip, key)
        elapsed_ms = (monotonic_ns() - started) // 1_000_000
        _log.info(
            "transcribe_ok",
            provider=transcriber.provider.value,
            model=transcriber.model,
            key=key.fingerprint if key is not None else None,
            latency_ms=int(elapsed_ms),
            chars=len(transcript.text),
        )
        return transcript


class _ProviderExhausted(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _format_history(history: list[_Attempt]) -> str:
    if not history:
        return "no providers configured"
    parts = [f"{a.provider.value}: {a.error}" for a in history]
    return "all providers failed — " + "; ".join(parts)
