from __future__ import annotations

import json
import logging

from hotel_agent.logging_config import JsonFormatter, request_id_var


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, "hello %s", ("x",), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_json_object() -> None:
    out = json.loads(JsonFormatter().format(_record(rows=3)))
    assert out["message"] == "hello x"
    assert out["level"] == "INFO"
    assert out["logger"] == "test.logger"
    assert out["rows"] == 3
    assert "request_id" not in out


def test_json_formatter_includes_request_id_from_context() -> None:
    token = request_id_var.set("req-123")
    try:
        out = json.loads(JsonFormatter().format(_record()))
    finally:
        request_id_var.reset(token)
    assert out["request_id"] == "req-123"


def test_json_formatter_drops_ansi_duplicate_fields() -> None:
    out = json.loads(JsonFormatter().format(_record(color_message="\x1b[36mhi\x1b[0m")))
    assert "color_message" not in out


def test_json_formatter_includes_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    out = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in out["exception"]
