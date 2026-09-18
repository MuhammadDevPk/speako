"""Structured logging setup.

One module-level factory produces a configured logger. All modules obtain
their logger via ``get_logger(__name__)``; no other logging configuration
happens anywhere else in the codebase.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Final, Literal

_CONFIGURED: bool = False
_LEVELS: Final[frozenset[str]] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class _JsonFormatter(logging.Formatter):
    """One JSON object per log record; ``extra=`` fields are merged in."""

    _RESERVED: Final[frozenset[str]] = frozenset(
        {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "asctime", "taskName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", fmt: Literal["json", "text"] = "json") -> None:
    """Idempotently configure the root logger. Safe to call more than once."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_upper = level.upper()
    if level_upper not in _LEVELS:
        raise ValueError(f"unknown log level: {level!r}")

    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level_upper)
    _CONFIGURED = True


class StructuredLogger:
    """Thin adapter over ``logging.Logger`` that promotes ``**kwargs`` to
    the record's ``extra`` dict, so call sites can write:

        _log.info("event_name", key="value", count=3)

    Kwargs shadow stdlib names (``exc_info``, ``stack_info``, ``stacklevel``)
    when they are those names; anything else flows into ``extra``.
    """

    __slots__ = ("_logger",)

    _PASSTHROUGH: Final[frozenset[str]] = frozenset({"exc_info", "stack_info", "stacklevel"})

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _emit(self, level: int, msg: str, kwargs: dict[str, Any]) -> None:
        passthrough: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key in self._PASSTHROUGH:
                passthrough[key] = value
            else:
                extra[key] = value
        self._logger.log(level, msg, extra=extra or None, **passthrough)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, msg, kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.INFO, msg, kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.WARNING, msg, kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.ERROR, msg, kwargs)

    def exception(self, msg: str, **kwargs: Any) -> None:
        kwargs.setdefault("exc_info", True)
        self._emit(logging.ERROR, msg, kwargs)

    def critical(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.CRITICAL, msg, kwargs)


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(logging.getLogger(name))
