"""Groq (Whisper) transcription strategy.

Imports the Groq SDK lazily so the app works without the ``groq`` extra
installed when the provider isn't in the priority chain.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from ..audio.clip import AudioClip
from ..config import ProviderId
from ..util.logging import get_logger
from .base import (
    AuthError,
    FatalProviderError,
    KeyHandle,
    QuotaExhaustedError,
    RateLimitError,
    Transcript,
    TransientProviderError,
)

if TYPE_CHECKING:
    from groq import Groq

_log: Final = get_logger(__name__)

_HTTP_PAYMENT_REQUIRED: Final[int] = 402
_HTTP_TRANSIENT: Final[frozenset[int]] = frozenset({500, 502, 503, 504})
_HTTP_FATAL_CLIENT: Final[frozenset[int]] = frozenset({400, 415, 422})


class GroqTranscriber:
    provider: ProviderId = ProviderId.GROQ

    def __init__(self, model: str) -> None:
        self.model = model
        try:
            import groq  # noqa: F401 — presence check
        except ImportError as exc:  # pragma: no cover - trivial import guard
            raise RuntimeError(
                "Groq provider requires the `groq` extra: pip install speako[groq]"
            ) from exc

    def transcribe(self, clip: AudioClip, key: KeyHandle | None) -> Transcript:
        if key is None:
            raise FatalProviderError("Groq requires an API key", self.provider)
        client = self._client(key.value)
        started = time.monotonic_ns()
        try:
            result = client.audio.transcriptions.create(
                file=("audio.wav", clip.to_wav_bytes(), "audio/wav"),
                model=self.model,
                response_format="text",
            )
        except Exception as exc:  # SDK-specific; mapped below
            raise self._map_error(exc) from exc

        # With response_format="text" the SDK returns a bare string; the
        # typed SDK annotation still says Transcription, so cast through
        # ``object`` to keep the isinstance branches meaningful to mypy.
        raw: object = result
        text = raw if isinstance(raw, str) else str(getattr(raw, "text", ""))
        elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
        return Transcript(
            text=text.strip(),
            provider=self.provider,
            model=self.model,
            latency_ms=int(elapsed_ms),
        )

    def _client(self, api_key: str) -> Groq:
        from groq import Groq
        return Groq(api_key=api_key)

    def _map_error(self, exc: BaseException) -> BaseException:
        # Import lazily so this module still parses without the SDK.
        try:
            from groq import (
                APIConnectionError,
                APIStatusError,
                AuthenticationError,
                PermissionDeniedError,
            )
            from groq import (
                RateLimitError as SDKRateLimitError,
            )
        except ImportError:
            return exc

        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return AuthError(str(exc), self.provider)
        if isinstance(exc, SDKRateLimitError):
            return RateLimitError(str(exc), self.provider)
        if isinstance(exc, APIConnectionError):
            return TransientProviderError(str(exc), self.provider)
        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", None)
            if status == _HTTP_PAYMENT_REQUIRED:
                return QuotaExhaustedError(str(exc), self.provider)
            if status in _HTTP_TRANSIENT:
                return TransientProviderError(str(exc), self.provider)
            if status in _HTTP_FATAL_CLIENT:
                return FatalProviderError(str(exc), self.provider)
        return TransientProviderError(str(exc), self.provider)
