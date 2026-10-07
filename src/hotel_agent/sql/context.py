"""Schema context for SQL generators: views, glossary, allowed values (ADR-0016/0017).

This is everything a future LLM generator would be given; the fake generators use the
business date and allowed values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Engine, text

from hotel_agent.db.models import (
    BookingChannel,
    ChargeCategory,
    HousekeepingStatus,
    ReservationStatus,
    TaskStatus,
    TaskType,
)
from hotel_agent.synthetic.generator import ROOM_TYPE_SPECS


@dataclass(frozen=True, slots=True)
class ViewSpec:
    name: str
    description: str
    columns: dict[str, str]


VIEWS: tuple[ViewSpec, ...] = (
    ViewSpec("business_date", "One row: the hotel's business date ('today').",
             {"business_date": "date"}),
    ViewSpec("room_types", "Sellable room categories.",
             {"room_type_code": "STD-K, STD-T, DLX, PRM, STE, VIL", "room_type_name": "text",
              "max_occupancy": "int", "rack_rate": "INR per night", "room_count": "int"}),
    ViewSpec("rooms", "Physical rooms and their current housekeeping status.",
             {"room_number": "text", "floor": "int", "room_type_code": "text",
              "room_type_name": "text", "housekeeping_status": "clean|dirty|inspected|out_of_order",
              "is_out_of_order": "boolean"}),
    ViewSpec("reservations", "One row per reservation; no guest identity.",
             {"reservation_id": "int", "room_type_code": "text", "room_number": "text or null",
              "check_in_date": "date", "check_out_date": "date", "nights": "int",
              "status": "confirmed|checked_in|checked_out|cancelled|no_show",
              "is_active": "status in confirmed/checked_in/checked_out",
              "channel": "direct|ota|corporate|travel_agent|walk_in", "adults": "int",
              "children": "int", "rate_per_night": "INR", "total_amount": "INR quoted room total",
              "booked_date": "date", "cancelled_date": "date or null", "lead_time_days": "int",
              "guest_loyalty_tier": "none|silver|gold|platinum", "guest_country": "ISO code"}),
    ViewSpec("daily_occupancy", "One row per night across the data range.",
             {"stay_date": "date", "total_rooms": "int", "occupied_rooms": "int",
              "occupancy_pct": "numeric", "room_revenue": "INR posted", "adr": "INR or null",
              "revpar": "INR"}),
    ViewSpec("daily_revenue", "Posted folio revenue per day and category.",
             {"charge_date": "date", "category": "room|food_beverage|spa|minibar|laundry",
              "amount": "INR", "charge_count": "int"}),
    ViewSpec("housekeeping_tasks", "Housekeeping and maintenance tasks.",
             {"task_id": "int", "room_number": "text",
              "task_type": "cleaning|turndown|deep_clean|inspection|maintenance",
              "priority": "low|normal|high|urgent", "status": "open|in_progress|done|cancelled",
              "created_at": "timestamptz", "due_at": "timestamptz", "completed_at": "timestamptz"}),
)  # fmt: skip

GLOSSARY: dict[str, str] = {
    "business date": "Today is analytics.business_date; never use CURRENT_DATE or now().",
    "occupied room-night": "An active reservation (confirmed, checked_in, checked_out) "
    "covering the night.",
    "occupancy": "Occupied rooms divided by total rooms (80).",
    "revenue": "Posted folio charges (daily_revenue). Nights are posted after they are "
    "consumed, so dates on or after the business date have no revenue yet.",
    "room revenue": "daily_revenue rows with category 'room'.",
    "ADR": "Room revenue divided by posted room-nights.",
    "RevPAR": "Room revenue divided by total rooms per night.",
    "average booking value": "Mean total_amount of reservations that are not cancelled and "
    "not no-shows.",
    "booking volume": "Number of reservations that are not cancelled.",
    "this week": "Monday of the business week through the business date.",
    "this month": "1st of the business month through the business date.",
    "last week / last month": "The previous full calendar week / month.",
}

ALLOWED_VALUES: dict[str, tuple[str, ...]] = {
    "status": tuple(s.value for s in ReservationStatus),
    "channel": tuple(c.value for c in BookingChannel),
    "room_type_code": tuple(s.code for s in ROOM_TYPE_SPECS),
    "category": tuple(c.value for c in ChargeCategory),
    "housekeeping_status": tuple(s.value for s in HousekeepingStatus),
    "task_status": tuple(s.value for s in TaskStatus),
    "task_type": tuple(t.value for t in TaskType),
}


@dataclass(frozen=True, slots=True)
class SchemaContext:
    business_date: date
    views: tuple[ViewSpec, ...] = VIEWS
    glossary: dict[str, str] = field(default_factory=lambda: dict(GLOSSARY))
    allowed_values: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(ALLOWED_VALUES))


def load_context(engine: Engine) -> SchemaContext:
    """Read the business date through the analytics view (as hotel_sql)."""
    with engine.connect() as conn:
        value = conn.execute(text("SELECT business_date FROM analytics.business_date")).scalar()
    if value is None:
        raise RuntimeError("analytics.business_date is empty; load the synthetic dataset first")
    return SchemaContext(business_date=value)
