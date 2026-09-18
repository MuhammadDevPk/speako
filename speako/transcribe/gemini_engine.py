"""Gemini Flash audio transcription strategy.

Uses the modern `google-genai` SDK (the older `google-generativeai` package
was deprecated in 2024–2025). Sends WAV audio inline as bytes; a 120-second
16 kHz mono clip is ~3.8 MB — well under the inline threshold.
"""

from __future__ import annotations

import time
from typing import Final

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

_log: Final = get_logger(__name__)

_PROMPT: Final[str] = (
    "Transcribe the following audio verbatim. "
    "Return only the transcription — no preamble, no commentary, no quotes."
)

_HTTP_UNAUTHORIZED: Final[frozenset[int]] = frozenset({401, 403})
_HTTP_RATE_LIMIT: Final[int] = 429
_HTTP_PAYMENT_REQUIRED: Final[int] = 402
_HTTP_TRANSIENT: Final[frozenset[int]] = frozenset({500, 502, 503, 504})
_HTTP_FATAL_CLIENT: Final[frozenset[int]] = frozenset({400, 404, 415, 422})


class GeminiTranscriber:
    provider: ProviderId = ProviderId.GEMINI

    def __init__(self, model: str) -> None:
        self.model = model
        try:
            import google.genai  # noqa: F401 — presence check
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Gemini provider requires the `gemini` extra: "
                "uv sync --extra gemini"
            ) from exc

    def transcribe(self, clip: AudioClip, key: KeyHandle | None) -> Transcript:
        if key is None:
            raise FatalProviderError("Gemini requires an API key", self.provider)

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key.value)
        wav_bytes = clip.to_wav_bytes()
        contents = [
            _PROMPT,
            types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
        ]

        started = time.monotonic_ns()
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
            )
        except Exception as exc:
            raise self._map_error(exc) from exc

        text = (getattr(response, "text", None) or "").strip()
        elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
        return Transcript(
            text=text,
            provider=self.provider,
            model=self.model,
            latency_ms=int(elapsed_ms),
        )

    def _map_error(self, exc: BaseException) -> BaseException:
        try:
            from google.genai import errors as gerrors
        except ImportError:
            return TransientProviderError(str(exc), self.provider)

        if not isinstance(exc, gerrors.APIError):
            # Network layer or unexpected — treat as transient so we rotate.
            return TransientProviderError(str(exc), self.provider)

        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        message = getattr(exc, "message", None) or str(exc)

        if code in _HTTP_UNAUTHORIZED:
            return AuthError(message, self.provider)
        if code == _HTTP_RATE_LIMIT:
            return RateLimitError(message, self.provider)
        if code == _HTTP_PAYMENT_REQUIRED:
            return QuotaExhaustedError(message, self.provider)
        if code in _HTTP_TRANSIENT:
            return TransientProviderError(message, self.provider)
        if code in _HTTP_FATAL_CLIENT:
            return FatalProviderError(message, self.provider)
        return TransientProviderError(message, self.provider)
