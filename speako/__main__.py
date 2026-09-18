"""CLI entrypoint. Wires config → context → main loop."""

from __future__ import annotations

import argparse
import queue
import signal
import sys
import threading
from pathlib import Path
from typing import Final

from .app import build_app_context
from .audio import RecordEvent
from .config import ConfigError, load_config
from .runtime import run
from .util.logging import configure_logging, get_logger

_log: Final = get_logger(__name__)


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

    events: queue.Queue[RecordEvent] = queue.Queue(maxsize=64)
    stop = threading.Event()

    try:
        app = build_app_context(config, events)
    except RuntimeError as exc:
        _log.error("startup_failed", error=str(exc))
        return 1

    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    try:
        run(app, events, stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


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
    return parser.parse_args(argv)


def _cli_overrides(args: argparse.Namespace) -> dict[str, object]:
    logging_over: dict[str, str] = {}
    if args.log_level:
        logging_over["level"] = args.log_level
    if args.log_format:
        logging_over["format"] = args.log_format
    return {"logging": logging_over} if logging_over else {}


if __name__ == "__main__":
    raise SystemExit(main())
