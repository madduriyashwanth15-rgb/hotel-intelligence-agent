"""Executor behaviour, including defence in depth with the guard deliberately bypassed."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from hotel_agent.sql.executor import ExecutionCode, SqlExecutionError, SqlExecutor
from hotel_agent.sql.guard import RejectionCode, SqlGuard, SqlRejectedError, ValidatedQuery

pytestmark = pytest.mark.db


def _raw(sql: str, params: dict[str, object] | None = None, max_rows: int = 100) -> ValidatedQuery:
    """Build a 'validated' query WITHOUT the guard — simulates a guard bypass."""
    return ValidatedQuery(sql=sql, params=params or {}, relations=frozenset(), max_rows=max_rows)  # type: ignore[arg-type]


@pytest.fixture
def executor(sql_role_engine: Engine) -> SqlExecutor:
    return SqlExecutor(sql_role_engine, timeout_ms=1_000)


def test_executes_guarded_query_with_params(executor: SqlExecutor) -> None:
    q = SqlGuard().validate(
        "SELECT count(*) AS n FROM reservations WHERE status = :s", {"s": "confirmed"}
    )
    result = executor.execute(q)
    assert result.columns == ["n"]
    assert result.rows[0][0] > 0
    assert result.truncated is False
    assert result.estimated_cost > 0


def test_row_cap_and_truncation_flag(executor: SqlExecutor) -> None:
    q = SqlGuard(max_rows=10).validate("SELECT reservation_id FROM reservations")
    result = executor.execute(q)
    assert len(result.rows) == 10
    assert result.truncated is True


def test_percent_signs_in_literals_are_safe(executor: SqlExecutor) -> None:
    q = SqlGuard().validate("SELECT count(*) FROM reservations WHERE channel LIKE 'o%'")
    assert executor.execute(q).rows[0][0] > 0


def test_cost_ceiling_rejects_expensive_plans(sql_role_engine: Engine) -> None:
    executor = SqlExecutor(sql_role_engine, max_cost=1_000)
    q = SqlGuard().validate(
        "SELECT count(*) FROM reservations a CROSS JOIN reservations b CROSS JOIN reservations c"
    )
    with pytest.raises(SqlRejectedError) as excinfo:
        executor.execute(q)
    assert excinfo.value.code == RejectionCode.TOO_EXPENSIVE


def test_unknown_column_maps_to_invalid_sql(executor: SqlExecutor) -> None:
    q = SqlGuard().validate("SELECT no_such_column FROM reservations")
    with pytest.raises(SqlRejectedError) as excinfo:
        executor.execute(q)
    assert excinfo.value.code == RejectionCode.INVALID_SQL
    assert "no_such_column" not in excinfo.value.message  # raw DB errors are not leaked


# --- Guard bypassed: the executor + database must still be safe ---


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM public.guests", RejectionCode.DISALLOWED_RELATION),
        ("SELECT email FROM public.guests", RejectionCode.DISALLOWED_RELATION),
        ("SELECT * FROM pg_catalog.pg_authid", RejectionCode.DISALLOWED_RELATION),
        ("DELETE FROM public.guests", RejectionCode.DISALLOWED_RELATION),
        ("UPDATE public.rooms SET floor = 1", RejectionCode.DISALLOWED_RELATION),
        # EXPLAIN cannot wrap DDL: Postgres rejects it (42601) before anything runs.
        ("CREATE TABLE analytics.x (id int)", RejectionCode.INVALID_SQL),
        ("SELECT set_config('statement_timeout', '0', true), pg_sleep(5)", None),
    ],
)
def test_guard_bypass_is_contained(
    executor: SqlExecutor, seeded_engine: Engine, sql: str, expected: RejectionCode | None
) -> None:
    with seeded_engine.connect() as conn:
        before = conn.execute(text("SELECT count(*), max(id) FROM guests")).one()
    with pytest.raises((SqlRejectedError, SqlExecutionError)) as excinfo:
        executor.execute(_raw(sql))
    if expected is not None:
        assert isinstance(excinfo.value, SqlRejectedError)
        assert excinfo.value.code in {expected, RejectionCode.UNSAFE_SQL}
    with seeded_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*), max(id) FROM guests")).one() == before
        assert conn.execute(text("SELECT to_regclass('analytics.x')")).scalar() is None
        assert conn.execute(text("SELECT count(*) FROM rooms WHERE floor = 1")).scalar_one() == 19


def test_timeout_cancels_long_query_even_without_guard(executor: SqlExecutor) -> None:
    with pytest.raises(SqlExecutionError) as excinfo:
        executor.execute(_raw("SELECT pg_sleep(5)"))
    assert excinfo.value.code == ExecutionCode.TIMEOUT


def test_session_settings_do_not_leak_between_queries(executor: SqlExecutor) -> None:
    """SET LOCAL + rollback: a query cannot change settings for the next one."""
    with pytest.raises((SqlRejectedError, SqlExecutionError)):
        executor.execute(_raw("SELECT set_config('statement_timeout', '0', false), pg_sleep(5)"))
    with pytest.raises(SqlExecutionError) as excinfo:
        executor.execute(_raw("SELECT pg_sleep(5)"))
    assert excinfo.value.code == ExecutionCode.TIMEOUT


def test_executor_rejects_unreasonable_timeout(sql_role_engine: Engine) -> None:
    with pytest.raises(ValueError, match="timeout_ms"):
        SqlExecutor(sql_role_engine, timeout_ms=0)
