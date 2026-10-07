"""SqlGuard bypass corpus: every unsafe input must be rejected (100%), safe ones allowed.

The guard is security code for untrusted input. These cases must not be relaxed to make
tests pass; a failing case means the guard needs fixing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlglot

from hotel_agent.sql.guard import (
    MAX_SQL_CHARS,
    ParamValue,
    RejectionCode,
    SqlGuard,
    SqlRejectedError,
    ValidatedQuery,
)

G = SqlGuard(max_rows=100)
U, R, F, INV, P, X = (
    RejectionCode.UNSAFE_SQL,
    RejectionCode.DISALLOWED_RELATION,
    RejectionCode.DISALLOWED_FUNCTION,
    RejectionCode.INVALID_SQL,
    RejectionCode.INVALID_PARAMETERS,
    RejectionCode.TOO_EXPENSIVE,
)

UNSAFE_CORPUS: list[tuple[str, RejectionCode]] = [
    # --- stacked statements / non-SELECT statements ---
    ("SELECT 1 FROM rooms; DROP TABLE reservations", U),
    ("SELECT 1 FROM rooms; SELECT 2 FROM rooms", U),
    ("BEGIN; DELETE FROM rooms; COMMIT", U),
    ("DROP TABLE reservations", U),
    ("DROP VIEW analytics.rooms", U),
    ("DELETE FROM reservations", U),
    ("DELETE FROM analytics.reservations WHERE true", U),
    ("UPDATE rooms SET housekeeping_status = 'clean'", U),
    ("INSERT INTO rooms (room_number) VALUES ('999')", U),
    ("TRUNCATE reservations", U),
    ("ALTER TABLE rooms ADD COLUMN x int", U),
    ("CREATE TABLE x AS SELECT * FROM rooms", U),
    ("CREATE VIEW v AS SELECT * FROM rooms", U),
    ("MERGE INTO rooms USING rooms r2 ON true WHEN MATCHED THEN DELETE", U),
    ("COPY rooms TO STDOUT", U),
    ("COPY (SELECT 1) TO PROGRAM 'rm -rf /'", U),
    ("DO $$ BEGIN PERFORM 1; END $$", U),
    ("SET statement_timeout = 0", U),
    ("SET ROLE postgres", U),
    ("RESET ALL", U),
    ("GRANT SELECT ON guests TO PUBLIC", U),
    ("VACUUM", U),
    ("EXPLAIN ANALYZE DELETE FROM rooms", U),
    ("PREPARE p AS SELECT 1", U),
    ("EXECUTE p", U),
    ("LISTEN chan", U),
    ("NOTIFY chan", U),
    ("CALL do_something()", U),
    ("VALUES (1)", U),
    # --- write/DDL hidden inside a SELECT ---
    ("WITH d AS (DELETE FROM reservations RETURNING *) SELECT * FROM d", U),
    ("WITH u AS (UPDATE rooms SET floor = 1 RETURNING *) SELECT count(*) FROM u", U),
    ("WITH i AS (INSERT INTO rooms DEFAULT VALUES RETURNING *) SELECT 1 FROM rooms", U),
    ("SELECT * INTO new_table FROM rooms", U),
    ("SELECT * FROM rooms FOR UPDATE", U),
    ("SELECT * FROM rooms FOR SHARE", U),
    ("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT * FROM r", U),
    ("SELECT * FROM (VALUES (1)) AS v(x)", U),
    ("SELECT * FROM rooms, LATERAL (SELECT 1) AS x", U),
    ("SELECT * FROM rooms LIMIT (SELECT 1000000)", U),
    ("SELECT * FROM rooms WHERE room_number = $1", U),
    ("SELECT room_number || 'x' FROM rooms", U),
    ("SELECT rank() OVER (ORDER BY floor) FROM rooms", U),
    # --- forbidden relations ---
    ("SELECT * FROM guests", R),
    ("SELECT * FROM public.guests", R),
    ("SELECT * FROM PUBLIC.GUESTS", R),
    ('SELECT * FROM "public"."guests"', R),
    ("SELECT * FROM public.reservations", R),
    ("SELECT * FROM kb_chunks", R),
    ("SELECT * FROM pg_catalog.pg_user", R),
    ("SELECT * FROM pg_shadow", R),
    ("SELECT * FROM pg_roles", R),
    ("SELECT * FROM information_schema.tables", R),
    ("SELECT * FROM mydb.analytics.rooms", R),
    ('SELECT * FROM "Reservations"', R),
    ("SELECT r.* FROM analytics.reservations r JOIN public.guests g ON true", R),
    ("SELECT (SELECT email FROM guests LIMIT 1) FROM rooms", R),
    ("SELECT 1 FROM rooms UNION SELECT email FROM guests", R),
    ("WITH x AS (SELECT * FROM guests) SELECT * FROM x", R),
    ("WITH rooms AS (SELECT * FROM public.guests) SELECT * FROM rooms", R),
    ("WITH guests AS (SELECT 1 FROM rooms) SELECT * FROM public.guests", R),
    ("SELECT * FROM rooms WHERE EXISTS (SELECT 1 FROM guests)", R),
    ("SELECT * FROM generate_series(1, 1000000000)", F),  # table function
    # --- forbidden functions ---
    ("SELECT pg_sleep(10)", F),
    ('SELECT "pg_sleep"(10)', F),
    ("SELECT pg_catalog.pg_sleep(10)", F),
    ("SELECT * FROM rooms WHERE floor = pg_sleep(1)", F),
    ("SELECT now()", F),
    ("SELECT current_date", F),
    ("SELECT CURRENT_TIMESTAMP", F),
    ("SELECT count(*) FROM reservations WHERE check_in_date = current_date", F),
    ("SELECT version()", F),
    ("SELECT current_user", F),
    ("SELECT set_config('statement_timeout', '0', false)", F),
    ("SELECT current_setting('server_version')", F),
    ("SELECT query_to_xml('select * from public.guests', true, true, '')", F),
    ("SELECT dblink('host=evil', 'select 1')", F),
    ("SELECT pg_read_file('/etc/passwd')", F),
    ("SELECT lo_import('/etc/passwd')", F),
    ("SELECT pg_terminate_backend(1)", F),
    ("SELECT txid_current()", F),
    ("SELECT random() FROM rooms", F),
    ("SELECT md5(room_number) FROM rooms", F),
    ("SELECT generate_series(1, 1000000000)", F),
    ("SELECT string_agg(room_number, ',') FROM rooms", F),
    ("SELECT 'guests'::regclass", F),
    ("SELECT CAST('guests' AS regclass)", F),
    ("SELECT room_number::oid FROM rooms", F),
    ("SELECT room_number::json FROM rooms", F),
    # --- invalid SQL ---
    ("", INV),
    ("   ", INV),
    ("SELEC * FROM rooms", INV),
    ("SELECT * FROM", INV),
    ("SELECT 'unterminated FROM rooms", INV),
    ("\x00SELECT 1 FROM rooms", INV),
    ("SELECT 1 FROM rooms WHERE " + "1 = 1 AND " * 500 + "true", INV),  # > MAX_SQL_CHARS
    # --- expensive / complex ---
    ("SELECT * FROM rooms JOIN reservations", X),
    ("SELECT * FROM rooms, reservations, daily_occupancy", X),
    ("SELECT * FROM rooms OFFSET 100000", X),
    ("SELECT * FROM rooms WHERE floor IN (" + ", ".join(["1"] * 700) + ")", X),
    (
        "SELECT * FROM rooms r1 JOIN rooms r2 ON true JOIN rooms r3 ON true JOIN rooms r4 ON true"
        " JOIN rooms r5 ON true JOIN rooms r6 ON true JOIN rooms r7 ON true JOIN rooms r8 ON true",
        X,
    ),
    (
        "SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT * FROM "
        "(SELECT * FROM rooms) a) b) c) d) e",
        X,
    ),
]


@pytest.mark.parametrize(
    ("sql", "code"), UNSAFE_CORPUS, ids=[f"u{i:03d}" for i in range(len(UNSAFE_CORPUS))]
)
def test_unsafe_sql_is_rejected(sql: str, code: RejectionCode) -> None:
    with pytest.raises(SqlRejectedError) as excinfo:
        G.validate(sql)
    assert excinfo.value.code == code, (sql, excinfo.value)


def test_unsafe_corpus_block_rate_is_100_percent() -> None:
    blocked = 0
    for sql, _ in UNSAFE_CORPUS:
        try:
            G.validate(sql)
        except SqlRejectedError:
            blocked += 1
    assert blocked == len(UNSAFE_CORPUS) >= 100


# --- Parameters ---


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        ("SELECT * FROM rooms WHERE floor = :f", {}),
        ("SELECT * FROM rooms", {"f": 1}),
        ("SELECT * FROM rooms WHERE floor = :f", {"f": 1, "extra": 2}),
        ("SELECT * FROM rooms WHERE floor = ?", {}),
        ("SELECT * FROM rooms WHERE room_type_code = :c", {"c": "x'; DROP TABLE rooms; --"}),
        ("SELECT * FROM rooms WHERE room_type_code = :c", {"c": "a" * 65}),
        ("SELECT * FROM rooms WHERE floor = :f", {"f": 10**12}),
        ("SELECT * FROM rooms WHERE floor = :f", {"f": float("nan")}),
        ("SELECT * FROM rooms WHERE floor = :f", {"f": [1, 2]}),
        ("SELECT * FROM rooms WHERE floor = :F", {"F": 1}),
    ],
)
def test_invalid_parameters_rejected(sql: str, params: dict[str, object]) -> None:
    with pytest.raises(SqlRejectedError) as excinfo:
        G.validate(sql, params)  # type: ignore[arg-type]  # deliberately bad values
    assert excinfo.value.code == RejectionCode.INVALID_PARAMETERS


def test_valid_parameters_are_kept_and_rendered_as_named_binds() -> None:
    params: dict[str, ParamValue] = {
        "s": "confirmed",
        "d": date(2026, 10, 1),
        "n": 3,
        "x": Decimal("1.5"),
        "b": True,
    }
    v = G.validate(
        "SELECT count(*) FROM reservations WHERE status = :s AND check_in_date >= :d"
        " AND nights > :n AND rate_per_night > :x AND is_active = :b",
        params,
    )
    assert v.params == params
    assert ":s" in v.sql
    assert ":d" in v.sql
    assert "%(" not in v.sql


# --- Safe queries that must be ALLOWED (no over-blocking) ---

SAFE_CORPUS = [
    "SELECT occupied_rooms, occupancy_pct FROM analytics.daily_occupancy"
    " WHERE stay_date = (SELECT business_date FROM analytics.business_date)",
    "select count(*) from RESERVATIONS where STATUS = 'confirmed'",
    'SELECT count(*) FROM analytics."reservations"',
    "SELECT 1 FROM rooms /* ; DROP TABLE rooms */",
    "SELECT 1 FROM rooms -- '; DROP TABLE rooms",
    "SELECT room_type_code, count(*) AS n FROM reservations GROUP BY room_type_code"
    " HAVING count(*) > 10 ORDER BY n DESC NULLS LAST LIMIT 3",
    "WITH b AS (SELECT business_date AS d FROM business_date) SELECT count(*)"
    " FROM reservations r CROSS JOIN b WHERE r.cancelled_date BETWEEN"
    " date_trunc('week', b.d)::date AND b.d",
    "SELECT category, round(sum(amount), 2) FROM daily_revenue"
    " WHERE charge_date >= (SELECT business_date FROM business_date) - interval '30 days'"
    " GROUP BY category",
    "SELECT CASE WHEN is_out_of_order THEN 'ooo' ELSE housekeeping_status END, count(*)"
    " FROM rooms GROUP BY 1",
    "SELECT count(*) FILTER (WHERE status = 'cancelled') FROM reservations",
    "SELECT r.room_type_code, rt.room_type_name, avg(r.total_amount)::numeric(12, 2)"
    " FROM reservations r JOIN room_types rt ON rt.room_type_code = r.room_type_code"
    " WHERE r.is_active GROUP BY 1, 2",
    "WITH guests AS (SELECT floor FROM rooms) SELECT count(*) FROM guests",
    "SELECT extract(month FROM check_in_date), count(*) FROM reservations GROUP BY 1",
    "SELECT * FROM rooms UNION ALL SELECT * FROM rooms",
    "SELECT DISTINCT channel FROM reservations WHERE channel LIKE 'o%'",
    "SELECT coalesce(nullif(adr, 0), 0), greatest(1, 2), abs(-1), lower('A'), upper('a')"
    " FROM daily_occupancy",
    "SELECT * FROM housekeeping_tasks WHERE status IN ('open', 'in_progress') LIMIT 5 OFFSET 5",
]


@pytest.mark.parametrize("sql", SAFE_CORPUS)
def test_safe_sql_is_allowed(sql: str) -> None:
    v = G.validate(sql)
    assert isinstance(v, ValidatedQuery)
    assert "/*" not in v.sql  # comments dropped
    assert "--" not in v.sql
    assert v.relations <= G.allowed_views
    # Re-validating the canonical SQL is stable (what we execute is what we checked).
    assert G.validate(v.sql).sql == v.sql


@pytest.mark.parametrize(
    ("sql", "expected_limit"),
    [
        ("SELECT * FROM rooms", 101),
        ("SELECT * FROM rooms LIMIT 5", 5),
        ("SELECT * FROM rooms LIMIT 100000", 101),
        ("SELECT 1 FROM rooms UNION SELECT 2 FROM rooms", 101),
    ],
)
def test_outer_limit_enforced(sql: str, expected_limit: int) -> None:
    tree = sqlglot.parse_one(G.validate(sql).sql, read="postgres")
    limit = tree.args["limit"].expression
    assert int(limit.this) == expected_limit


def test_max_sql_chars_boundary() -> None:
    with pytest.raises(SqlRejectedError, match="too_long"):
        G.validate("SELECT 1 FROM rooms WHERE " + "x" * MAX_SQL_CHARS)


# --- Robustness: the guard only ever accepts or raises SqlRejectedError ---


def test_truncation_fuzz_never_crashes_or_admits_unsafe_sql() -> None:
    allowed = 0
    for sql, _ in UNSAFE_CORPUS:
        for cut in range(1, len(sql), max(1, len(sql) // 25)):
            fragment = sql[:cut]
            try:
                v = G.validate(fragment)
            except SqlRejectedError:
                continue
            allowed += 1
            # Anything accepted must still satisfy the invariants.
            assert v.relations <= G.allowed_views
            tree = sqlglot.parse_one(v.sql, read="postgres")
            assert tree.args.get("limit") is not None
    assert allowed >= 0
