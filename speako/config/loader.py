"""Configuration loader.

Precedence (lowest → highest):
    1. Packaged defaults (this module).
    2. Project ``config.yaml`` in CWD.
    3. User ``~/.config/speako/config.yaml``.
    4. Environment variables (``SPEAKO_*``) and the ``.env`` file.
    5. Explicit overrides passed by the caller (e.g. from CLI flags).

Secrets (API key pools) are resolved **only** from environment variables
named by ``api_keys_env``. YAML never carries secret values.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final, cast

import yaml
from dotenv import load_dotenv

from .models import (
    AppConfig,
    AudioConfig,
    CooldownConfig,
    GeminiProviderConfig,
    GroqProviderConfig,
    HotkeyConfig,
    HotkeyMode,
    LocalProviderConfig,
    LogFormat,
    LoggingConfig,
    LogLevel,
    OutputConfig,
    OutputMethod,
    ProviderId,
    ProvidersConfig,
)


class ConfigError(ValueError):
    """Raised when configuration is missing, malformed, or contradictory."""


_DEFAULTS: Final[dict[str, Any]] = {
    "providers": {
        "priority": ["local"],
        "groq": None,
        "gemini": None,
        "local": {
            "model": "large-v3-turbo",
            "compute_type": "int8",
        },
    },
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
        "device": None,
        "max_seconds": 120,
    },
    "hotkey": {
        "key": "right_option",
        "mode": "hold",
    },
    "output": {
        "method": "paste",
        "restore_clipboard": True,
    },
    "cooldown": {
        "initial_seconds": 30.0,
        "factor": 4.0,
        "max_seconds": 600.0,
    },
    "logging": {
        "level": "INFO",
        "format": "json",
    },
}

_USER_CONFIG_PATH: Final[Path] = Path.home() / ".config" / "speako" / "config.yaml"
_VALID_HOTKEY_MODES: Final[frozenset[str]] = frozenset({"hold", "toggle"})
_VALID_OUTPUT_METHODS: Final[frozenset[str]] = frozenset({"paste", "type"})
_VALID_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)
_VALID_LOG_FORMATS: Final[frozenset[str]] = frozenset({"json", "text"})


def load_config(
    project_config: Path | str | None = None,
    overrides: dict[str, Any] | None = None,
    load_env: bool = True,
) -> AppConfig:
    """Load and validate configuration.

    Args:
        project_config: Path to the project-level YAML file. Defaults to
            ``./config.yaml`` (if it exists) or the ``SPEAKO_CONFIG`` env var.
        overrides: Nested dict merged last (typically from CLI flags).
        load_env: If True (default), read ``.env`` in CWD into ``os.environ``.
    """
    if load_env:
        load_dotenv(override=False)

    merged: dict[str, Any] = _deep_copy_defaults(_DEFAULTS)
    source_paths: list[str] = []

    project_path = _resolve_project_path(project_config)
    if project_path is not None:
        _deep_merge(merged, _read_yaml(project_path))
        source_paths.append(str(project_path))

    if _USER_CONFIG_PATH.exists():
        _deep_merge(merged, _read_yaml(_USER_CONFIG_PATH))
        source_paths.append(str(_USER_CONFIG_PATH))

    _apply_env_overrides(merged)

    if overrides:
        _deep_merge(merged, overrides)

    return _build_config(merged, tuple(source_paths))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_project_path(explicit: Path | str | None) -> Path | None:
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise ConfigError(f"config file not found: {path}")
        return path

    env_path = os.environ.get("SPEAKO_CONFIG")
    if env_path:
        path = Path(env_path).expanduser()
        if not path.exists():
            raise ConfigError(f"SPEAKO_CONFIG points to missing file: {path}")
        return path

    default = Path.cwd() / "config.yaml"
    return default if default.exists() else None


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"top-level YAML in {path} must be a mapping")
    return cast("dict[str, Any]", data)


def _deep_copy_defaults(src: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in src.items():
        out[key] = _deep_copy_defaults(value) if isinstance(value, dict) else value
    return out


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value


def _apply_env_overrides(merged: dict[str, Any]) -> None:
    """A minimal set of env overrides for cross-cutting concerns.

    Deep provider-config overrides live in YAML; env variables are reserved
    for operator-level toggles (log level, config path).
    """
    level = os.environ.get("SPEAKO_LOG_LEVEL")
    if level:
        merged.setdefault("logging", {})["level"] = level.upper()


def _build_config(raw: dict[str, Any], source_paths: tuple[str, ...]) -> AppConfig:
    return AppConfig(
        providers=_build_providers(_require_mapping(raw, "providers")),
        audio=_build_audio(_require_mapping(raw, "audio")),
        hotkey=_build_hotkey(_require_mapping(raw, "hotkey")),
        output=_build_output(_require_mapping(raw, "output")),
        cooldown=_build_cooldown(_require_mapping(raw, "cooldown")),
        logging=_build_logging(_require_mapping(raw, "logging")),
        source_paths=source_paths,
    )


def _build_providers(raw: dict[str, Any]) -> ProvidersConfig:
    priority_raw = raw.get("priority") or []
    if not isinstance(priority_raw, list) or not priority_raw:
        raise ConfigError("providers.priority must be a non-empty list")

    priority: list[ProviderId] = []
    for name in priority_raw:
        try:
            priority.append(ProviderId(name))
        except ValueError as exc:
            raise ConfigError(f"unknown provider in priority: {name!r}") from exc

    groq = _build_groq(raw.get("groq")) if ProviderId.GROQ in priority else None
    gemini = _build_gemini(raw.get("gemini")) if ProviderId.GEMINI in priority else None
    local = _build_local(raw.get("local")) if ProviderId.LOCAL in priority else None

    if ProviderId.GROQ in priority and groq is None:
        raise ConfigError("providers.groq config required when groq is in priority")
    if ProviderId.GEMINI in priority and gemini is None:
        raise ConfigError("providers.gemini config required when gemini is in priority")
    if ProviderId.LOCAL in priority and local is None:
        raise ConfigError("providers.local config required when local is in priority")

    return ProvidersConfig(
        priority=tuple(priority),
        groq=groq,
        gemini=gemini,
        local=local,
    )


def _build_groq(raw: Any) -> GroqProviderConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("providers.groq must be a mapping")
    model = _require_str(raw, "model", "providers.groq")
    keys_env = _require_str(raw, "api_keys_env", "providers.groq")
    return GroqProviderConfig(
        model=model,
        api_keys_env=keys_env,
        api_keys=_resolve_keys(keys_env),
    )


def _build_gemini(raw: Any) -> GeminiProviderConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("providers.gemini must be a mapping")
    model = _require_str(raw, "model", "providers.gemini")
    keys_env = _require_str(raw, "api_keys_env", "providers.gemini")
    return GeminiProviderConfig(
        model=model,
        api_keys_env=keys_env,
        api_keys=_resolve_keys(keys_env),
    )


def _build_local(raw: Any) -> LocalProviderConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("providers.local must be a mapping")
    return LocalProviderConfig(
        model=_require_str(raw, "model", "providers.local"),
        compute_type=str(raw.get("compute_type", "int8")),
    )


def _build_audio(raw: dict[str, Any]) -> AudioConfig:
    sample_rate = int(raw.get("sample_rate", 16000))
    channels = int(raw.get("channels", 1))
    max_seconds = int(raw.get("max_seconds", 120))
    if sample_rate <= 0:
        raise ConfigError("audio.sample_rate must be positive")
    if channels not in (1, 2):
        raise ConfigError("audio.channels must be 1 or 2")
    if max_seconds <= 0:
        raise ConfigError("audio.max_seconds must be positive")
    device = raw.get("device")
    if device is not None and not isinstance(device, (int, str)):
        raise ConfigError("audio.device must be null, int, or string")
    return AudioConfig(
        sample_rate=sample_rate,
        channels=channels,
        device=device,
        max_seconds=max_seconds,
    )


def _build_hotkey(raw: dict[str, Any]) -> HotkeyConfig:
    key = _require_str(raw, "key", "hotkey")
    mode = str(raw.get("mode", "hold")).lower()
    if mode not in _VALID_HOTKEY_MODES:
        raise ConfigError(f"hotkey.mode must be one of {sorted(_VALID_HOTKEY_MODES)}")
    return HotkeyConfig(key=key, mode=cast("HotkeyMode", mode))


def _build_output(raw: dict[str, Any]) -> OutputConfig:
    method = str(raw.get("method", "paste")).lower()
    if method not in _VALID_OUTPUT_METHODS:
        raise ConfigError(f"output.method must be one of {sorted(_VALID_OUTPUT_METHODS)}")
    return OutputConfig(
        method=cast("OutputMethod", method),
        restore_clipboard=bool(raw.get("restore_clipboard", True)),
    )


def _build_cooldown(raw: dict[str, Any]) -> CooldownConfig:
    initial = float(raw.get("initial_seconds", 30.0))
    factor = float(raw.get("factor", 4.0))
    max_s = float(raw.get("max_seconds", 600.0))
    if initial <= 0 or factor < 1.0 or max_s < initial:
        raise ConfigError("cooldown must satisfy: 0 < initial, factor >= 1, max >= initial")
    return CooldownConfig(initial_seconds=initial, factor=factor, max_seconds=max_s)


def _build_logging(raw: dict[str, Any]) -> LoggingConfig:
    level = str(raw.get("level", "INFO")).upper()
    fmt = str(raw.get("format", "json")).lower()
    if level not in _VALID_LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of {sorted(_VALID_LOG_LEVELS)}")
    if fmt not in _VALID_LOG_FORMATS:
        raise ConfigError(f"logging.format must be one of {sorted(_VALID_LOG_FORMATS)}")
    return LoggingConfig(level=cast("LogLevel", level), format=cast("LogFormat", fmt))


def _resolve_keys(env_var: str) -> tuple[str, ...]:
    raw = os.environ.get(env_var, "")
    return tuple(k.strip() for k in raw.split(",") if k.strip())


def _require_mapping(root: dict[str, Any], key: str) -> dict[str, Any]:
    value = root.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"config section {key!r} must be a mapping")
    return cast("dict[str, Any]", value)


def _require_str(mapping: dict[str, Any], key: str, ctx: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{ctx}.{key} must be a non-empty string")
    return value
