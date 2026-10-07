"""Tool definitions: classification, permissions, and implementations (ADR-0019..0021).

READ_ONLY tools run inside read-only transactions. MUTATING tools have two halves:
`prepare` (validate + authorize-time checks, runs read-only, returns what a human must
approve) and `execute` (runs only from the human approval channel, re-validates).
Every query is scoped to the principal's hotel; other hotels' data is "not found".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Connection, and_, func, insert, select, update

from hotel_agent.db.models import (
    ApprovalRequest,
    Guest,
    HousekeepingTask,
    Refund,
    Reservation,
    Room,
    RoomType,
    StaffRole,
)
from hotel_agent.ops.principal import Principal
from hotel_agent.ops.refund import POLICY_REFERENCE, RefundQuote, StayForRefund, quote_refund
from hotel_agent.ops.schemas import (
    CalculateRefundArgs,
    CreateHousekeepingTaskArgs,
    GetApprovalStatusArgs,
    GetHousekeepingTasksArgs,
    GetOccupancyArgs,
    GetReservationArgs,
    GetRoomStatusArgs,
    RequestCancellationRefundArgs,
)

ACTIVE = ("confirmed", "checked_in", "checked_out")
REFUND_MANAGER_LIMIT = Decimal("50000.00")
OCCUPANCY_WINDOW_DAYS = 365


class Access(StrEnum):
    READ_ONLY = "READ_ONLY"
    MUTATING = "MUTATING"


class ToolErrorCode(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    NOT_ELIGIBLE = "not_eligible"
    RATE_LIMITED = "rate_limited"
    EXPIRED = "expired"
    INTERNAL_ERROR = "internal_error"


class ToolError(Exception):
    def __init__(self, code: ToolErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ToolContext:
    principal: Principal
    business_date: date
    business_now: datetime  # simulated hotel 'now' for reservation data
    now: datetime  # wall-clock UTC for the approval workflow (injectable for tests)


@dataclass(frozen=True, slots=True)
class Prepared:
    """What a mutating call would do; stored for a human to approve."""

    summary: str
    stored_arguments: dict[str, Any]  # no guest names; includes server-side context
    approver_roles: frozenset[StaffRole]


@dataclass(frozen=True, slots=True)
class ApprovalInfo:
    """Passed to `execute`: which approval authorised this run and who approved it."""

    approval_row_id: int
    approver_code: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    access: Access
    sensitive: bool
    args_model: type[BaseModel]
    allowed_roles: frozenset[StaffRole]
    lookup: bool = False  # counts towards the failed-lookup lockout
    run: Callable[[Connection, ToolContext, Any], dict[str, Any]] | None = None
    prepare: Callable[[Connection, ToolContext, Any], Prepared] | None = None
    execute: (
        Callable[[Connection, ToolContext, dict[str, Any], ApprovalInfo], dict[str, Any]] | None
    ) = None
    tags: tuple[str, ...] = field(default=())


# --- helpers ---


def _initials(first: str, last: str) -> str:
    return f"{first[:1].upper()}.{last[:1].upper()}."


def _find_reservation(conn: Connection, ctx: ToolContext, code: str, last_name: str) -> Any:
    """Code AND last name must match, within the principal's hotel (SOP-PRIVACY-001)."""
    row = conn.execute(
        select(
            Reservation.id,
            Reservation.confirmation_code,
            Reservation.status,
            Reservation.check_in_date,
            Reservation.check_out_date,
            Reservation.adults,
            Reservation.children,
            Reservation.channel,
            Reservation.rate_per_night,
            Reservation.total_amount,
            RoomType.code.label("room_type_code"),
            Room.room_number,
            Guest.first_name,
            Guest.last_name,
            Guest.loyalty_tier,
        )
        .join(Guest, Guest.id == Reservation.guest_id)
        .join(RoomType, RoomType.id == Reservation.room_type_id)
        .outerjoin(Room, Room.id == Reservation.room_id)
        .where(
            Reservation.hotel_id == ctx.principal.hotel_id,
            Reservation.confirmation_code == code,
            func.lower(Guest.last_name) == last_name.lower(),
        )
    ).first()
    if row is None:
        # Same message for wrong code, wrong name or other hotel: no existence oracle.
        raise ToolError(ToolErrorCode.NOT_FOUND, "no reservation matches that code and last name")
    return row


def _stay(row: Any) -> StayForRefund:
    return StayForRefund(
        status=row.status,
        channel=row.channel,
        room_type_code=row.room_type_code,
        check_in_date=row.check_in_date,
        check_out_date=row.check_out_date,
        rate_per_night=row.rate_per_night,
        total_amount=row.total_amount,
    )


