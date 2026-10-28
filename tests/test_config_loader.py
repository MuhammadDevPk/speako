"""Tests for the config loader: precedence, validation, key resolution."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from speako.config import ConfigError, load_config
from speako.config.models import ProviderId


def _write_yaml(path: Path, body: str) -> Path:
    path.write_text(dedent(body).lstrip(), encoding="utf-8")
    return path


def test_defaults_api_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_config(load_env=False, user_config=False)
    assert cfg.providers.priority == (ProviderId.GROQ, ProviderId.GEMINI)
    assert cfg.providers.groq is not None
    assert cfg.providers.groq.model == "whisper-large-v3"
    assert cfg.providers.gemini is not None
    assert cfg.providers.gemini.model == "gemini-2.5-flash"
    assert cfg.providers.local is None
    assert cfg.audio.sample_rate == 16000
    assert cfg.audio.max_seconds == 3600
    assert cfg.hotkey.mode == "toggle"


def test_yaml_overrides_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEYS", "k1,k2, k3 ")
    cfg_path = _write_yaml(
        tmp_path / "config.yaml",
        """
        providers:
          priority: [groq]
          groq:
            model: whisper-large-v3
            api_keys_env: GROQ_API_KEYS
        audio:
          sample_rate: 22050
        """,
    )
    monkeypatch.chdir(tmp_path)
    cfg = load_config(project_config=cfg_path, load_env=False, user_config=False)
    assert cfg.providers.priority == (ProviderId.GROQ,)
    assert cfg.providers.groq is not None
    assert cfg.providers.groq.api_keys == ("k1", "k2", "k3")
    assert cfg.audio.sample_rate == 22050
    # untouched defaults preserved
    assert cfg.audio.channels == 1


def test_overrides_win(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_config(
        overrides={"logging": {"level": "DEBUG"}, "hotkey": {"key": "f19"}},
        load_env=False,
        user_config=False,
    )
    assert cfg.logging.level == "DEBUG"
    assert cfg.hotkey.key == "f19"


def test_env_overrides_log_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEAKO_LOG_LEVEL", "warning")
    monkeypatch.chdir(tmp_path)
    cfg = load_config(load_env=False, user_config=False)
    assert cfg.logging.level == "WARNING"


def test_priority_requires_provider_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _write_yaml(
        tmp_path / "config.yaml",
        """
        providers:
          priority: [groq]
          groq: null
        """,
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="groq"):
        load_config(project_config=cfg_path, load_env=False, user_config=False)


def test_local_provider_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Local in priority is filtered out
    cfg_path = _write_yaml(
        tmp_path / "config.yaml",
        """
        providers:
          priority: [groq, local]
          groq:
            model: whisper-large-v3
            api_keys_env: GROQ_API_KEYS
        """,
    )
    monkeypatch.chdir(tmp_path)
    cfg = load_config(project_config=cfg_path, load_env=False, user_config=False)
    assert cfg.providers.priority == (ProviderId.GROQ,)
    assert cfg.providers.local is None

    # Only local raises ConfigError
    cfg_path_only_local = _write_yaml(
        tmp_path / "config_local.yaml",
        """
        providers:
          priority: [local]
        """,
    )
    with pytest.raises(ConfigError, match="local models"):
        load_config(project_config=cfg_path_only_local, load_env=False, user_config=False)


def test_secrets_never_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    cfg_path = _write_yaml(
        tmp_path / "config.yaml",
        """
        providers:
          priority: [groq]
          groq:
            model: whisper-large-v3-turbo
            api_keys_env: GROQ_API_KEYS
        """,
    )
    monkeypatch.chdir(tmp_path)
    cfg = load_config(project_config=cfg_path, load_env=False)
    assert cfg.providers.groq is not None
    assert cfg.providers.groq.api_keys == ()  # empty pool, not a YAML string


def test_invalid_hotkey_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _write_yaml(
        tmp_path / "config.yaml",
        """
        hotkey:
          key: right_option
          mode: chord
        """,
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="hotkey.mode"):
        load_config(project_config=cfg_path, load_env=False, user_config=False)


def test_missing_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(project_config=tmp_path / "nope.yaml", load_env=False, user_config=False)
