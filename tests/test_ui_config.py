"""UI section of the config loader."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from speako.config import ConfigError, load_config


def _write(path: Path, body: str) -> Path:
    path.write_text(dedent(body).lstrip(), encoding="utf-8")
    return path


def test_ui_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_config(load_env=False)
    assert cfg.ui.enabled is True
    assert cfg.ui.position == "bottom_center"
    assert cfg.ui.margin_px == 80
    assert cfg.ui.opacity == 0.92


def test_ui_yaml_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _write(
        tmp_path / "config.yaml",
        """
        ui:
          enabled: false
          position: top_center
          margin_px: 24
          opacity: 0.5
        """,
    )
    monkeypatch.chdir(tmp_path)
    cfg = load_config(project_config=cfg_path, load_env=False)
    assert cfg.ui.enabled is False
    assert cfg.ui.position == "top_center"
    assert cfg.ui.margin_px == 24
    assert cfg.ui.opacity == 0.5


def test_ui_cli_override_disables_hud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_config(overrides={"ui": {"enabled": False}}, load_env=False)
    assert cfg.ui.enabled is False


def test_ui_rejects_bad_position(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _write(
        tmp_path / "config.yaml",
        """
        ui:
          position: sidebar
        """,
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="ui.position"):
        load_config(project_config=cfg_path, load_env=False)


def test_ui_rejects_bad_opacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _write(
        tmp_path / "config.yaml",
        """
        ui:
          opacity: 1.7
        """,
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="ui.opacity"):
        load_config(project_config=cfg_path, load_env=False)


def test_ui_rejects_negative_margin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _write(
        tmp_path / "config.yaml",
        """
        ui:
          margin_px: -5
        """,
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="ui.margin_px"):
        load_config(project_config=cfg_path, load_env=False)
