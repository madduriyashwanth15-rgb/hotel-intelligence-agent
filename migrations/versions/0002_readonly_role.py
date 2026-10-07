"""read-only database role

Creates a NOLOGIN group role `hotel_readonly` with SELECT on the public schema
(including tables created by future migrations), and a LOGIN role (name and
password from READONLY_DB_USER / READONLY_DB_PASSWORD) that is a member of it.
The login role defaults to read-only transactions and a statement timeout as
defence in depth. Phase 3 (Text-to-SQL) will narrow access to curated views.

Roles are cluster-wide, so creation is idempotent; downgrade only revokes the
privileges granted in this database and leaves the roles in place (other
databases in the cluster may still reference them).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from hotel_agent.config import get_settings

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GROUP_ROLE = "hotel_readonly"
STATEMENT_TIMEOUT = "5s"


def _execute_formatted(template: str, *args: str) -> None:
    """Run DDL built server-side with format() so identifiers/literals are safely quoted."""
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

    if not _role_exists(GROUP_ROLE):
        _execute_formatted("CREATE ROLE %I NOLOGIN", GROUP_ROLE)

    _execute_formatted("GRANT USAGE ON SCHEMA public TO %I", GROUP_ROLE)
    _execute_formatted("GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I", GROUP_ROLE)
    _execute_formatted(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO %I", GROUP_ROLE
    )

    if settings.readonly_db_password is None:
        # No login role without a password from the environment.
        return
    login_role = settings.readonly_db_user
    password = settings.readonly_db_password.get_secret_value()
    if _role_exists(login_role):
        _execute_formatted("ALTER ROLE %I LOGIN PASSWORD %L", login_role, password)
    else:
        _execute_formatted("CREATE ROLE %I LOGIN PASSWORD %L", login_role, password)
    _execute_formatted("GRANT %I TO %I", GROUP_ROLE, login_role)
    _execute_formatted("ALTER ROLE %I SET default_transaction_read_only = on", login_role)
    _execute_formatted("ALTER ROLE %I SET statement_timeout = %L", login_role, STATEMENT_TIMEOUT)


def downgrade() -> None:
    _execute_formatted(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM %I", GROUP_ROLE
    )
    _execute_formatted("REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM %I", GROUP_ROLE)
    _execute_formatted("REVOKE USAGE ON SCHEMA public FROM %I", GROUP_ROLE)
