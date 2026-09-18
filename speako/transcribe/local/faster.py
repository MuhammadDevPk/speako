"""faster-whisper backend (Intel / AMD x86_64, optional CUDA)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from ...audio.clip import AudioClip
from ...config import ProviderId
from ...util.logging import get_logger
from ..base import FatalProviderError, KeyHandle, Transcript, TransientProviderError

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

_log: Final = get_logger(__name__)


class FasterWhisperTranscriber:
    provider: ProviderId = ProviderId.LOCAL

    def __init__(self, model: str, compute_type: str = "int8") -> None:
        self.model = model
        self._compute_type = compute_type
        self._instance: WhisperModel | None = None
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "faster-whisper is required on x86_64: pip install speako[faster]"
            ) from exc

    def transcribe(self, clip: AudioClip, key: KeyHandle | None) -> Transcript:
        model = self._ensure_loaded()
        audio = clip.as_mono_float32()
        started = time.monotonic_ns()
        try:
            segments, _info = model.transcribe(audio, beam_size=1)
            text = "".join(seg.text for seg in segments).strip()
        except FileNotFoundError as exc:
            raise FatalProviderError(f"model not found: {self.model}", self.provider) from exc
        except (RuntimeError, ValueError) as exc:
            raise TransientProviderError(str(exc), self.provider) from exc

        elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
        return Transcript(
            text=text,
            provider=self.provider,
            model=self.model,
            latency_ms=int(elapsed_ms),
        )

    def _ensure_loaded(self) -> WhisperModel:
        if self._instance is None:
            from faster_whisper import WhisperModel
            self._instance = WhisperModel(
                self.model,
                device="auto",
                compute_type=self._compute_type,
            )
        return self._instance
