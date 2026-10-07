"""Migrations against a real PostgreSQL database."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text

from hotel_agent.config import Settings
from hotel_agent.db.models import Base
from hotel_agent.db.session import alembic_config, current_revision, head_revision, make_engine
from tests.conftest import drop_database, migrate, recreate_database

pytestmark = pytest.mark.db

EXPECTED_TABLES = set(Base.metadata.tables) | {"alembic_version"}


@pytest.fixture
def scratch_db(settings: Settings, admin_engine: Engine) -> Iterator[tuple[str, Engine]]:
    name = f"{settings.test_postgres_db}_migrations"
    recreate_database(admin_engine, name)
    engine = make_engine(settings.database_url(name))
    yield name, engine
    engine.dispose()
    drop_database(admin_engine, name)


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_upgrade_creates_schema_and_extensions(migrated_engine: Engine) -> None:
    assert _tables(migrated_engine) == EXPECTED_TABLES
    assert current_revision(migrated_engine) == head_revision()
    with migrated_engine.connect() as conn:
        extensions = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
    assert {"vector", "btree_gist"} <= extensions


def test_models_match_migrations(settings: Settings, migrated_engine: Engine) -> None:
    cfg = alembic_config(settings.database_url(settings.test_postgres_db))
    cfg.attributes["skip_logging_config"] = True
    command.check(cfg)  # raises if autogenerate would emit any operation


def test_downgrade_and_reupgrade_roundtrip(
    settings: Settings, scratch_db: tuple[str, Engine]
) -> None:
    name, engine = scratch_db
    migrate(settings, name)
    assert _tables(engine) == EXPECTED_TABLES

    cfg = alembic_config(settings.database_url(name))
    cfg.attributes["skip_logging_config"] = True
    command.downgrade(cfg, "base")
    engine.dispose()
    assert _tables(engine) == {"alembic_version"}
    assert current_revision(engine) is None

    migrate(settings, name)
    engine.dispose()
    assert _tables(engine) == EXPECTED_TABLES
    assert current_revision(engine) == head_revision()
