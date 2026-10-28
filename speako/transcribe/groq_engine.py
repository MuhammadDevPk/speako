"""Groq (Whisper) transcription strategy.

Imports the Groq SDK lazily so the app works without the ``groq`` extra
installed when the provider isn't in the priority chain.
"""

from __future__ import annotations

import importlib.util
import time
from typing import TYPE_CHECKING, Any, Final

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

_DEFAULT_PROMPT: Final[str] = (
    "Accurate verbatim speech transcription with correct spelling, "
    "capitalization, and punctuation."
)

_MIN_CHUNK_DURATION_SECONDS: Final[float] = 0.1
_HTTP_PAYMENT_REQUIRED: Final[int] = 402
_HTTP_TRANSIENT: Final[frozenset[int]] = frozenset({500, 502, 503, 504})
_HTTP_FATAL_CLIENT: Final[frozenset[int]] = frozenset({400, 415, 422})


class GroqTranscriber:
    provider: ProviderId = ProviderId.GROQ

    def __init__(
        self,
        model: str = "whisper-large-v3",
        prompt: str | None = None,
        language: str | None = None,
    ) -> None:
        self.model = model
        self.prompt = prompt or _DEFAULT_PROMPT
        self.language = language
        if importlib.util.find_spec("groq") is None:  # pragma: no cover
            raise RuntimeError(
                "Groq provider requires the `groq` extra: uv sync --extra groq"
            )

    def transcribe(self, clip: AudioClip, key: KeyHandle | None) -> Transcript:
        if key is None:
            raise FatalProviderError("Groq requires an API key", self.provider)
        client = self._client(key.value)
        started = time.monotonic_ns()

        chunks = clip.split_chunks(max_chunk_seconds=600.0)
        texts: list[str] = []
        for chunk in chunks:
            if chunk.duration_seconds < _MIN_CHUNK_DURATION_SECONDS:
                continue
            normalized_chunk = chunk.normalize()
            try:
                kwargs: dict[str, Any] = {
                    "file": ("audio.wav", normalized_chunk.to_wav_bytes(), "audio/wav"),
                    "model": self.model,
                    "response_format": "json",
                    "temperature": 0.0,
                }
                if self.prompt:
                    kwargs["prompt"] = self.prompt
                if self.language:
                    kwargs["language"] = self.language

                result = client.audio.transcriptions.create(**kwargs)
            except Exception as exc:  # SDK-specific; mapped below
                raise self._map_error(exc) from exc

            raw: object = result
            if isinstance(raw, str):
                chunk_text = raw
            elif isinstance(raw, dict):
                chunk_text = str(raw.get("text", ""))
            else:
                chunk_text = str(getattr(raw, "text", ""))

            if chunk_text.strip():
                texts.append(chunk_text.strip())

        elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
        return Transcript(
            text=" ".join(texts).strip(),
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
