"""ToolGateway: the single entry point for tool calls (ADR-0018..0020).

Order of checks for every call: known tool -> principal still active -> role allowed ->
lookup lockout / pending cap -> strict argument validation -> scoped execution.
READ_ONLY tools run in read-only transactions. MUTATING tools only create a pending
approval request; they never execute here.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Connection, Engine, insert, select, text

from hotel_agent.db.models import ApprovalRequest, StaffMember
from hotel_agent.ops.audit import audit, mask_arguments
from hotel_agent.ops.principal import Principal, business_date, business_now
from hotel_agent.ops.tools import TOOLS, Access, ToolContext, ToolError, ToolErrorCode, ToolSpec

APPROVAL_TTL = timedelta(minutes=30)
MAX_FAILED_LOOKUPS = 5
MAX_PENDING_PER_SESSION = 10
MAX_TOOL_NAME_LOG = 64

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_hash(arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validation_summary(exc: ValidationError) -> str:
    """Field names and error kinds only — never echo the submitted values."""
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "arguments"
        parts.append(f"{loc}: {err.get('type', 'invalid')}")
    return "; ".join(parts)


@dataclass(slots=True)
class SessionState:
    failed_lookups: int = 0
    pending_created: int = 0


class ToolGateway:
    def __init__(self, engine: Engine, principal: Principal, clock: Clock = utc_now) -> None:
        self._engine = engine
        self.principal = principal
        self._clock = clock
        self.session = SessionState()

    # --- public ---

    @staticmethod
    def list_tools() -> list[ToolSpec]:
        return list(TOOLS.values())

    def call(self, name: Any, arguments: Any) -> dict[str, Any]:
        start = time.perf_counter()
        tool_name = name if isinstance(name, str) else repr(name)
        audit(
            "tool.requested",
            tool=tool_name[:MAX_TOOL_NAME_LOG],
            staff_code=self.principal.staff_code,
            role=self.principal.role.value,
            arguments=mask_arguments(arguments),
        )
        try:
            result = self._call(tool_name, arguments)
        except ToolError as exc:
            result = self._error(tool_name, exc.code, exc.message)
        except Exception as exc:  # never leak internals to the caller
            audit(
                "tool.internal_error", tool=tool_name[:MAX_TOOL_NAME_LOG], error=type(exc).__name__
            )
            result = self._error(tool_name, ToolErrorCode.INTERNAL_ERROR, "the tool failed")
        latency = round((time.perf_counter() - start) * 1000, 2)
        audit(
            "tool.result",
            tool=tool_name[:MAX_TOOL_NAME_LOG],
            staff_code=self.principal.staff_code,
            ok=result["ok"],
            code=result.get("error", {}).get("code"),
            status=result.get("status"),
            latency_ms=latency,
        )
        result["latency_ms"] = latency
        return result

    # --- internals ---

    def _error(self, tool: str, code: ToolErrorCode, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "tool": tool[:MAX_TOOL_NAME_LOG],
            "error": {"code": code.value, "message": message},
        }

    def _deny(self, tool: str, reason: str, code: ToolErrorCode, message: str) -> ToolError:
        audit(
            "tool.authorization",
            tool=tool[:MAX_TOOL_NAME_LOG],
            staff_code=self.principal.staff_code,
            role=self.principal.role.value,
            decision="denied",
            reason=reason,
        )
        return ToolError(code, message)

    def _context(self, conn: Connection) -> ToolContext:
        return ToolContext(
            principal=self.principal,
            business_date=business_date(conn),
            business_now=business_now(conn, self.principal.hotel_timezone),
            now=self._clock(),
        )

    def _call(self, name: str, arguments: Any) -> dict[str, Any]:
        spec = TOOLS.get(name)
        if spec is None:
            raise self._deny(name, "unknown_tool", ToolErrorCode.UNKNOWN_TOOL, "no such tool")
        if not self._principal_active():
            raise self._deny(
                name, "inactive_principal", ToolErrorCode.FORBIDDEN, "staff member is not active"
            )
        if self.principal.role not in spec.allowed_roles:
            raise self._deny(
                name,
                "role_not_permitted",
                ToolErrorCode.FORBIDDEN,
                f"role {self.principal.role.value} may not use {name}",
            )
        if spec.lookup and self.session.failed_lookups >= MAX_FAILED_LOOKUPS:
            raise self._deny(
                name,
                "lookup_lockout",
                ToolErrorCode.RATE_LIMITED,
                "too many failed reservation lookups in this session",
            )
        if (
            spec.access is Access.MUTATING
            and self.session.pending_created >= MAX_PENDING_PER_SESSION
        ):
            raise self._deny(
                name,
                "pending_cap",
                ToolErrorCode.RATE_LIMITED,
                "too many requests awaiting approval in this session",
            )
        if not isinstance(arguments, dict):
            raise ToolError(ToolErrorCode.INVALID_ARGUMENTS, "arguments must be a JSON object")
        try:
            args = spec.args_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolError(ToolErrorCode.INVALID_ARGUMENTS, _validation_summary(exc)) from None
        audit(
            "tool.authorization",
            tool=name,
            staff_code=self.principal.staff_code,
            role=self.principal.role.value,
            decision="allowed",
            access=spec.access.value,
            sensitive=spec.sensitive,
        )
        try:
            if spec.access is Access.READ_ONLY:
                return self._run_read_only(spec, args)
            return self._request_approval(spec, args)
        except ToolError as exc:
            if spec.lookup and exc.code is ToolErrorCode.NOT_FOUND:
                self.session.failed_lookups += 1
            raise

    def _principal_active(self) -> bool:
        with self._engine.connect() as conn:
            active = conn.execute(
                select(StaffMember.is_active).where(
                    StaffMember.id == self.principal.staff_id,
                    StaffMember.hotel_id == self.principal.hotel_id,
                )
            ).scalar()
        return bool(active)

    def _run_read_only(self, spec: ToolSpec, args: Any) -> dict[str, Any]:
        if spec.run is None:
            raise ToolError(ToolErrorCode.INTERNAL_ERROR, "tool has no implementation")
        with self._engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                data = spec.run(conn, self._context(conn), args)
            finally:
                trans.rollback()
        return {"ok": True, "tool": spec.name, "access": spec.access.value, "data": data}

    def _request_approval(self, spec: ToolSpec, args: Any) -> dict[str, Any]:
        if spec.prepare is None:
            raise ToolError(ToolErrorCode.INTERNAL_ERROR, "tool has no implementation")
        with self._engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                ctx = self._context(conn)
                prepared = spec.prepare(conn, ctx, args)
            finally:
                trans.rollback()
        stored = {
            **prepared.stored_arguments,
            "_approver_roles": sorted(r.value for r in prepared.approver_roles),
        }
        public_id = secrets.token_hex(12)
        now = self._clock()
        expires = now + APPROVAL_TTL
        with self._engine.begin() as conn:
            conn.execute(
                insert(ApprovalRequest).values(
                    public_id=public_id,
                    hotel_id=self.principal.hotel_id,
                    tool_name=spec.name,
                    arguments=stored,
                    arguments_sha256=canonical_hash(stored),
                    summary=prepared.summary,
                    requested_by=self.principal.staff_id,
                    requested_at=now,
                    expires_at=expires,
                    status="pending",
                )
            )
        self.session.pending_created += 1
        audit(
            "approval.created",
            approval_id=public_id,
            tool=spec.name,
            staff_code=self.principal.staff_code,
            required_roles=stored["_approver_roles"],
            sensitive=spec.sensitive,
        )
        return {
            "ok": True,
            "tool": spec.name,
            "access": spec.access.value,
            "status": "pending_approval",
            "approval": {
                "approval_id": public_id,
                "summary": prepared.summary,
                "expires_at": expires.isoformat(),
                "required_approver_roles": stored["_approver_roles"],
                "note": "Nothing has been executed. A human approver must approve this request.",
            },
        }
