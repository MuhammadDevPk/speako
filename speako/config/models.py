"""Typed configuration dataclasses.

All configuration values used at runtime are declared here. Dataclasses are
frozen so config is effectively immutable once loaded — no component may
mutate it. The loader (``loader.py``) is the only place that builds them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class ProviderId(StrEnum):
    GROQ = "groq"
    GEMINI = "gemini"
    LOCAL = "local"


HotkeyMode = Literal["hold", "toggle"]
OutputMethod = Literal["paste", "type"]
LogFormat = Literal["json", "text"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


@dataclass(frozen=True, slots=True)
class GroqProviderConfig:
    model: str
    api_keys_env: str
    api_keys: tuple[str, ...]  # resolved from env at load time


@dataclass(frozen=True, slots=True)
class GeminiProviderConfig:
    model: str
    api_keys_env: str
    api_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LocalProviderConfig:
    model: str
    compute_type: str  # faster-whisper only; mlx ignores


@dataclass(frozen=True, slots=True)
class ProvidersConfig:
    priority: tuple[ProviderId, ...]
    groq: GroqProviderConfig | None
    gemini: GeminiProviderConfig | None
    local: LocalProviderConfig | None


@dataclass(frozen=True, slots=True)
class AudioConfig:
    sample_rate: int
    channels: int
    device: int | str | None
    max_seconds: int


@dataclass(frozen=True, slots=True)
class HotkeyConfig:
    key: str
    mode: HotkeyMode


@dataclass(frozen=True, slots=True)
class OutputConfig:
    method: OutputMethod
    restore_clipboard: bool


@dataclass(frozen=True, slots=True)
class CooldownConfig:
    initial_seconds: float
    factor: float
    max_seconds: float


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: LogLevel
    format: LogFormat


HudPosition = Literal["bottom_center", "top_center"]


@dataclass(frozen=True, slots=True)
class UIConfig:
    enabled: bool
    position: HudPosition
    margin_px: int          # distance from the screen edge
    opacity: float          # 0.0–1.0 whole-window alpha


@dataclass(frozen=True, slots=True)
class AppConfig:
    providers: ProvidersConfig
    audio: AudioConfig
    hotkey: HotkeyConfig
    output: OutputConfig
    cooldown: CooldownConfig
    logging: LoggingConfig
    ui: UIConfig
    source_paths: tuple[str, ...] = field(default_factory=tuple)
