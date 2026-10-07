"""Audit logging for tool calls and approvals, with per-field argument masking.

Never logged: guest names, free-text notes, full confirmation codes, unknown argument
values, secrets. Logged: tool, principal (staff code + role), masked arguments,
authorization and approval decisions, outcome codes, latency.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

audit_logger = logging.getLogger("hotel_agent.ops.audit")

_REDACT = frozenset({"guest_last_name"})
_TEXT = frozenset({"notes", "note"})
_PARTIAL = frozenset({"confirmation_code"})
_KNOWN = frozenset(
    {
        "confirmation_code", "guest_last_name", "room_number", "date", "status", "limit",
        "approval_id", "task_type", "priority", "notes", "note", "due_in_minutes", "reason",
    }
)  # fmt: skip


def mask_value(field: str, value: Any) -> Any:
    if field in _REDACT:
        return "<redacted>"
    if field in _TEXT:
        return f"<text len={len(value)}>" if isinstance(value, str) else "<text>"
    if field in _PARTIAL and isinstance(value, str):
        return value[:4] + "****" + value[-2:] if len(value) > 6 else "****"
    if isinstance(value, str) and len(value) > 64:
        return f"<str len={len(value)}>"
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    return f"<{type(value).__name__}>"


def mask_arguments(arguments: Mapping[str, Any] | Any) -> dict[str, Any] | str:
    if not isinstance(arguments, Mapping):
        return f"<non-object {type(arguments).__name__}>"
    masked: dict[str, Any] = {}
    for key, value in list(arguments.items())[:20]:
        name = str(key)[:40]
        # Unexpected fields: record the name only, never the value.
        masked[name] = mask_value(name, value) if name in _KNOWN else "<unexpected>"
    return masked


def audit(event: str, **fields: Any) -> None:
    audit_logger.info(event, extra={"event": event, **fields})
