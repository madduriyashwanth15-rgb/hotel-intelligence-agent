"""Analytics views agree with independent reference SQL on the raw tables."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db

BUSINESS_DATE = "DATE '2026-10-01'"


def _rows(engine: Engine, sql: str) -> list[tuple[object, ...]]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql)).all()]


def _mapping(engine: Engine, sql: str) -> dict[object, object]:
    return {row[0]: row[1] for row in _rows(engine, sql)}


def test_business_date_is_anchor(sql_role_engine: Engine, seeded_engine: Engine) -> None:
    view = _rows(sql_role_engine, "SELECT business_date::text FROM analytics.business_date")
    raw = _rows(seeded_engine, "SELECT value FROM dataset_metadata WHERE key = 'anchor_date'")
    assert view == raw == [("2026-10-01",)]


def test_occupancy_matches_raw_reservations(sql_role_engine: Engine, seeded_engine: Engine) -> None:
    view = _rows(
        sql_role_engine,
        "SELECT occupied_rooms, total_rooms, occupancy_pct FROM analytics.daily_occupancy"
        f" WHERE stay_date = {BUSINESS_DATE}",
    )
    raw = _rows(
        seeded_engine,
        "SELECT count(*), (SELECT count(*) FROM rooms) FROM reservations"
        " WHERE status IN ('confirmed','checked_in','checked_out')"
        f" AND check_in_date <= {BUSINESS_DATE} AND check_out_date > {BUSINESS_DATE}",
    )
    occupied, total = raw[0]
    assert view == [(occupied, total, round(Decimal(100) * occupied / total, 2))]  # type: ignore[operator]


def test_every_night_occupancy_matches(sql_role_engine: Engine, seeded_engine: Engine) -> None:
    view = _mapping(
        sql_role_engine, "SELECT stay_date, occupied_rooms FROM analytics.daily_occupancy"
    )
    raw = _mapping(
        seeded_engine,
        "SELECT d::date, count(r.id) FROM generate_series("
        "(SELECT min(check_in_date) FROM reservations),"
        "(SELECT max(check_out_date) - 1 FROM reservations), interval '1 day') d"
        " LEFT JOIN reservations r ON r.status IN ('confirmed','checked_in','checked_out')"
        " AND r.check_in_date <= d::date AND r.check_out_date > d::date GROUP BY d",
    )
    assert view == raw
    assert len(view) >= 150


def test_reservation_counts_by_status(sql_role_engine: Engine, seeded_engine: Engine) -> None:
    sql = "SELECT status, count(*) FROM {} GROUP BY status ORDER BY status"
    assert _rows(sql_role_engine, sql.format("analytics.reservations")) == _rows(
        seeded_engine, sql.format("reservations")
    )


def test_revenue_by_category(sql_role_engine: Engine, seeded_engine: Engine) -> None:
    view = _rows(
        sql_role_engine,
        "SELECT category, sum(amount) FROM analytics.daily_revenue GROUP BY 1 ORDER BY 1",
    )
    raw = _rows(
        seeded_engine, "SELECT category, sum(amount) FROM folio_charges GROUP BY 1 ORDER BY 1"
    )
    assert view == raw


def test_cancelled_dates_use_hotel_timezone(sql_role_engine: Engine, seeded_engine: Engine) -> None:
    view = _rows(
        sql_role_engine,
        "SELECT reservation_id, cancelled_date FROM analytics.reservations"
        " WHERE cancelled_date IS NOT NULL ORDER BY 1",
    )
    raw = _rows(
        seeded_engine,
        "SELECT id, (cancelled_at AT TIME ZONE 'Asia/Kolkata')::date FROM reservations"
        " WHERE cancelled_at IS NOT NULL ORDER BY 1",
    )
    assert view == raw
    assert view


def test_room_and_task_views(sql_role_engine: Engine, seeded_engine: Engine) -> None:
    assert _rows(
        sql_role_engine,
        "SELECT housekeeping_status, count(*) FROM analytics.rooms GROUP BY 1 ORDER BY 1",
    ) == _rows(
        seeded_engine,
        "SELECT housekeeping_status, count(*) FROM rooms GROUP BY 1 ORDER BY 1",
    )
    assert _rows(
        sql_role_engine,
        "SELECT room_type_code, room_count FROM analytics.room_types ORDER BY 1",
    ) == _rows(
        seeded_engine,
        "SELECT rt.code, count(r.id) FROM room_types rt JOIN rooms r ON r.room_type_id = rt.id"
        " GROUP BY rt.code ORDER BY 1",
    )
    assert _rows(
        sql_role_engine,
        "SELECT status, count(*) FROM analytics.housekeeping_tasks GROUP BY 1 ORDER BY 1",
    ) == _rows(
        seeded_engine, "SELECT status, count(*) FROM housekeeping_tasks GROUP BY 1 ORDER BY 1"
    )
