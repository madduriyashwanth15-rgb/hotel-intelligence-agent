"""Cancellation/refund policy engine for POL-CANCEL-001 (ADR-0021). Pure functions.

Simulation assumptions: the guest prepaid the quoted total; taxes are ignored; rate plans
(e.g. non-refundable advance purchase) are not modelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

POLICY_REFERENCE = "POL-CANCEL-001"
CHECK_IN_TIME = time(14, 0)


@dataclass(frozen=True, slots=True)
class StayForRefund:
    status: str
    channel: str
    room_type_code: str
    check_in_date: date
    check_out_date: date
    rate_per_night: Decimal
    total_amount: Decimal


@dataclass(frozen=True, slots=True)
class RefundQuote:
    eligible: bool
    rule: str
    cancellation_charge: Decimal
    refund_amount: Decimal
    hours_before_arrival: float
    reason: str


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def is_peak(day: date) -> bool:
    """Peak season: 1 Apr - 30 Jun and 20 Dec - 5 Jan."""
    return (
        (day.month in (4, 5, 6))
        or (day.month == 12 and day.day >= 20)
        or (day.month == 1 and day.day <= 5)
    )


def quote_refund(stay: StayForRefund, now: datetime, timezone: str) -> RefundQuote:
    arrival = datetime.combine(stay.check_in_date, CHECK_IN_TIME, tzinfo=ZoneInfo(timezone))
    hours = (arrival - now).total_seconds() / 3600
    total = _money(stay.total_amount)
    nights = (stay.check_out_date - stay.check_in_date).days
    zero = Decimal("0.00")

    def not_eligible(rule: str, reason: str) -> RefundQuote:
        return RefundQuote(False, rule, zero, zero, round(hours, 1), reason)

    if stay.status != "confirmed":
        return not_eligible("not_refundable_status", f"reservation status is {stay.status}")
    if hours <= 0:
        return not_eligible("arrival_passed", "the arrival time has passed")
    if stay.channel == "ota":
        return not_eligible("ota", "OTA bookings are cancelled and refunded through the OTA")

    one_night = _money(stay.rate_per_night)
    if stay.room_type_code == "VIL":
        free = hours >= 14 * 24
        charge = zero if free else (total if nights < 2 else _money(stay.rate_per_night * 2))
        rule = "villa_free" if free else "villa_two_nights"
    elif stay.channel == "corporate":
        free = hours >= 24
        charge, rule = (zero, "corporate_free") if free else (one_night, "corporate_one_night")
    elif is_peak(stay.check_in_date):
        free = hours >= 7 * 24
        charge = zero if free else _money(total * Decimal("0.5"))
        rule = "peak_free" if free else "peak_half_stay"
    else:
        free = hours >= 48
        charge, rule = (zero, "flexible_free") if free else (one_night, "flexible_one_night")

    charge = min(charge, total)
    return RefundQuote(True, rule, charge, _money(total - charge), round(hours, 1), "eligible")


def window_end(stay: StayForRefund, timezone: str, days: int) -> datetime:
    """Helper for tests: the free-cancellation cut-off for a window of `days` days."""
    arrival = datetime.combine(stay.check_in_date, CHECK_IN_TIME, tzinfo=ZoneInfo(timezone))
    return arrival - timedelta(days=days)
