"""Shared fixtures. Database tests run against throwaway databases in the local container."""

from __future__ import annotations

from collections.abc import Iterator

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
