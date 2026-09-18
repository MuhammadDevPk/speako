"""mlx-whisper backend (Apple Silicon)."""

from __future__ import annotations

import importlib.util
import time
from typing import Any, Final, cast

from ...audio.clip import AudioClip
from ...config import ProviderId
from ...util.logging import get_logger
from ..base import FatalProviderError, KeyHandle, Transcript, TransientProviderError

_log: Final = get_logger(__name__)


class MLXWhisperTranscriber:
    provider: ProviderId = ProviderId.LOCAL

    def __init__(self, model: str) -> None:
        self.model = model
        if importlib.util.find_spec("mlx_whisper") is None:  # pragma: no cover
            raise RuntimeError(
                "mlx-whisper is required on Apple Silicon: uv sync --extra mlx"
            )

    def transcribe(self, clip: AudioClip, _key: KeyHandle | None) -> Transcript:
        import mlx_whisper  # pyright: ignore[reportMissingImports]

        audio = clip.as_mono_float32()
        started = time.monotonic_ns()
        try:
            result = cast(
                dict[str, Any],
                mlx_whisper.transcribe(audio, path_or_hf_repo=self.model),
            )
        except FileNotFoundError as exc:
            raise FatalProviderError(f"model not found: {self.model}", self.provider) from exc
        except (RuntimeError, ValueError) as exc:
            raise TransientProviderError(str(exc), self.provider) from exc

        text = str(result.get("text", "")).strip()
        elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
        return Transcript(
            text=text,
            provider=self.provider,
            model=self.model,
            latency_ms=int(elapsed_ms),
        )
