"""POL-CANCEL-001 refund policy engine (pure functions)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from hotel_agent.ops.refund import StayForRefund, is_peak, quote_refund

TZ = "Asia/Kolkata"


def _stay(**kw: object) -> StayForRefund:
    base: dict[str, object] = {
        "status": "confirmed",
        "channel": "direct",
        "room_type_code": "DLX",
        "check_in_date": date(2026, 11, 10),
        "check_out_date": date(2026, 11, 13),
        "rate_per_night": Decimal("9000.00"),
        "total_amount": Decimal("27000.00"),
    }
    base.update(kw)
    return StayForRefund(**base)  # type: ignore[arg-type]


def _hours_before(stay: StayForRefund, hours: float) -> datetime:
    arrival = datetime(
        stay.check_in_date.year,
        stay.check_in_date.month,
        stay.check_in_date.day,
        14,
        tzinfo=ZoneInfo(TZ),
    )
    return arrival - timedelta(hours=hours)


@pytest.mark.parametrize(
    ("hours", "rule", "refund"),
    [
        (48, "flexible_free", "27000.00"),
        (47.9, "flexible_one_night", "18000.00"),
        (1, "flexible_one_night", "18000.00"),
    ],
)
def test_flexible_window(hours: float, rule: str, refund: str) -> None:
    stay = _stay()
    q = quote_refund(stay, _hours_before(stay, hours), TZ)
    assert (q.eligible, q.rule, str(q.refund_amount)) == (True, rule, refund)


def test_corporate_window() -> None:
    stay = _stay(channel="corporate")
    assert quote_refund(stay, _hours_before(stay, 24), TZ).rule == "corporate_free"
    late = quote_refund(stay, _hours_before(stay, 23), TZ)
    assert late.rule == "corporate_one_night"
    assert late.cancellation_charge == Decimal("9000.00")


def test_villa_rules_override_season_and_channel() -> None:
    stay = _stay(
        room_type_code="VIL",
        rate_per_night=Decimal("38000"),
        total_amount=Decimal("114000"),
        check_in_date=date(2026, 5, 1),
        check_out_date=date(2026, 5, 4),
    )
    assert quote_refund(stay, _hours_before(stay, 14 * 24), TZ).rule == "villa_free"
    late = quote_refund(stay, _hours_before(stay, 13 * 24), TZ)
    assert late.rule == "villa_two_nights"
    assert late.refund_amount == Decimal("38000.00")


def test_villa_one_night_stay_charges_full_stay() -> None:
    stay = _stay(
        room_type_code="VIL",
        rate_per_night=Decimal("38000"),
        total_amount=Decimal("38000"),
        check_out_date=date(2026, 11, 11),
    )
    q = quote_refund(stay, _hours_before(stay, 24), TZ)
    assert q.refund_amount == Decimal("0.00")
    assert q.cancellation_charge == Decimal("38000.00")


def test_peak_season() -> None:
    stay = _stay(
        check_in_date=date(2026, 12, 24),
        check_out_date=date(2026, 12, 26),
        total_amount=Decimal("18000"),
    )
    assert quote_refund(stay, _hours_before(stay, 7 * 24), TZ).rule == "peak_free"
    late = quote_refund(stay, _hours_before(stay, 3 * 24), TZ)
    assert late.rule == "peak_half_stay"
    assert late.refund_amount == Decimal("9000.00")


@pytest.mark.parametrize(
    ("day", "peak"),
    [
        (date(2026, 4, 1), True),
        (date(2026, 6, 30), True),
        (date(2026, 7, 1), False),
        (date(2026, 12, 19), False),
        (date(2026, 12, 20), True),
        (date(2027, 1, 5), True),
        (date(2027, 1, 6), False),
    ],
)
def test_peak_dates(day: date, peak: bool) -> None:
    assert is_peak(day) is peak


@pytest.mark.parametrize(
    ("kw", "rule"),
    [
        ({"channel": "ota"}, "ota"),
        ({"status": "checked_out"}, "not_refundable_status"),
        ({"status": "cancelled"}, "not_refundable_status"),
        ({"status": "no_show"}, "not_refundable_status"),
        ({"status": "checked_in"}, "not_refundable_status"),
    ],
)
def test_not_eligible(kw: dict[str, object], rule: str) -> None:
    stay = _stay(**kw)
    q = quote_refund(stay, _hours_before(stay, 100), TZ)
    assert not q.eligible
    assert q.rule == rule
    assert q.refund_amount == Decimal("0.00")


def test_arrival_passed() -> None:
    stay = _stay()
    q = quote_refund(stay, _hours_before(stay, -1), TZ)
    assert (q.eligible, q.rule) == (False, "arrival_passed")


def test_refund_never_negative_or_above_total() -> None:
    stay = _stay(rate_per_night=Decimal("50000"), total_amount=Decimal("1000"))
    q = quote_refund(stay, _hours_before(stay, 1), TZ)
    assert Decimal("0") <= q.refund_amount <= stay.total_amount
    assert q.cancellation_charge <= stay.total_amount
