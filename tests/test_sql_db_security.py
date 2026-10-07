"""Database-layer security for Text-to-SQL (ADR-0014).

These tests connect directly as `hotel_sql` and run raw SQL — the SQL guard is NOT in the
path. They prove the database alone confines generated SQL to the analytics views.
"""

from __future__ import annotations

import pytest
from psycopg import errors as pg_errors
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.db

ANALYTICS_VIEWS = {
    "business_date",
    "room_types",
    "rooms",
    "reservations",
    "daily_occupancy",
    "daily_revenue",
    "housekeeping_tasks",
}
PII_COLUMNS = {
    "first_name",
    "last_name",
    "email",
    "phone",
    "confirmation_code",
    "notes",
    "guest_id",
}


def _error(engine: Engine, sql: str) -> BaseException | None:
    with engine.connect() as conn:
        try:
            conn.execute(text(sql))
        except DBAPIError as exc:
            return exc.orig
    return None


# --- What hotel_sql CAN do ---


def test_hotel_sql_reads_every_analytics_view(sql_role_engine: Engine) -> None:
    with sql_role_engine.connect() as conn:
        for view in ANALYTICS_VIEWS:
            conn.execute(text(f"SELECT * FROM analytics.{view} LIMIT 1")).all()
        # search_path = analytics, so bare names resolve to the views
        assert conn.execute(text("SELECT business_date FROM business_date")).scalar_one()


def test_hotel_sql_session_defaults(sql_role_engine: Engine) -> None:
    with sql_role_engine.connect() as conn:
        assert conn.execute(text("SHOW default_transaction_read_only")).scalar_one() == "on"
        assert conn.execute(text("SHOW statement_timeout")).scalar_one() == "3s"
        assert conn.execute(text("SHOW search_path")).scalar_one() == "analytics"
        assert conn.execute(text("SELECT current_user")).scalar_one() == "hotel_sql"


def test_analytics_schema_contains_exactly_the_approved_views(seeded_engine: Engine) -> None:
    with seeded_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n"
                " ON n.oid = c.relnamespace WHERE n.nspname = 'analytics'"
            )
        ).all()
    assert {r[0] for r in rows} == ANALYTICS_VIEWS
    assert {r[1] for r in rows} == {"v"}  # views only


def test_no_view_exposes_pii_columns(seeded_engine: Engine) -> None:
    with seeded_engine.connect() as conn:
        columns = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = 'analytics'"
                )
            ).scalars()
        )
    assert not columns & PII_COLUMNS


# --- What hotel_sql CANNOT do (guard bypassed) ---


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM public.guests",
        "SELECT email FROM public.guests",
        "SELECT * FROM public.reservations",
        "SELECT * FROM public.folio_charges",
        "SELECT * FROM public.kb_chunks",
        "SELECT * FROM public.dataset_metadata",
        "SELECT * FROM pg_catalog.pg_authid",
    ],
)
def test_cannot_read_forbidden_relations(sql_role_engine: Engine, sql: str) -> None:
    assert isinstance(_error(sql_role_engine, sql), pg_errors.InsufficientPrivilege)


def test_cannot_read_guests_even_with_explicit_search_path(sql_role_engine: Engine) -> None:
    with sql_role_engine.connect() as conn:
        conn.execute(text("SET search_path = public, analytics"))
        with pytest.raises(DBAPIError) as excinfo:
            conn.execute(text("SELECT * FROM guests"))
    assert isinstance(
        excinfo.value.orig, pg_errors.InsufficientPrivilege | pg_errors.UndefinedTable
    )


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO public.dataset_metadata (key, value) VALUES ('x', 'y')",
        "UPDATE public.rooms SET housekeeping_status = 'clean'",
        "DELETE FROM public.guests",
        "TRUNCATE public.folio_charges",
        "DROP TABLE public.reservations",
        "DROP VIEW analytics.rooms",
        "ALTER TABLE public.guests ADD COLUMN x int",
        "CREATE TABLE analytics.sneaky (id int)",
        "CREATE TABLE public.sneaky (id int)",
        "CREATE TEMP TABLE sneaky (id int)",
        "CREATE VIEW analytics.v2 AS SELECT 1",
        "CREATE FUNCTION analytics.f() RETURNS int AS 'SELECT 1' LANGUAGE sql",
        "CREATE SCHEMA evil",
        "GRANT SELECT ON public.guests TO hotel_sql",
        "COPY (SELECT 1) TO '/tmp/x'",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT lo_import('/etc/passwd')",
        "ALTER ROLE hotel_sql SET statement_timeout = '0'",
    ],
)
def test_cannot_write_create_or_escalate(sql_role_engine: Engine, sql: str) -> None:
    err = _error(sql_role_engine, sql)
    assert err is not None, f"statement unexpectedly succeeded: {sql}"
    assert isinstance(err, pg_errors.InsufficientPrivilege | pg_errors.ReadOnlySqlTransaction), (
        sql,
        type(err),
    )


def test_writes_blocked_by_privileges_even_in_read_write_transaction(
    sql_role_engine: Engine,
) -> None:
    with sql_role_engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ WRITE"))
        with pytest.raises(DBAPIError) as excinfo:
            conn.execute(text("DELETE FROM public.guests"))
    assert isinstance(excinfo.value.orig, pg_errors.InsufficientPrivilege)


def test_long_running_query_is_killed_by_role_timeout(sql_role_engine: Engine) -> None:
    err = _error(sql_role_engine, "SELECT pg_sleep(10)")
    assert isinstance(err, pg_errors.QueryCanceled)


def test_connection_limit_is_set(seeded_engine: Engine) -> None:
    with seeded_engine.connect() as conn:
        limit = conn.execute(
            text("SELECT rolconnlimit FROM pg_roles WHERE rolname = 'hotel_sql'")
        ).scalar_one()
    assert limit == 5
