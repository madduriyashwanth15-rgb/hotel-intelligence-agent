"""Deterministic natural-language summaries of query results (no LLM)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any


def inr(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"₹{Decimal(value):,.2f}"


def _num(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,}" if isinstance(value, int) else f"{Decimal(value):,.2f}"


def _rows_as_dicts(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _posting_note(latest: Any, business_date: date) -> str:
    latest_text = latest.isoformat() if isinstance(latest, date) else "none"
    return (
        " Revenue is posted per night after the night is consumed; the latest posted night "
        f"is {latest_text}, so nights from {business_date.isoformat()} onward have no posted "
        "revenue yet."
    )


def summarize(
    intent: str,
    period_label: str | None,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    business_date: date,
    truncated: bool = False,
) -> str:
    data = _rows_as_dicts(columns, rows)
    period = f" ({period_label})" if period_label else ""
    first = data[0] if data else {}

    if intent == "occupancy_on_date":
        if not data:
            return f"No occupancy data is available for {period_label}."
        return (
            f"Occupancy on {first['stay_date']}: {first['occupancy_pct']}% "
            f"({first['occupied_rooms']} of {first['total_rooms']} rooms occupied)."
        )
    if intent == "occupancy_over_period":
        if first.get("from_date") is None:
            return f"No occupancy data is available for {period_label}."
        return (
            f"Average occupancy{period} from {first['from_date']} to {first['to_date']}: "
            f"{first['avg_occupancy_pct']}% ({_num(first['occupied_room_nights'])} occupied "
            "room-nights)."
        )
    if intent == "revenue_total":
        text = f"Posted revenue{period}: {inr(first.get('revenue'))}."
        if not first.get("revenue"):
            text += _posting_note(first.get("latest_posted_date"), business_date)
        return text
    if intent == "revenue_by_category":
        if not data:
            return f"Posted revenue{period}: {inr(0)}." + _posting_note(None, business_date)
        parts = ", ".join(f"{r['category']} {inr(r['revenue'])}" for r in data)
        return f"Posted revenue by category{period}: {parts}."
    if intent == "adr_revpar":
        text = f"ADR{period}: {inr(first.get('adr'))}; RevPAR: {inr(first.get('revpar'))}."
        if not first.get("room_revenue"):
            text += _posting_note(first.get("latest_posted_date"), business_date)
        return text
    simple_counts = {
        "reservation_count_by_status": "Reservations with status {label}: {n}.",
        "cancellation_count": "Cancellations{period}: {n}.",
        "no_show_count": "No-shows{period}: {n}.",
        "arrivals": "Arrivals{period}: {n}.",
        "departures": "Departures{period}: {n}.",
        "bookings_made": "Reservations made{period}: {n}.",
        "room_count_by_status": "Rooms with status {label}: {n}.",
        "room_count": "Total rooms: {n}.",
    }
    if intent in simple_counts and data:
        n = _num(next(iter(first.values())))
        return simple_counts[intent].format(label=period_label, period=period, n=n)
    if intent == "average_booking_value":
        return (
            f"Average booking value{period}: {inr(first.get('average_booking_value'))} "
            f"across {_num(first.get('reservations'))} reservations (excluding cancelled and "
            "no-shows)."
        )
    if intent == "average_length_of_stay":
        return f"Average length of stay: {first.get('average_nights')} nights."
    if intent == "booking_volume_by_room_type" and data:
        top = data[0]
        return (
            f"Highest booking volume{period}: {top['room_type_name']} ({top['room_type_code']}) "
            f"with {_num(top['bookings'])} reservations; {len(data)} room types listed."
        )
    if intent == "bookings_by_channel" and data:
        parts = ", ".join(f"{r['channel']} {_num(r['bookings'])}" for r in data)
        return f"Reservations by channel (excluding cancelled): {parts}."
    if intent == "occupancy_by_room_type" and data:
        parts = ", ".join(
            f"{r['room_type_code']} {r['occupied_rooms']}/{r['room_count']}" for r in data
        )
        return f"Occupied rooms by room type{period}: {parts}."
    if intent == "rooms_with_status":
        rooms = ", ".join(str(r["room_number"]) for r in data) or "none"
        return f"Rooms with status {period_label}: {rooms}."
    if intent == "room_status_summary" and data:
        parts = ", ".join(f"{r['housekeeping_status']} {r['rooms']}" for r in data)
        return f"Rooms by housekeeping status: {parts}."
    if intent == "open_housekeeping_tasks":
        total = sum(int(r["open_tasks"]) for r in data)
        parts = ", ".join(f"{r['task_type']} {r['open_tasks']}" for r in data)
        return f"Open housekeeping tasks: {total}" + (f" ({parts})." if parts else ".")

    noun = "row" if len(data) == 1 else "rows"
    more = " (more rows exist; showing the first ones)" if truncated else ""
    return f"The query returned {len(data)} {noun}{more}."
