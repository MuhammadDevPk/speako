"""AppContext: constructed once in ``__main__``, held for the process lifetime.

Everything the app needs to run is wired here — dependencies flow one way
(config → components → app), with no globals and no service locator.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Final

from .audio import AudioCapturer, HotkeyListener, RecordEvent
from .config import AppConfig, ProviderId
from .output import OutputInjector
from .transcribe import BaseTranscriber, KeyManager, TranscriberDispatcher
from .transcribe.gemini_engine import GeminiTranscriber
from .transcribe.groq_engine import GroqTranscriber
from .transcribe.local import LocalEngineFactory, UnsupportedHardwareError
from .util.logging import get_logger

_log: Final = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AppContext:
    config: AppConfig
    capturer: AudioCapturer
    hotkey: HotkeyListener
    dispatcher: TranscriberDispatcher
    injector: OutputInjector
    keys: KeyManager


def build_app_context(
    config: AppConfig,
    hotkey_sink: queue.Queue[RecordEvent],
) -> AppContext:
    capturer = AudioCapturer(config.audio)
    hotkey = HotkeyListener(config.hotkey, hotkey_sink)
    keys = KeyManager(config.providers, config.cooldown)
    transcribers = _build_transcribers(config)
    _validate_priority(config.providers.priority, transcribers)
    dispatcher = TranscriberDispatcher(
        priority=config.providers.priority,
        transcribers=transcribers,
        keys=keys,
    )
    injector = OutputInjector(config.output)

    return AppContext(
        config=config,
        capturer=capturer,
        hotkey=hotkey,
        dispatcher=dispatcher,
        injector=injector,
        keys=keys,
    )


def _build_transcribers(config: AppConfig) -> dict[ProviderId, BaseTranscriber]:
    out: dict[ProviderId, BaseTranscriber] = {}
    providers = config.providers

    if providers.groq is not None and ProviderId.GROQ in providers.priority:
        try:
            out[ProviderId.GROQ] = GroqTranscriber(model=providers.groq.model)
        except RuntimeError as exc:
            _log.warning("provider_disabled", provider="groq", reason=str(exc))

    if providers.gemini is not None and ProviderId.GEMINI in providers.priority:
        try:
            out[ProviderId.GEMINI] = GeminiTranscriber(model=providers.gemini.model)
        except RuntimeError as exc:
            _log.warning("provider_disabled", provider="gemini", reason=str(exc))

    if providers.local is not None and ProviderId.LOCAL in providers.priority:
        try:
            out[ProviderId.LOCAL] = LocalEngineFactory.detect(providers.local)
        except (RuntimeError, UnsupportedHardwareError) as exc:
            _log.warning("provider_disabled", provider="local", reason=str(exc))

    return out


def _validate_priority(
    priority: tuple[ProviderId, ...],
    transcribers: dict[ProviderId, BaseTranscriber],
) -> None:
    live = [p for p in priority if p in transcribers]
    if not live:
        raise RuntimeError(
            "no usable providers — check dependencies and API keys. "
            f"priority={list(priority)}"
        )
    _log.info("dispatch_chain", providers=[p.value for p in live])
