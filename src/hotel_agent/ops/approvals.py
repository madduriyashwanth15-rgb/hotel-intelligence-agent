"""Human approval channel (ADR-0019). NOT exposed through MCP.

A decision requires: same hotel, pending and unexpired request, an active approver who is
not the requester (four-eyes), and approver authority for the tool/amount. Approval
executes the stored action exactly once in the same transaction (row lock + status
transition), after verifying the stored arguments were not altered.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import Engine, select, update

from hotel_agent.db.models import ApprovalRequest, StaffMember, StaffRole
from hotel_agent.ops.audit import audit
from hotel_agent.ops.gateway import Clock, canonical_hash, utc_now
from hotel_agent.ops.principal import (
    Principal,
    PrincipalError,
    business_date,
    business_now,
    load_principal,
)
from hotel_agent.ops.tools import TOOLS, ApprovalInfo, ToolContext, ToolError, ToolErrorCode


class ApprovalService:
    def __init__(self, engine: Engine, clock: Clock = utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def list_pending(self, approver: Principal) -> list[dict[str, Any]]:
        now = self._clock()
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    ApprovalRequest.public_id,
                    ApprovalRequest.tool_name,
                    ApprovalRequest.summary,
                    ApprovalRequest.expires_at,
                    ApprovalRequest.arguments,
                    StaffMember.staff_code,
                )
                .join(StaffMember, StaffMember.id == ApprovalRequest.requested_by)
                .where(
                    ApprovalRequest.hotel_id == approver.hotel_id,
                    ApprovalRequest.status == "pending",
                    ApprovalRequest.expires_at > now,
                )
                .order_by(ApprovalRequest.requested_at)
            ).all()
        out: list[dict[str, Any]] = []
        for r in rows:
            stored: dict[str, Any] = dict(r[4])
            roles = [str(role) for role in stored.get("_approver_roles", [])]
            out.append(
                {
                    "approval_id": r[0],
                    "tool": r[1],
                    "summary": r[2],
                    "expires_at": r[3].isoformat(),
                    "requested_by": r[5],
                    "required_approver_roles": roles,
                    "you_may_approve": approver.role.value in roles and r[5] != approver.staff_code,
                }
            )
        return out

    def decide(
        self, approver: Principal, approval_id: str, approve: bool, note: str | None = None
    ) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            outcome = self._decide(approver, approval_id, approve, note)
        except ToolError as exc:
            audit(
                "approval.decision_refused",
                approval_id=approval_id[:32],
                approver=approver.staff_code,
                role=approver.role.value,
                code=exc.code.value,
                reason=exc.message,
            )
            return {"ok": False, "error": {"code": exc.code.value, "message": exc.message}}
        outcome["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
        return outcome

    def _decide(
        self, approver: Principal, approval_id: str, approve: bool, note: str | None
    ) -> dict[str, Any]:
        if note is not None and len(note) > 500:
            raise ToolError(ToolErrorCode.INVALID_ARGUMENTS, "note too long")
        now = self._clock()
        with self._engine.begin() as conn:
            approver_active = conn.execute(
                select(StaffMember.is_active).where(
                    StaffMember.id == approver.staff_id, StaffMember.hotel_id == approver.hotel_id
                )
            ).scalar()
            if not approver_active:
                raise ToolError(ToolErrorCode.FORBIDDEN, "approver is not an active staff member")
            row = conn.execute(
                select(
                    ApprovalRequest.id,
                    ApprovalRequest.status,
                    ApprovalRequest.expires_at,
                    ApprovalRequest.requested_by,
                    ApprovalRequest.arguments,
                    ApprovalRequest.arguments_sha256,
                    ApprovalRequest.tool_name,
                )
                .where(
                    ApprovalRequest.public_id == approval_id,
                    ApprovalRequest.hotel_id == approver.hotel_id,
                )
                .with_for_update()
            ).first()
            if row is None:
                raise ToolError(ToolErrorCode.NOT_FOUND, "approval request not found")
            if row.status != "pending":
                raise ToolError(ToolErrorCode.CONFLICT, f"request is already {row.status}")
            if row.expires_at <= now:
                conn.execute(
                    update(ApprovalRequest)
                    .where(ApprovalRequest.id == row.id)
                    .values(status="expired", decided_at=now)
                )
                audit("approval.expired", approval_id=approval_id, tool=row.tool_name)
                return {
                    "ok": False,
                    "error": {"code": ToolErrorCode.EXPIRED.value, "message": "request expired"},
                }
            if row.requested_by == approver.staff_id:
                raise ToolError(ToolErrorCode.FORBIDDEN, "you cannot approve your own request")
            stored = dict(row.arguments)
            if canonical_hash(stored) != row.arguments_sha256:
                raise ToolError(ToolErrorCode.CONFLICT, "stored arguments failed integrity check")
            allowed = {StaffRole(r) for r in stored.get("_approver_roles", [])}
            if approver.role not in allowed:
                raise ToolError(
                    ToolErrorCode.FORBIDDEN,
                    f"role {approver.role.value} may not approve this request",
                )

            decision = {"decided_by": approver.staff_id, "decided_at": now, "decision_note": note}
            if not approve:
                conn.execute(
                    update(ApprovalRequest)
                    .where(ApprovalRequest.id == row.id)
                    .values(status="rejected", **decision)
                )
                audit(
                    "approval.decided",
                    approval_id=approval_id,
                    tool=row.tool_name,
                    approver=approver.staff_code,
                    decision="rejected",
                )
                return {"ok": True, "approval_id": approval_id, "status": "rejected"}

            spec = TOOLS[row.tool_name]
            if spec.execute is None:
                raise ToolError(ToolErrorCode.INTERNAL_ERROR, "tool cannot be executed")
            requester_code = conn.execute(
                select(StaffMember.staff_code).where(StaffMember.id == row.requested_by)
            ).scalar_one()
            try:
                requester = load_principal(conn, requester_code)
            except PrincipalError:
                raise ToolError(
                    ToolErrorCode.FORBIDDEN, "the requester is no longer an active staff member"
                ) from None
            ctx = ToolContext(
                principal=requester,
                business_date=business_date(conn),
                business_now=business_now(conn, requester.hotel_timezone),
                now=now,
            )
            audit(
                "approval.decided",
                approval_id=approval_id,
                tool=row.tool_name,
                approver=approver.staff_code,
                decision="approved",
            )
            savepoint = conn.begin_nested()
            try:
                result = spec.execute(conn, ctx, stored, ApprovalInfo(row.id, approver.staff_code))
                savepoint.commit()
                status, payload = "executed", result
            except ToolError as exc:
                savepoint.rollback()
                status, payload = (
                    "failed",
                    {"error": {"code": exc.code.value, "message": exc.message}},
                )
            conn.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.id == row.id, ApprovalRequest.status == "pending")
                .values(status=status, executed_at=now, result=payload, **decision)
            )
        audit(
            "approval.executed",
            approval_id=approval_id,
            tool=row.tool_name,
            outcome=status,
            approver=approver.staff_code,
        )
        return {
            "ok": status == "executed",
            "approval_id": approval_id,
            "status": status,
            "result": payload,
        }
