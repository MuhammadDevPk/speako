"""AppContext: constructed once in ``__main__``, held for the process lifetime.

Everything the app needs to run is wired here — dependencies flow one way
(config → components → app), with no globals and no service locator.
"""

from __future__ import annotations

import contextlib
import queue
import threading
from dataclasses import dataclass
from typing import Final

from .audio import AudioCapturer, HotkeyListener, RecordEvent
from .config import AppConfig, ProviderId
from .output import OutputInjector
from .transcribe import BaseTranscriber, KeyManager, TranscriberDispatcher
from .transcribe.gemini_engine import GeminiTranscriber
from .transcribe.groq_engine import GroqTranscriber
from .transcribe.local import LocalEngineFactory, UnsupportedHardwareError
from .ui import HudEvent, HudLevelEvent
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
    # Producer→consumer channel to the HUD. Always present so the runtime
    # code path is uniform; when the HUD is disabled the entrypoint drains
    # this queue on its own to prevent unbounded growth.
    hud_events: queue.Queue[HudEvent]
    hud_enabled: bool
    stop: threading.Event


def build_app_context(
    config: AppConfig,
    hotkey_sink: queue.Queue[RecordEvent],
    hud_events: queue.Queue[HudEvent],
    stop: threading.Event,
) -> AppContext:
    # The capturer publishes RMS level updates by wrapping each float into
    # a HudLevelEvent before it hits the shared bus.
    level_sink: queue.Queue[float] = queue.Queue(maxsize=8)

    capturer = AudioCapturer(config.audio, level_sink=level_sink)
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

    # Adapter thread: float → HudLevelEvent. Runs as long as ``stop`` is
    # clear. Kept tiny so it never touches any transcription state.
    threading.Thread(
        target=_pump_levels,
        args=(level_sink, hud_events, stop),
        name="speako-level-pump",
        daemon=True,
    ).start()

    return AppContext(
        config=config,
        capturer=capturer,
        hotkey=hotkey,
        dispatcher=dispatcher,
        injector=injector,
        keys=keys,
        hud_events=hud_events,
        hud_enabled=config.ui.enabled,
        stop=stop,
    )


def _pump_levels(
    src: queue.Queue[float],
    dst: queue.Queue[HudEvent],
    stop: threading.Event,
) -> None:
    while not stop.is_set():
        try:
            level = src.get(timeout=0.1)
        except queue.Empty:
            continue
        # HUD consumer is behind — dropping level events is invisible.
        with contextlib.suppress(queue.Full):
            dst.put_nowait(HudLevelEvent(level=level))


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