def _room(conn: Connection, ctx: ToolContext, room_number: str) -> Any:
    row = conn.execute(
        select(Room.id, Room.room_number, Room.floor, Room.housekeeping_status, RoomType.code)
        .join(RoomType, RoomType.id == Room.room_type_id)
        .where(Room.hotel_id == ctx.principal.hotel_id, Room.room_number == room_number)
    ).first()
    if row is None:
        raise ToolError(ToolErrorCode.NOT_FOUND, f"room {room_number} not found")
    return row


def _quote_dict(quote: RefundQuote) -> dict[str, Any]:
    return {
        "eligible": quote.eligible,
        "rule": quote.rule,
        "reason": quote.reason,
        "cancellation_charge": str(quote.cancellation_charge),
        "refund_amount": str(quote.refund_amount),
        "currency": "INR",
        "hours_before_arrival": quote.hours_before_arrival,
        "policy_reference": POLICY_REFERENCE,
    }


def _already_refunded(conn: Connection, reservation_id: int) -> bool:
    return (
        conn.execute(select(Refund.id).where(Refund.reservation_id == reservation_id)).first()
        is not None
    )


def _pending_for(conn: Connection, ctx: ToolContext, tool: str, key: str, value: Any) -> bool:
    return (
        conn.execute(
            select(ApprovalRequest.id).where(
                ApprovalRequest.hotel_id == ctx.principal.hotel_id,
                ApprovalRequest.tool_name == tool,
                ApprovalRequest.status == "pending",
                ApprovalRequest.expires_at > ctx.now,
                ApprovalRequest.arguments[key].astext == str(value),
            )
        ).first()
        is not None
    )


# --- read-only tools ---


def get_reservation(conn: Connection, ctx: ToolContext, args: GetReservationArgs) -> dict[str, Any]:
    row = _find_reservation(conn, ctx, args.confirmation_code, args.guest_last_name)
    return {
        "confirmation_code": row.confirmation_code,
        "status": row.status,
        "check_in_date": row.check_in_date.isoformat(),
        "check_out_date": row.check_out_date.isoformat(),
        "nights": (row.check_out_date - row.check_in_date).days,
        "room_type_code": row.room_type_code,
        "room_number": row.room_number,
        "adults": row.adults,
        "children": row.children,
        # Minimum identity data: initials and loyalty tier only.
        "guest": {
            "initials": _initials(row.first_name, row.last_name),
            "loyalty_tier": row.loyalty_tier,
        },
    }


def get_room_status(conn: Connection, ctx: ToolContext, args: GetRoomStatusArgs) -> dict[str, Any]:
    room = _room(conn, ctx, args.room_number)
    open_tasks = conn.execute(
        select(func.count())
        .select_from(HousekeepingTask)
        .where(
            HousekeepingTask.room_id == room.id,
            HousekeepingTask.status.in_(("open", "in_progress")),
        )
    ).scalar_one()
    occupied = conn.execute(
        select(func.count())
        .select_from(Reservation)
        .where(
            Reservation.room_id == room.id,
            Reservation.status.in_(ACTIVE),
            Reservation.check_in_date <= ctx.business_date,
            Reservation.check_out_date > ctx.business_date,
        )
    ).scalar_one()
    return {
        "room_number": room.room_number,
        "room_type_code": room[4],
        "floor": room.floor,
        "housekeeping_status": room.housekeeping_status,
        "is_out_of_order": room.housekeeping_status == "out_of_order",
        "occupied_tonight": occupied > 0,
        "open_tasks": open_tasks,
        "business_date": ctx.business_date.isoformat(),
    }


def get_occupancy(conn: Connection, ctx: ToolContext, args: GetOccupancyArgs) -> dict[str, Any]:
    day = args.date or ctx.business_date
    if abs((day - ctx.business_date).days) > OCCUPANCY_WINDOW_DAYS:
        raise ToolError(ToolErrorCode.INVALID_ARGUMENTS, "date must be within one year of today")
    hotel = ctx.principal.hotel_id
    total = conn.execute(
        select(func.count()).select_from(Room).where(Room.hotel_id == hotel)
    ).scalar_one()
    occupied = conn.execute(
        select(func.count())
        .select_from(Reservation)
        .where(
            Reservation.hotel_id == hotel,
            Reservation.status.in_(ACTIVE),
            Reservation.check_in_date <= day,
            Reservation.check_out_date > day,
        )
    ).scalar_one()
    out_of_order = conn.execute(
        select(func.count())
        .select_from(Room)
        .where(Room.hotel_id == hotel, Room.housekeeping_status == "out_of_order")
    ).scalar_one()
    pct = round(100 * occupied / total, 2) if total else 0.0
    return {
        "date": day.isoformat(),
        "occupied_rooms": occupied,
        "total_rooms": total,
        "occupancy_pct": pct,
        "out_of_order_rooms_now": out_of_order,
    }


