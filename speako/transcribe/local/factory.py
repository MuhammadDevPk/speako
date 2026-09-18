"""Hardware detection → concrete local engine.

Runs once at startup. The chosen instance is cached on the AppContext; no
runtime re-detection.
"""

from __future__ import annotations

import platform
from typing import Final

from ...config import LocalProviderConfig
from ...util.logging import get_logger
from ..base import BaseTranscriber

_log: Final = get_logger(__name__)


class UnsupportedHardwareError(RuntimeError):
    """No local backend supports the current platform."""


class LocalEngineFactory:
    @staticmethod
    def detect(config: LocalProviderConfig) -> BaseTranscriber:
        system = platform.system()
        machine = platform.machine().lower()

        if system == "Darwin" and machine == "arm64":
            from .mlx import MLXWhisperTranscriber
            _log.info("local_engine_selected", engine="mlx-whisper", machine=machine)
            return MLXWhisperTranscriber(model=config.model)

        if machine in {"x86_64", "amd64"}:
            from .faster import FasterWhisperTranscriber
            _log.info("local_engine_selected", engine="faster-whisper", machine=machine)
            return FasterWhisperTranscriber(
                model=config.model,
                compute_type=config.compute_type,
            )

        raise UnsupportedHardwareError(
            f"no local backend for system={system!r} machine={machine!r}. "
            "Install a cloud provider or run on Apple Silicon / x86_64."
        )
