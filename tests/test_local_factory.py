"""LocalEngineFactory hardware detection (without importing MLX/faster)."""

from __future__ import annotations

import pytest

from speako.config.models import LocalProviderConfig
from speako.transcribe.local.factory import LocalEngineFactory, UnsupportedHardwareError


def test_unsupported_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "SolarisNG")
    monkeypatch.setattr("platform.machine", lambda: "sparc64")
    cfg = LocalProviderConfig(model="large-v3-turbo", compute_type="int8")
    with pytest.raises(UnsupportedHardwareError, match="sparc64"):
        LocalEngineFactory.detect(cfg)


def test_apple_silicon_selects_mlx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    cfg = LocalProviderConfig(model="mlx-community/whisper-large-v3-turbo", compute_type="int8")
    # We don't require mlx-whisper to be installed to verify the branch.
    # The MLX transcriber's __init__ raises RuntimeError when the SDK is
    # missing, so we just assert that the factory routes to that branch by
    # observing the raised message ("mlx-whisper is required ...").
    try:
        LocalEngineFactory.detect(cfg)
    except RuntimeError as exc:
        assert "mlx-whisper" in str(exc)
    except UnsupportedHardwareError:
        pytest.fail("factory routed away from Apple Silicon branch")


def test_x86_selects_faster(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    cfg = LocalProviderConfig(model="large-v3-turbo", compute_type="int8")
    try:
        LocalEngineFactory.detect(cfg)
    except RuntimeError as exc:
        assert "faster-whisper" in str(exc)
    except UnsupportedHardwareError:
        pytest.fail("factory routed away from x86_64 branch")