def get_housekeeping_tasks(
    conn: Connection, ctx: ToolContext, args: GetHousekeepingTasksArgs
) -> dict[str, Any]:
    stmt = (
        select(
            HousekeepingTask.id,
            Room.room_number,
            HousekeepingTask.task_type,
            HousekeepingTask.priority,
            HousekeepingTask.status,
            HousekeepingTask.due_at,
            HousekeepingTask.created_by,
        )
        .join(Room, Room.id == HousekeepingTask.room_id)
        .where(HousekeepingTask.hotel_id == ctx.principal.hotel_id)
    )
    if args.status is not None:
        stmt = stmt.where(HousekeepingTask.status == args.status.value)
    if args.room_number is not None:
        stmt = stmt.where(Room.room_number == args.room_number)
    rows = conn.execute(
        stmt.order_by(HousekeepingTask.due_at.asc().nulls_last(), HousekeepingTask.id).limit(
            args.limit
        )
    ).all()
    return {
        "tasks": [
            {
                "task_id": r[0],
                "room_number": r[1],
                "task_type": r[2],
                "priority": r[3],
                "status": r[4],
                "due_at": r[5].isoformat() if r[5] else None,
                "source": "agent" if str(r[6]).startswith("agent:") else "system",
            }
            for r in rows
        ],
        "count": len(rows),
        "limit": args.limit,
    }


def calculate_refund(
    conn: Connection, ctx: ToolContext, args: CalculateRefundArgs
) -> dict[str, Any]:
    row = _find_reservation(conn, ctx, args.confirmation_code, args.guest_last_name)
    quote = quote_refund(_stay(row), ctx.business_now, ctx.principal.hotel_timezone)
    return {
        "confirmation_code": row.confirmation_code,
        "quote": _quote_dict(quote),
        "already_refunded": _already_refunded(conn, row.id),
        "note": "Quote only: no money is moved and the reservation is not changed.",
    }


def get_approval_status(
    conn: Connection, ctx: ToolContext, args: GetApprovalStatusArgs
) -> dict[str, Any]:
    stmt = select(
        ApprovalRequest.public_id,
        ApprovalRequest.tool_name,
        ApprovalRequest.summary,
        ApprovalRequest.status,
        ApprovalRequest.requested_at,
        ApprovalRequest.expires_at,
        ApprovalRequest.decided_at,
        ApprovalRequest.result,
        ApprovalRequest.requested_by,
    ).where(
        ApprovalRequest.hotel_id == ctx.principal.hotel_id,
        ApprovalRequest.public_id == args.approval_id,
    )
    row = conn.execute(stmt).first()
    if row is None or (row.requested_by != ctx.principal.staff_id and not ctx.principal.is_manager):
        raise ToolError(ToolErrorCode.NOT_FOUND, "approval request not found")
    status = row.status
    if status == "pending" and row.expires_at <= ctx.now:
        status = "expired"
    return {
        "approval_id": row.public_id,
        "tool": row.tool_name,
        "summary": row.summary,
        "status": status,
        "requested_at": row.requested_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "result": row.result,
    }


# --- mutating tools ---

HK_APPROVERS = frozenset(
    {StaffRole.HOUSEKEEPING_SUPERVISOR, StaffRole.DUTY_MANAGER, StaffRole.FRONT_OFFICE_MANAGER}
)


def prepare_housekeeping_task(
    conn: Connection, ctx: ToolContext, args: CreateHousekeepingTaskArgs
) -> Prepared:
    room = _room(conn, ctx, args.room_number)
    if room.housekeeping_status == "out_of_order" and args.task_type.value != "maintenance":
        raise ToolError(ToolErrorCode.CONFLICT, "room is out of order; only maintenance tasks")
    _check_no_duplicate_task(conn, room.id, args.task_type.value)
    if _pending_for(
        conn, ctx, "create_housekeeping_task", "room_task", f"{room.id}:{args.task_type.value}"
    ):
        raise ToolError(ToolErrorCode.CONFLICT, "an identical request is already awaiting approval")
    stored = {
        "room_number": args.room_number,
        "task_type": args.task_type.value,
        "priority": args.priority.value,
        "notes": args.notes,
        "due_in_minutes": args.due_in_minutes,
        "room_task": f"{room.id}:{args.task_type.value}",
    }
    summary = (
        f"Create a {args.priority.value}-priority {args.task_type.value} task for room "
        f"{args.room_number}, due {args.due_in_minutes} minutes after approval."
    )
    return Prepared(summary, stored, HK_APPROVERS)


