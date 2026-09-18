"""Audio capture, clip representation, and hotkey listening."""

from .capturer import AudioCapturer, AudioDeviceError
from .clip import AudioClip
from .hotkey import HotkeyListener, RecordEvent, RecordEventKind

__all__ = [
    "AudioCapturer",
    "AudioClip",
    "AudioDeviceError",
    "HotkeyListener",
    "RecordEvent",
    "RecordEventKind",
]
