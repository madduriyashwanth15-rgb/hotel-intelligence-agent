"""Database-layer security for the tool gateway role hotel_ops (ADR-0020).

Raw SQL as hotel_ops — the gateway is NOT in the path. Proves least privilege holds even
if gateway code were bypassed or buggy.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from psycopg import errors as pg_errors
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.db


@pytest.fixture
def ops(ops_role_engine: Engine) -> Iterator[Connection]:
    with ops_role_engine.connect() as conn:
        trans = conn.begin()
        yield conn
        trans.rollback()


def _err(engine: Engine, sql: str) -> BaseException | None:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(text(sql))
        except DBAPIError as exc:
            return exc.orig
        finally:
            trans.rollback()
    return None


def test_can_read_what_tools_need(ops: Connection) -> None:
    assert ops.execute(text("SELECT count(*) FROM reservations")).scalar_one() > 0
    assert ops.execute(text("SELECT count(*) FROM staff_members")).scalar_one() == 9
    assert ops.execute(text("SELECT last_name, loyalty_tier FROM guests LIMIT 1")).first()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT email FROM guests",
        "SELECT phone FROM guests",
        "SELECT * FROM guests",
        "SELECT * FROM folio_charges",
        "SELECT * FROM kb_chunks",
        "SELECT * FROM analytics.reservations",
        "DELETE FROM housekeeping_tasks",
        "DELETE FROM approval_requests",
        "DELETE FROM refunds",
        "UPDATE reservations SET total_amount = 0",
        "UPDATE reservations SET room_id = NULL",
        "UPDATE guests SET last_name = 'x'",
        "UPDATE staff_members SET role = 'general_manager'",
        "UPDATE approval_requests SET requested_by = 1",
        "UPDATE approval_requests SET arguments = '{}'::jsonb",
        "UPDATE housekeeping_tasks SET status = 'done'",
        "INSERT INTO staff_members (hotel_id, staff_code, display_name, role, is_active)"
        " VALUES (1, 'S-X-01', 'x', 'general_manager', true)",
        "INSERT INTO folio_charges (reservation_id, charge_date, category, description, amount)"
        " VALUES (1, '2026-10-01', 'room', 'x', 1)",
        "TRUNCATE refunds",
        "DROP TABLE refunds",
        "CREATE TABLE sneaky (id int)",
        "ALTER TABLE refunds DROP CONSTRAINT uq_refunds_reservation_id",
        "GRANT SELECT ON guests TO hotel_ops",
    ],
)
def test_forbidden_operations(ops_role_engine: Engine, sql: str) -> None:
    err = _err(ops_role_engine, sql)
    assert err is not None, f"unexpectedly allowed: {sql}"
    assert isinstance(err, pg_errors.InsufficientPrivilege), (sql, type(err))


def _approval(ops: Connection, requested_by: int, public_id: str) -> int:
    return int(
        ops.execute(
            text(
                "INSERT INTO approval_requests (public_id, hotel_id, tool_name, arguments,"
                " arguments_sha256, summary, requested_by, expires_at, status)"
                " VALUES (:p, 1, 'create_housekeeping_task', '{}'::jsonb, :h, 's', :r,"
                " now() + interval '30 minutes', 'pending') RETURNING id"
            ),
            {"p": public_id, "h": "0" * 64, "r": requested_by},
        ).scalar_one()
    )


def test_four_eyes_enforced_by_database(ops: Connection) -> None:
    approval_id = _approval(ops, requested_by=1, public_id="t-four-eyes")
    with pytest.raises(IntegrityError) as excinfo:
        ops.execute(
            text(
                "UPDATE approval_requests SET status = 'executed', decided_by = 1,"
                " decided_at = now(), executed_at = now() WHERE id = :i"
            ),
            {"i": approval_id},
        )
    assert isinstance(excinfo.value.orig, pg_errors.CheckViolation)


def test_status_lifecycle_constraints(ops: Connection) -> None:
    approval_id = _approval(ops, requested_by=1, public_id="t-lifecycle")
    with pytest.raises(IntegrityError):
        # executed without approver / timestamps
        ops.execute(
            text("UPDATE approval_requests SET status = 'executed' WHERE id = :i"),
            {"i": approval_id},
        )


def test_one_refund_per_reservation(ops: Connection) -> None:
    a1 = _approval(ops, requested_by=1, public_id="t-refund-1")
    a2 = _approval(ops, requested_by=1, public_id="t-refund-2")
    insert = text(
        "INSERT INTO refunds (hotel_id, reservation_id, approval_request_id, amount, currency,"
        " policy_rule) VALUES (1, 1, :a, 10, 'INR', 'flexible')"
    )
    ops.execute(insert, {"a": a1})
    with pytest.raises(IntegrityError) as excinfo:
        ops.execute(insert, {"a": a2})
    assert isinstance(excinfo.value.orig, pg_errors.UniqueViolation)


def test_negative_refund_rejected(ops: Connection) -> None:
    a1 = _approval(ops, requested_by=1, public_id="t-neg")
    with pytest.raises(IntegrityError):
        ops.execute(
            text(
                "INSERT INTO refunds (hotel_id, reservation_id, approval_request_id, amount,"
                " currency, policy_rule) VALUES (1, 2, :a, -5, 'INR', 'x')"
            ),
            {"a": a1},
        )


@pytest.mark.parametrize("table", ["staff_members", "approval_requests", "refunds"])
def test_analytics_role_cannot_see_ops_tables(sql_role_engine: Engine, table: str) -> None:
    err = _err(sql_role_engine, f"SELECT * FROM public.{table}")
    assert isinstance(err, pg_errors.InsufficientPrivilege)