def _check_no_duplicate_task(conn: Connection, room_id: int, task_type: str) -> None:
    duplicate = conn.execute(
        select(HousekeepingTask.id).where(
            HousekeepingTask.room_id == room_id,
            HousekeepingTask.task_type == task_type,
            HousekeepingTask.status.in_(("open", "in_progress")),
        )
    ).first()
    if duplicate is not None:
        raise ToolError(
            ToolErrorCode.CONFLICT, "an open task of this type already exists for the room"
        )


def execute_housekeeping_task(
    conn: Connection, ctx: ToolContext, stored: dict[str, Any], approval: ApprovalInfo
) -> dict[str, Any]:
    room = _room(conn, ctx, stored["room_number"])
    _check_no_duplicate_task(conn, room.id, stored["task_type"])
    now = ctx.now
    task_id = conn.execute(
        insert(HousekeepingTask)
        .values(
            hotel_id=ctx.principal.hotel_id,
            room_id=room.id,
            task_type=stored["task_type"],
            priority=stored["priority"],
            status="open",
            notes=stored["notes"],
            created_by=f"agent:{ctx.principal.staff_code}/approved:{approval.approver_code}",
            created_at=now,
            due_at=now + timedelta(minutes=int(stored["due_in_minutes"])),
        )
        .returning(HousekeepingTask.id)
    ).scalar_one()
    return {"task_id": task_id, "room_number": stored["room_number"], "status": "open"}


def _refund_approvers(amount: Decimal) -> frozenset[StaffRole]:
    if amount <= REFUND_MANAGER_LIMIT:
        return frozenset(
            {StaffRole.FRONT_OFFICE_MANAGER, StaffRole.DUTY_MANAGER, StaffRole.GENERAL_MANAGER}
        )
    return frozenset({StaffRole.GENERAL_MANAGER})


def prepare_cancellation_refund(
    conn: Connection, ctx: ToolContext, args: RequestCancellationRefundArgs
) -> Prepared:
    row = _find_reservation(conn, ctx, args.confirmation_code, args.guest_last_name)
    quote = quote_refund(_stay(row), ctx.business_now, ctx.principal.hotel_timezone)
    if not quote.eligible:
        raise ToolError(ToolErrorCode.NOT_ELIGIBLE, quote.reason)
    if _already_refunded(conn, row.id):
        raise ToolError(ToolErrorCode.CONFLICT, "this reservation has already been refunded")
    if _pending_for(conn, ctx, "request_cancellation_refund", "reservation_id", row.id):
        raise ToolError(
            ToolErrorCode.CONFLICT, "a refund for this reservation is awaiting approval"
        )
    stored = {
        # Server-side context: the caller never supplies the amount (ADR-0021).
        "reservation_id": row.id,
        "confirmation_code": row.confirmation_code,
        "reason": args.reason.value,
        "note": args.note,
        "quoted_refund_amount": str(quote.refund_amount),
        "quoted_rule": quote.rule,
    }
    summary = (
        f"Cancel reservation {row.confirmation_code} (check-in {row.check_in_date.isoformat()}) "
        f"and refund ₹{quote.refund_amount:,} under rule {quote.rule} ({POLICY_REFERENCE}); "
        f"reason: {args.reason.value}."
    )
    return Prepared(summary, stored, _refund_approvers(quote.refund_amount))


