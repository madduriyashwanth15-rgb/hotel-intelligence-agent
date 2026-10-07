"""Shared fixtures. Database tests run against throwaway databases in the local container."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError

from hotel_agent.config import Settings, get_settings
from hotel_agent.db.session import alembic_config, make_engine
from hotel_agent.synthetic.generator import SyntheticDataset, generate
from hotel_agent.synthetic.seed import load_dataset

TEST_SEED = 42


@pytest.fixture(scope="session")
def settings() -> Settings:
    s = get_settings()
    if s.test_postgres_db == s.postgres_db:
        pytest.fail("TEST_POSTGRES_DB must differ from POSTGRES_DB; tests drop that database")
    return s


@pytest.fixture(scope="session")
def admin_engine(settings: Settings) -> Iterator[Engine]:
    """Autocommit connection to the maintenance DB, for CREATE/DROP DATABASE."""
    engine = create_engine(
        settings.database_url("postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable (run `docker compose up -d`): {exc}")
    yield engine
    engine.dispose()


def recreate_database(admin_engine: Engine, name: str) -> None:
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))


def drop_database(admin_engine: Engine, name: str) -> None:
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def migrate(settings: Settings, database: str, revision: str = "head") -> None:
    cfg = alembic_config(settings.database_url(database))
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, revision)


@pytest.fixture(scope="session")
def dataset(settings: Settings) -> SyntheticDataset:
    return generate(TEST_SEED, settings.synthetic_anchor_date)


@pytest.fixture(scope="session")
def migrated_engine(settings: Settings, admin_engine: Engine) -> Iterator[Engine]:
    name = settings.test_postgres_db
    recreate_database(admin_engine, name)
    migrate(settings, name)
    engine = make_engine(settings.database_url(name))
    yield engine
    engine.dispose()
    drop_database(admin_engine, name)


@pytest.fixture(scope="session")
def seeded_engine(migrated_engine: Engine, dataset: SyntheticDataset) -> Engine:
    load_dataset(migrated_engine, dataset, reset=True)
    return migrated_engine


def clear_knowledge_base(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM kb_documents"))


@pytest.fixture(scope="session")
def sql_role_engine(seeded_engine: Engine, settings: Settings) -> Iterator[Engine]:
    """Raw connection as hotel_sql to the seeded test DB — no SQL guard in between."""
    if settings.sql_db_password is None:
        pytest.skip("SQL_DB_PASSWORD not set")
    engine = make_engine(settings.sql_database_url(settings.test_postgres_db))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def ops_role_engine(seeded_engine: Engine, settings: Settings) -> Iterator[Engine]:
    """Raw connection as hotel_ops to the seeded test DB — no tool gateway in between."""
    if settings.ops_db_password is None:
        pytest.skip("OPS_DB_PASSWORD not set")
    engine = make_engine(settings.ops_database_url(settings.test_postgres_db))
    yield engine
    engine.dispose()


@dataclass(frozen=True)
class OpsDatabase:
    admin: Engine
    ops: Engine
    name: str


SECOND_HOTEL_SQL = [
    "INSERT INTO hotels (id, code, name, city, country_code, timezone, currency)"
    " VALUES (2, 'TST', 'Test Coast Hotel (synthetic)', 'Seaview', 'IN', 'Asia/Kolkata', 'INR')",
    "INSERT INTO room_types (hotel_id, code, name, description, max_occupancy, base_rate)"
    " VALUES (2, 'STD-K', 'Standard King', 'Synthetic second-hotel room', 2, 5000)",
    "INSERT INTO rooms (hotel_id, room_type_id, room_number, floor, housekeeping_status)"
    " SELECT 2, id, '101', 9, 'dirty' FROM room_types WHERE hotel_id = 2",
    "INSERT INTO guests (first_name, last_name, email, phone, country_code, loyalty_tier)"
    " VALUES ('Tess', 'Testcase', 'tess.testcase.0@example.com', '+1-201-555-0100', 'IN', 'none')",
    "INSERT INTO reservations (confirmation_code, hotel_id, guest_id, room_type_id, room_id,"
    " check_in_date, check_out_date, status, channel, adults, children, rate_per_night,"
    " total_amount, booked_at) SELECT 'TST-ABC123', 2, g.id, rt.id, r.id, DATE '2026-10-20',"
    " DATE '2026-10-22', 'confirmed', 'direct', 1, 0, 5000, 10000,"
    " TIMESTAMPTZ '2026-09-01 10:00+05:30'"
    " FROM guests g, room_types rt, rooms r WHERE g.last_name = 'Testcase' AND rt.hotel_id = 2"
    " AND r.hotel_id = 2",
    "INSERT INTO housekeeping_tasks (hotel_id, room_id, task_type, priority, status, created_by)"
    " SELECT 2, id, 'cleaning', 'normal', 'open', 'system' FROM rooms WHERE hotel_id = 2",
    "INSERT INTO staff_members (hotel_id, staff_code, display_name, role, is_active) VALUES"
    " (2, 'S-T2-01', 'Test Hotel Front Desk 01', 'front_desk', true),"
    " (2, 'S-T2-02', 'Test Hotel Duty Manager 02', 'duty_manager', true)",
]


@pytest.fixture(scope="session")
def ops_db(
    settings: Settings, admin_engine: Engine, dataset: SyntheticDataset
) -> Iterator[OpsDatabase]:
    """Isolated, seeded database for tool tests (they execute approved mutations)."""
    if settings.ops_db_password is None:
        pytest.skip("OPS_DB_PASSWORD not set")
    name = f"{settings.test_postgres_db}_ops"
    recreate_database(admin_engine, name)
    migrate(settings, name)
    admin = make_engine(settings.database_url(name))
    load_dataset(admin, dataset, reset=True)
    with admin.begin() as conn:
        for statement in SECOND_HOTEL_SQL:
            conn.execute(text(statement))
    ops = make_engine(settings.ops_database_url(name))
    yield OpsDatabase(admin=admin, ops=ops, name=name)
    ops.dispose()
    admin.dispose()
    drop_database(admin_engine, name)
