"""analytics views and hotel_sql role

ADR-0014. Creates schema `analytics` with curated, PII-free views over the operational
tables, a NOLOGIN group `hotel_analytics` with SELECT on exactly those views, and the login
role `hotel_sql` (SQL_DB_USER / SQL_DB_PASSWORD) used to execute generated SQL. Narrows
`hotel_readonly` (ADR-0005) to the knowledge-base tables and removes PUBLIC's USAGE/CREATE
on schema public.

Views are owned by the migrating (admin) role, so readers need no base-table privileges.
Roles are cluster-wide: creation is idempotent and downgrade leaves the roles in place.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from hotel_agent.config import get_settings

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ANALYTICS_GROUP = "hotel_analytics"
READONLY_GROUP = "hotel_readonly"
KB_TABLES = ("kb_documents", "kb_chunks")

VIEWS: dict[str, str] = {
    "business_date": """
        SELECT value::date AS business_date
        FROM public.dataset_metadata
        WHERE key = 'anchor_date'
    """,
    "room_types": """
        SELECT rt.code AS room_type_code,
               rt.name AS room_type_name,
               rt.max_occupancy,
               rt.base_rate AS rack_rate,
               count(r.id)::int AS room_count
        FROM public.room_types rt
        LEFT JOIN public.rooms r ON r.room_type_id = rt.id
        GROUP BY rt.id, rt.code, rt.name, rt.max_occupancy, rt.base_rate
    """,
    "rooms": """
        SELECT r.room_number,
               r.floor,
               rt.code AS room_type_code,
               rt.name AS room_type_name,
               r.housekeeping_status,
               (r.housekeeping_status = 'out_of_order') AS is_out_of_order
        FROM public.rooms r
        JOIN public.room_types rt ON rt.id = r.room_type_id
    """,
    # No guest names, emails, phones or confirmation codes.
    "reservations": """
        SELECT res.id AS reservation_id,
               rt.code AS room_type_code,
               rm.room_number,
               res.check_in_date,
               res.check_out_date,
               (res.check_out_date - res.check_in_date) AS nights,
               res.status,
               (res.status IN ('confirmed', 'checked_in', 'checked_out')) AS is_active,
               res.channel,
               res.adults,
               res.children,
               res.rate_per_night,
               res.total_amount,
               (res.booked_at AT TIME ZONE h.timezone)::date AS booked_date,
               (res.cancelled_at AT TIME ZONE h.timezone)::date AS cancelled_date,
               (res.check_in_date - (res.booked_at AT TIME ZONE h.timezone)::date)
                   AS lead_time_days,
               g.loyalty_tier AS guest_loyalty_tier,
               g.country_code AS guest_country
        FROM public.reservations res
        JOIN public.hotels h ON h.id = res.hotel_id
        JOIN public.room_types rt ON rt.id = res.room_type_id
        JOIN public.guests g ON g.id = res.guest_id
        LEFT JOIN public.rooms rm ON rm.id = res.room_id
    """,
    "daily_occupancy": """
        WITH bounds AS (
            SELECT min(check_in_date) AS first_night, max(check_out_date) - 1 AS last_night
            FROM public.reservations
        ),
        nights AS (
            SELECT generate_series(first_night, last_night, interval '1 day')::date AS stay_date
            FROM bounds
            WHERE first_night IS NOT NULL
        ),
        total AS (SELECT count(*)::int AS total_rooms FROM public.rooms),
        occupied AS (
            SELECT n.stay_date, count(res.id)::int AS occupied_rooms
            FROM nights n
            LEFT JOIN public.reservations res
              ON res.status IN ('confirmed', 'checked_in', 'checked_out')
             AND res.check_in_date <= n.stay_date
             AND res.check_out_date > n.stay_date
            GROUP BY n.stay_date
        ),
        room_rev AS (
            SELECT charge_date, sum(amount) AS room_revenue, count(*)::int AS posted_room_nights
            FROM public.folio_charges
            WHERE category = 'room'
            GROUP BY charge_date
        )
        SELECT o.stay_date,
               t.total_rooms,
               o.occupied_rooms,
               round(100.0 * o.occupied_rooms / NULLIF(t.total_rooms, 0), 2) AS occupancy_pct,
               coalesce(rr.room_revenue, 0)::numeric(14, 2) AS room_revenue,
               round(rr.room_revenue / NULLIF(rr.posted_room_nights, 0), 2) AS adr,
               round(coalesce(rr.room_revenue, 0) / NULLIF(t.total_rooms, 0), 2) AS revpar
        FROM occupied o
        CROSS JOIN total t
        LEFT JOIN room_rev rr ON rr.charge_date = o.stay_date
    """,
    "daily_revenue": """
        SELECT charge_date, category, sum(amount) AS amount, count(*)::int AS charge_count
        FROM public.folio_charges
        GROUP BY charge_date, category
    """,
    # No free-text notes.
    "housekeeping_tasks": """
        SELECT t.id AS task_id,
               r.room_number,
               t.task_type,
               t.priority,
               t.status,
               t.created_at,
               t.due_at,
               t.completed_at
        FROM public.housekeeping_tasks t
        JOIN public.rooms r ON r.id = t.room_id
    """,
}


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def _formatted(template: str, *args: str) -> None:
    """Build DDL server-side with format() so identifiers/literals are safely quoted."""
    bind = op.get_bind()
    placeholders = ", ".join(f"CAST(:a{i} AS text)" for i in range(len(args)))
    statement = bind.execute(
        text(f"SELECT format(CAST(:template AS text), {placeholders})"),
        {"template": template, **{f"a{i}": arg for i, arg in enumerate(args)}},
    ).scalar_one()
    bind.exec_driver_sql(statement)


def _role_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name})
        .scalar()
    )


def upgrade() -> None:
    settings = get_settings()

    # --- Tighten schema public: nobody gets implicit access ---
    _sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    _sql("REVOKE USAGE ON SCHEMA public FROM PUBLIC")

    # --- Narrow hotel_readonly to the knowledge-base tables (amends ADR-0005) ---
    _formatted(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM %I",
        READONLY_GROUP,
    )
    _formatted("REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM %I", READONLY_GROUP)
    _formatted("GRANT USAGE ON SCHEMA public TO %I", READONLY_GROUP)
    for table in KB_TABLES:
        _formatted("GRANT SELECT ON public.%I TO %I", table, READONLY_GROUP)

    # --- analytics schema and views ---
    _sql("CREATE SCHEMA IF NOT EXISTS analytics")
    _sql("REVOKE ALL ON SCHEMA analytics FROM PUBLIC")
    for name, body in VIEWS.items():
        _sql(f"CREATE VIEW analytics.{name} AS {body}")

    # --- hotel_analytics group: SELECT on exactly the approved views ---
    if not _role_exists(ANALYTICS_GROUP):
        _formatted("CREATE ROLE %I NOLOGIN", ANALYTICS_GROUP)
    _formatted("GRANT USAGE ON SCHEMA analytics TO %I", ANALYTICS_GROUP)
    for name in VIEWS:
        _formatted("GRANT SELECT ON analytics.%I TO %I", name, ANALYTICS_GROUP)

    # --- hotel_sql login role ---
    if settings.sql_db_password is None:
        return
    login = settings.sql_db_user
    password = settings.sql_db_password.get_secret_value()
    verb = "ALTER" if _role_exists(login) else "CREATE"
    _formatted(f"{verb} ROLE %I LOGIN PASSWORD %L CONNECTION LIMIT 5", login, password)
    _formatted("GRANT %I TO %I", ANALYTICS_GROUP, login)
    _formatted("ALTER ROLE %I SET default_transaction_read_only = on", login)
    _formatted("ALTER ROLE %I SET statement_timeout = '3s'", login)
    _formatted("ALTER ROLE %I SET idle_in_transaction_session_timeout = '10s'", login)
    _formatted("ALTER ROLE %I SET search_path = analytics", login)


def downgrade() -> None:
    for name in VIEWS:
        _sql(f"DROP VIEW IF EXISTS analytics.{name}")
    _formatted("REVOKE ALL ON SCHEMA analytics FROM %I", ANALYTICS_GROUP)
    _sql("DROP SCHEMA IF EXISTS analytics")

    # Restore the migration-0002 state for hotel_readonly and schema public.
    _formatted("GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I", READONLY_GROUP)
    _formatted(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO %I",
        READONLY_GROUP,
    )
    _sql("GRANT USAGE ON SCHEMA public TO PUBLIC")
