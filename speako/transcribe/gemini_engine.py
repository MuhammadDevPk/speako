"""Gemini Flash audio transcription strategy.

Uses the modern `google-genai` SDK (the older `google-generativeai` package
was deprecated in 2024–2025). Sends WAV audio inline as bytes; a 120-second
16 kHz mono clip is ~3.8 MB — well under the inline threshold.
"""

from __future__ import annotations

import importlib.util
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

_DEFAULT_PROMPT: Final[str] = (
    "Transcribe the spoken audio in this clip exactly. "
    "Do not add any greeting, answers, explanations, or comments. "
    "Return only the verbatim transcription text without quotes or markdown formatting."
)

_HTTP_UNAUTHORIZED: Final[frozenset[int]] = frozenset({401, 403})
_HTTP_RATE_LIMIT: Final[int] = 429
_HTTP_PAYMENT_REQUIRED: Final[int] = 402
_HTTP_TRANSIENT: Final[frozenset[int]] = frozenset({500, 502, 503, 504})
_HTTP_FATAL_CLIENT: Final[frozenset[int]] = frozenset({400, 404, 415, 422})


class GeminiTranscriber:
    provider: ProviderId = ProviderId.GEMINI

    def __init__(self, model: str = "gemini-2.5-flash", prompt: str | None = None) -> None:
        self.model = model
        self.prompt = prompt or _DEFAULT_PROMPT
        if importlib.util.find_spec("google.genai") is None:  # pragma: no cover
            raise RuntimeError(
                "Gemini provider requires the `gemini` extra: "
                "uv sync --extra gemini"
            )

    def transcribe(self, clip: AudioClip, key: KeyHandle | None) -> Transcript:
        if key is None:
            raise FatalProviderError("Gemini requires an API key", self.provider)

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key.value)
        normalized_clip = clip.normalize()
        wav_bytes = normalized_clip.to_wav_bytes()
        contents = [
            self.prompt,
            types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
        ]

        started = time.monotonic_ns()
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                ),
            )
        except Exception as exc:
            raise self._map_error(exc) from exc

        text = (getattr(response, "text", None) or "").strip()
        # Clean up any surrounding quotes or markdown code block formatting
        if text.startswith("```") and text.endswith("```"):
            text = text.strip("`").strip()
            if text.startswith("text\n") or text.startswith("markdown\n"):
                text = text.split("\n", 1)[1]
        if (text.startswith('"') and text.endswith('"')) or (
            text.startswith("'") and text.endswith("'")
        ):
            text = text[1:-1].strip()

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
