"""Structured JSON logging with a per-request correlation id."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Literal

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else was passed via `extra=` and is emitted.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "color_message"}  # uvicorn adds ANSI-coloured duplicates
)


class JsonFormatter(logging.Formatter):
    """Render each record as a single JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _CurrentStreamHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever sys.stdout / sys.stderr is at emit time (robust if swapped)."""

    def __init__(self, target: Literal["stdout", "stderr"]) -> None:
        super().__init__(getattr(sys, target))
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = getattr(sys, self._target)
        super().emit(record)


def configure_logging(level: str = "INFO", stream: Literal["stdout", "stderr"] = "stdout") -> None:
    """Install the JSON formatter on the root logger (idempotent).

    Use stream="stderr" for stdio protocol servers (MCP), where stdout carries protocol
    messages and must never receive log lines.
    """
    handler = _CurrentStreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # Uvicorn's access log duplicates our request log; keep its error log.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
