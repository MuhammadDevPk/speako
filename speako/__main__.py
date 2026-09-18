"""CLI entrypoint. Wires config → context → runtime + HUD."""

from __future__ import annotations

import argparse
import contextlib
import queue
import signal
import sys
import threading
from pathlib import Path
from typing import Final

from .app import AppContext, build_app_context
from .audio import RecordEvent
from .config import ConfigError, load_config
from .runtime import run as runtime_run
from .ui import Hud, HudEvent, HudShutdown, HudUnavailableError
from .util.logging import configure_logging, get_logger

_log: Final = get_logger(__name__)

_HUD_QUEUE_MAX: Final[int] = 128


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    overrides = _cli_overrides(args)

    try:
        config = load_config(project_config=args.config, overrides=overrides)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    configure_logging(level=config.logging.level, fmt=config.logging.format)
    _log.info("config_loaded", sources=list(config.source_paths))

    hotkey_events: queue.Queue[RecordEvent] = queue.Queue(maxsize=64)
    hud_events: queue.Queue[HudEvent] = queue.Queue(maxsize=_HUD_QUEUE_MAX)
    stop = threading.Event()

    try:
        app = build_app_context(config, hotkey_events, hud_events, stop)
    except RuntimeError as exc:
        _log.error("startup_failed", error=str(exc))
        return 1

    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    if config.ui.enabled:
        return _run_with_hud(app, hotkey_events, hud_events, stop)
    return _run_headless(app, hotkey_events, stop)


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------


def _run_headless(
    app: AppContext,
    hotkey_events: queue.Queue[RecordEvent],
    stop: threading.Event,
) -> int:
    """No overlay. Runtime blocks on the main thread; SIGINT stops it."""
    try:
        runtime_run(app, hotkey_events, stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


def _run_with_hud(
    app: AppContext,
    hotkey_events: queue.Queue[RecordEvent],
    hud_events: queue.Queue[HudEvent],
    stop: threading.Event,
) -> int:
    """HUD on the main thread (mandatory), runtime on a worker thread.

    Startup:
        1. Spawn the runtime worker.
        2. Enter the Tk mainloop.
    Shutdown paths:
        a. User quits → runtime loop's ``stop`` becomes set → its
           ``finally`` pushes ``HudShutdown`` → Tk mainloop exits.
        b. Tk closes for another reason → ``stop`` gets set below → runtime
           worker exits → we join it.
    """
    worker = threading.Thread(
        target=_runtime_worker,
        args=(app, hotkey_events, stop),
        name="speako-runtime",
        daemon=False,
    )
    worker.start()

    hud = Hud(app.config.ui, hud_events)
    try:
        try:
            with contextlib.suppress(KeyboardInterrupt):
                hud.run()
        except HudUnavailableError as exc:
            _log.warning("hud_unavailable_falling_back_headless", reason=str(exc))
            # Stay in the app — the worker thread is already running and
            # will keep transcribing without the overlay.
            stop.wait()
    finally:
        stop.set()
        # Nudge the worker's blocking Queue.get so it exits promptly if
        # it happens to be idle.
        with contextlib.suppress(queue.Full):
            hud_events.put_nowait(HudShutdown())
        worker.join(timeout=5.0)
        if worker.is_alive():
            _log.warning("runtime_worker_did_not_exit")
    return 0


def _runtime_worker(
    app: AppContext,
    hotkey_events: queue.Queue[RecordEvent],
    stop: threading.Event,
) -> None:
    try:
        runtime_run(app, hotkey_events, stop)
    except Exception:
        _log.exception("runtime_worker_crashed")
        stop.set()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="speako")
    parser.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="override logging.level from config",
    )
    parser.add_argument(
        "--log-format",
        choices=["json", "text"],
        default=None,
        help="override logging.format from config",
    )
    parser.add_argument(
        "--no-hud",
        action="store_true",
        help="disable the floating HUD overlay (headless mode)",
    )
    return parser.parse_args(argv)


def _cli_overrides(args: argparse.Namespace) -> dict[str, object]:
    overrides: dict[str, object] = {}
    logging_over: dict[str, str] = {}
    if args.log_level:
        logging_over["level"] = args.log_level
    if args.log_format:
        logging_over["format"] = args.log_format
    if logging_over:
        overrides["logging"] = logging_over
    if args.no_hud:
        overrides["ui"] = {"enabled": False}
    return overrides


if __name__ == "__main__":
    raise SystemExit(main())
