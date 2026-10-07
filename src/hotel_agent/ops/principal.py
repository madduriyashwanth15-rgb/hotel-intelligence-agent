"""Staff principals (ADR-0020).

A principal is fixed for a tool session when the server starts. There is NO
authentication in Phase 4: the local launcher asserts the staff code. Tool arguments can
never change the principal, its role or its hotel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, select

from hotel_agent.db.models import DatasetMetadata, Hotel, StaffMember, StaffRole

STAFF_CODE = re.compile(r"^S-[A-Z0-9]{2,4}-\d{2}$")
MANAGER_ROLES = frozenset(
    {StaffRole.FRONT_OFFICE_MANAGER, StaffRole.DUTY_MANAGER, StaffRole.GENERAL_MANAGER}
)


class PrincipalError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Principal:
    staff_id: int
    staff_code: str
    role: StaffRole
    hotel_id: int
    hotel_code: str
    hotel_timezone: str

    @property
    def is_manager(self) -> bool:
        return self.role in MANAGER_ROLES


def load_principal(conn: Connection, staff_code: str) -> Principal:
    if not STAFF_CODE.match(staff_code or ""):
        raise PrincipalError("invalid staff code")
    row = conn.execute(
        select(
            StaffMember.id,
            StaffMember.staff_code,
            StaffMember.role,
            StaffMember.is_active,
            Hotel.id,
            Hotel.code,
            Hotel.timezone,
        )
        .join(Hotel, Hotel.id == StaffMember.hotel_id)
        .where(StaffMember.staff_code == staff_code)
    ).first()
    if row is None:
        raise PrincipalError("unknown staff member")
    if not row[3]:
        raise PrincipalError("staff member is not active")
    return Principal(
        staff_id=row[0],
        staff_code=row[1],
        role=StaffRole(row[2]),
        hotel_id=row[4],
        hotel_code=row[5],
        hotel_timezone=row[6],
    )


def business_date(conn: Connection) -> date:
    value = conn.execute(
        select(DatasetMetadata.value).where(DatasetMetadata.key == "anchor_date")
    ).scalar()
    if value is None:
        raise PrincipalError("business date not available; load the synthetic dataset")
    return date.fromisoformat(value)


def business_now(conn: Connection, timezone: str) -> datetime:
    """Simulated 'now' for hotel data: the business date at 12:00 hotel time (ADR-0021)."""
    return datetime.combine(business_date(conn), time(12, 0), tzinfo=ZoneInfo(timezone))