def execute_cancellation_refund(
    conn: Connection, ctx: ToolContext, stored: dict[str, Any], approval: ApprovalInfo
) -> dict[str, Any]:
    reservation_id = int(stored["reservation_id"])
    row = conn.execute(
        select(
            Reservation.id,
            Reservation.status,
            Reservation.channel,
            Reservation.check_in_date,
            Reservation.check_out_date,
            Reservation.rate_per_night,
            Reservation.total_amount,
            RoomType.code.label("room_type_code"),
        )
        .join(RoomType, RoomType.id == Reservation.room_type_id)
        .where(Reservation.id == reservation_id, Reservation.hotel_id == ctx.principal.hotel_id)
        .with_for_update(of=Reservation)
    ).first()
    if row is None:
        raise ToolError(ToolErrorCode.NOT_FOUND, "reservation no longer exists")
    quote = quote_refund(_stay(row), ctx.business_now, ctx.principal.hotel_timezone)
    if not quote.eligible:
        raise ToolError(ToolErrorCode.NOT_ELIGIBLE, quote.reason)
    if str(quote.refund_amount) != stored["quoted_refund_amount"]:
        raise ToolError(ToolErrorCode.CONFLICT, "the refund amount changed since the request")
    if _already_refunded(conn, reservation_id):
        raise ToolError(ToolErrorCode.CONFLICT, "this reservation has already been refunded")
    conn.execute(
        update(Reservation)
        .where(and_(Reservation.id == reservation_id, Reservation.status == "confirmed"))
        .values(status="cancelled", cancelled_at=ctx.business_now)
    )
    conn.execute(
        insert(Refund).values(
            hotel_id=ctx.principal.hotel_id,
            reservation_id=reservation_id,
            approval_request_id=approval.approval_row_id,
            amount=quote.refund_amount,
            currency="INR",
            policy_rule=quote.rule,
        )
    )
    return {
        "confirmation_code": stored["confirmation_code"],
        "reservation_status": "cancelled",
        "refund_amount": str(quote.refund_amount),
        "currency": "INR",
        "rule": quote.rule,
        "simulated": True,
    }


# --- registry ---

R = StaffRole
ALL_STAFF = frozenset(StaffRole)
DESK_AND_MANAGERS = frozenset(
    {R.FRONT_DESK, R.FRONT_OFFICE_MANAGER, R.DUTY_MANAGER, R.GENERAL_MANAGER}
)
HOUSEKEEPING_VIEW = frozenset(
    {
        R.FRONT_DESK,
        R.HOUSEKEEPING,
        R.HOUSEKEEPING_SUPERVISOR,
        R.FRONT_OFFICE_MANAGER,
        R.DUTY_MANAGER,
        R.GENERAL_MANAGER,
    }
)
HK_REQUESTERS = frozenset(
    {
        R.FRONT_DESK,
        R.HOUSEKEEPING,
        R.HOUSEKEEPING_SUPERVISOR,
        R.FRONT_OFFICE_MANAGER,
        R.DUTY_MANAGER,
    }
)

TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "get_reservation",
            "Look up one reservation by confirmation code AND guest last name. Returns "
            "stay details, guest initials and loyalty tier only.",
            Access.READ_ONLY,
            False,
            GetReservationArgs,
            DESK_AND_MANAGERS,
            lookup=True,
            run=get_reservation,
        ),
        ToolSpec(
            "get_room_status",
            "Current housekeeping status of one room.",
            Access.READ_ONLY,
            False,
            GetRoomStatusArgs,
            HOUSEKEEPING_VIEW,
            run=get_room_status,
        ),
        ToolSpec(
            "get_occupancy",
            "Occupied and total rooms for a date (default: today).",
            Access.READ_ONLY,
            False,
            GetOccupancyArgs,
            DESK_AND_MANAGERS,
            run=get_occupancy,
        ),
        ToolSpec(
            "get_housekeeping_tasks",
            "List housekeeping tasks, optionally filtered by status or room (max 50).",
            Access.READ_ONLY,
            False,
            GetHousekeepingTasksArgs,
            HOUSEKEEPING_VIEW,
            run=get_housekeeping_tasks,
        ),
        ToolSpec(
            "calculate_refund",
            "Quote the refund under POL-CANCEL-001 for a confirmed future stay. Quote "
            "only: moves no money and changes nothing.",
            Access.READ_ONLY,
            True,
            CalculateRefundArgs,
            DESK_AND_MANAGERS,
            lookup=True,
            run=calculate_refund,
        ),
        ToolSpec(
            "get_approval_status",
            "Status and outcome of an approval request you created.",
            Access.READ_ONLY,
            False,
            GetApprovalStatusArgs,
            ALL_STAFF,
            run=get_approval_status,
        ),
        ToolSpec(
            "create_housekeeping_task",
            "Request a new housekeeping task. Requires human approval; nothing is "
            "created until a supervisor or manager approves.",
            Access.MUTATING,
            False,
            CreateHousekeepingTaskArgs,
            HK_REQUESTERS,
            prepare=prepare_housekeeping_task,
            execute=execute_housekeeping_task,
        ),
        ToolSpec(
            "request_cancellation_refund",
            "Request cancellation of a confirmed future stay with a policy-computed "
            "refund. Requires manager approval (general manager above ₹50,000). The "
            "amount is always computed by the server.",
            Access.MUTATING,
            True,
            RequestCancellationRefundArgs,
            DESK_AND_MANAGERS,
            lookup=True,
            prepare=prepare_cancellation_refund,
            execute=execute_cancellation_refund,
        ),
    )
}
