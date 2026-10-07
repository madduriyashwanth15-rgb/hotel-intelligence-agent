"""Strict argument schemas for every tool (unknown fields are rejected)."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ConfirmationCode = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z]{2,5}-[A-Z0-9]{6}$")
]
LastName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100,
                      pattern=r"^[A-Za-z][A-Za-z' \-]{0,99}$"),
]  # fmt: skip
RoomNumber = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^(V\d{2}|[1-9]\d{2})$")
]
ApprovalId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{24}$")]
# Free text is data, never instructions: printable characters only, no markup brackets.
Note = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200,
                      pattern=r"^[A-Za-z0-9 .,;:'()/\-!?&#₹%]+$"),
]  # fmt: skip


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TaskStatusFilter(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class NewTaskType(StrEnum):
    CLEANING = "cleaning"
    TURNDOWN = "turndown"
    DEEP_CLEAN = "deep_clean"
    INSPECTION = "inspection"
    MAINTENANCE = "maintenance"


class NewTaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class RefundReason(StrEnum):
    GUEST_REQUEST = "guest_request"
    TRAVEL_DISRUPTION = "travel_disruption"
    MEDICAL = "medical"
    DUPLICATE_BOOKING = "duplicate_booking"


class GetReservationArgs(Strict):
    confirmation_code: ConfirmationCode
    guest_last_name: LastName


class GetRoomStatusArgs(Strict):
    room_number: RoomNumber


class GetOccupancyArgs(Strict):
    # JSON has no date type: accept ISO strings (strict mode would reject them otherwise).
    date: Annotated[dt.date | None, Field(strict=False)] = None


class GetHousekeepingTasksArgs(Strict):
    status: Annotated[TaskStatusFilter | None, Field(strict=False)] = None
    room_number: RoomNumber | None = None
    limit: Annotated[int, Field(ge=1, le=50)] = 20


class CalculateRefundArgs(Strict):
    confirmation_code: ConfirmationCode
    guest_last_name: LastName


class GetApprovalStatusArgs(Strict):
    approval_id: ApprovalId


class CreateHousekeepingTaskArgs(Strict):
    room_number: RoomNumber
    task_type: Annotated[NewTaskType, Field(strict=False)]
    priority: Annotated[NewTaskPriority, Field(strict=False)] = NewTaskPriority.NORMAL
    notes: Note | None = None
    due_in_minutes: Annotated[int, Field(ge=15, le=1440)] = 120


class RequestCancellationRefundArgs(Strict):
    confirmation_code: ConfirmationCode
    guest_last_name: LastName
    reason: Annotated[RefundReason, Field(strict=False)]
    note: Note | None = None
