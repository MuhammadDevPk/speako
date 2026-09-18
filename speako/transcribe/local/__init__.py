"""Local (offline) transcription engines with hardware-aware selection."""

from .factory import LocalEngineFactory, UnsupportedHardwareError

__all__ = ["LocalEngineFactory", "UnsupportedHardwareError"]
