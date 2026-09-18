"""Transcription pipeline: providers, key rotation, dispatch."""

from .base import (
    AuthError,
    BaseTranscriber,
    FatalProviderError,
    KeyHandle,
    NoLiveKeysError,
    ProviderError,
    QuotaExhaustedError,
    RateLimitError,
    TranscribeError,
    Transcript,
    TransientProviderError,
)
from .dispatcher import DispatchError, TranscriberDispatcher
from .keys import KeyManager, KeyState

__all__ = [
    "AuthError",
    "BaseTranscriber",
    "DispatchError",
    "FatalProviderError",
    "KeyHandle",
    "KeyManager",
    "KeyState",
    "NoLiveKeysError",
    "ProviderError",
    "QuotaExhaustedError",
    "RateLimitError",
    "TranscribeError",
    "Transcript",
    "TranscriberDispatcher",
    "TransientProviderError",
]
