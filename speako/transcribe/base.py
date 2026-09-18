"""Transcription domain: protocol, value types, closed error taxonomy.

Every provider strategy converts SDK-native exceptions to one of the
domain errors in this module BEFORE they cross the strategy boundary.
The dispatcher and ``KeyManager`` only ever see these types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from ..audio.clip import AudioClip
from ..config import ProviderId

_FINGERPRINT_TAIL: Final[int] = 4


@dataclass(frozen=True, slots=True)
class KeyHandle:
    """Opaque reference to one API key inside a provider pool.

    Only the last 4 characters of the value are ever logged (``fingerprint``).
    The full ``value`` is passed to the SDK and nowhere else.
    """

    provider: ProviderId
    index: int  # position in the original pool, stable across the session
    value: str

    @property
    def fingerprint(self) -> str:
        if len(self.value) >= _FINGERPRINT_TAIL:
            return f"…{self.value[-_FINGERPRINT_TAIL:]}"
        return "…"


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    provider: ProviderId
    model: str
    latency_ms: int


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class TranscribeError(Exception):
    """Root of the transcription error hierarchy."""


class ProviderError(TranscribeError):
    """Raised by provider strategies; carries the provider for logs."""

    def __init__(self, message: str, provider: ProviderId) -> None:
        super().__init__(message)
        self.provider = provider


class RateLimitError(ProviderError):
    """HTTP 429 or equivalent. Recoverable via cooldown or key rotation."""


class QuotaExhaustedError(ProviderError):
    """HTTP 402 or hard quota rejection. Key is unlikely to recover soon."""


class AuthError(ProviderError):
    """HTTP 401/403. Key is bad — mark INACTIVE for the session."""


class TransientProviderError(ProviderError):
    """HTTP 5xx / network blip. Try another key or another provider."""


class FatalProviderError(ProviderError):
    """Provider-side error we cannot rotate our way out of (e.g. malformed audio)."""


class NoLiveKeysError(TranscribeError):
    """The provider's key pool is exhausted for this session."""

    def __init__(self, provider: ProviderId) -> None:
        super().__init__(f"no live keys for provider {provider.value}")
        self.provider = provider


# ---------------------------------------------------------------------------
# Strategy interface
# ---------------------------------------------------------------------------


@runtime_checkable
class BaseTranscriber(Protocol):
    """One transcription backend.

    Local backends ignore ``key`` (it is ``None``). Cloud backends receive a
    live ``KeyHandle`` from the dispatcher and must raise a ``ProviderError``
    subclass on failure.
    """

    provider: ProviderId
    model: str

    def transcribe(self, clip: AudioClip, key: KeyHandle | None) -> Transcript: ...
