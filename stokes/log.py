"""Structured logging for the desktop, the agent and the tools.

Usage::

    from stokes.log import get_logger
    log = get_logger(__name__)
    log.info("project opened", extra=fields(path=str(path), nodes=count))

Rules (docs/engineering/coding-standards.md): never log file contents, tokens,
keys or licence payloads; paths and counts are fine.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

_FIELDS_KEY = "stokes_fields"
_configured = False


def fields(**values: Any) -> dict[str, Any]:  # Any: free-form structured fields
    """Wrap keyword fields for the ``extra`` argument of a log call."""
    return {_FIELDS_KEY: values}


class JsonFormatter(logging.Formatter):
    """One JSON object per line; stable keys first."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, _FIELDS_KEY, None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable variant for development terminals."""

    def format(self, record: logging.LogRecord) -> str:
        base = f"{record.levelname:<7} {record.name}: {record.getMessage()}"
        extra = getattr(record, _FIELDS_KEY, None)
        if isinstance(extra, dict) and extra:
            base += " " + " ".join(f"{k}={v}" for k, v in extra.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure(
    level: str | None = None, *, json_lines: bool | None = None, stream: TextIO | None = None
) -> None:
    """Configure the root ``stokes`` logger once. Environment: STOKES_LOG_LEVEL, STOKES_LOG_JSON."""
    global _configured  # noqa: PLW0603 - single process-wide configuration
    if _configured:
        return
    level_name = level or os.environ.get("STOKES_LOG_LEVEL", "INFO")
    use_json = (
        json_lines
        if json_lines is not None
        else os.environ.get("STOKES_LOG_JSON", "1") not in {"0", "false", "no"}
    )
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if use_json else TextFormatter())
    root = logging.getLogger("stokes")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level_name.upper())
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``stokes`` hierarchy, configuring lazily."""
    configure()
    return logging.getLogger(name if name.startswith("stokes") else f"stokes.{name}")
