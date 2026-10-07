"""Seeded database: loader behaviour, constraints and the read-only role."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from psycopg import errors as pg_errors
from sqlalchemy import Connection, Engine, func, insert, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from hotel_agent.config import Settings
from hotel_agent.db.models import (
    Base,
    DatasetMetadata,
    HousekeepingTask,
    Reservation,
    ReservationStatus,
)
from hotel_agent.db.session import make_engine
from hotel_agent.synthetic import seed
from hotel_agent.synthetic.generator import SyntheticDataset
from hotel_agent.synthetic.seed import DatabaseNotEmptyError, load_dataset

pytestmark = pytest.mark.db


@pytest.fixture
def rollback_conn(seeded_engine: Engine) -> Iterator[Connection]:
    """A connection whose changes are always rolled back."""
    with seeded_engine.connect() as conn:
        trans = conn.begin()
        yield conn
        trans.rollback()


def test_row_counts_match_generated_dataset(
    seeded_engine: Engine, dataset: SyntheticDataset
) -> None:
    with seeded_engine.connect() as conn:
        for table, expected in dataset.counts().items():
            actual = conn.execute(
                select(func.count()).select_from(Base.metadata.tables[table])
            ).scalar_one()
            # The loader adds a `loaded_at` provenance row.
            assert actual == expected + (1 if table == "dataset_metadata" else 0), table


def test_enum_values_stored_as_lowercase_values(seeded_engine: Engine) -> None:
    with seeded_engine.connect() as conn:
        statuses = set(conn.execute(select(Reservation.status).distinct()).scalars())
    assert statuses == {s.value for s in ReservationStatus}


def test_database_is_marked_synthetic(seeded_engine: Engine) -> None:
    with seeded_engine.connect() as conn:
        value = conn.execute(
            select(DatasetMetadata.value).where(DatasetMetadata.key == "synthetic")
        ).scalar_one()
    assert value == "true"


def test_loader_refuses_non_empty_database(
    seeded_engine: Engine, dataset: SyntheticDataset
) -> None:
    with pytest.raises(DatabaseNotEmptyError):
        load_dataset(seeded_engine, dataset)


def test_seed_cli_exit_codes(seeded_engine: Engine, settings: Settings) -> None:
    db = ["--database", settings.test_postgres_db]
    assert seed.main([*db]) == 1  # already seeded, no --reset
    assert seed.main([*db, "--reset", "--seed", "42"]) == 0
    with seeded_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM hotels")).scalar_one() == 1


def test_sequences_continue_after_explicit_ids(rollback_conn: Connection) -> None:
    max_id = rollback_conn.execute(select(func.max(HousekeepingTask.id))).scalar_one()
    new_id = rollback_conn.execute(
        insert(HousekeepingTask)
        .values(
            hotel_id=1,
            room_id=1,
            task_type="cleaning",
            priority="normal",
            status="open",
            created_by="test",
        )
        .returning(HousekeepingTask.id)
    ).scalar_one()
    assert new_id == max_id + 1


def test_exclusion_constraint_prevents_double_booking(rollback_conn: Connection) -> None:
    existing = rollback_conn.execute(
        select(Reservation.__table__)
        .where(Reservation.status == "checked_out", Reservation.room_id.is_not(None))
        .limit(1)
    ).one()
    overlapping = dict(existing._mapping)
    overlapping.pop("id")
    overlapping.update(
        confirmation_code="TEST-OVERLAP",
        status="confirmed",
        check_out_date=existing.check_in_date + timedelta(days=1),
    )
    with pytest.raises(IntegrityError) as excinfo:
        rollback_conn.execute(insert(Reservation), [overlapping])
    assert isinstance(excinfo.value.orig, pg_errors.ExclusionViolation)


def test_check_constraint_rejects_unknown_status(rollback_conn: Connection) -> None:
    with pytest.raises(IntegrityError) as excinfo:
        rollback_conn.execute(text("UPDATE reservations SET status = 'teleported' WHERE id = 1"))
    assert isinstance(excinfo.value.orig, pg_errors.CheckViolation)


# --- Read-only role ---


@pytest.fixture
def readonly_engine(seeded_engine: Engine, settings: Settings) -> Iterator[Engine]:
    if settings.readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")
    engine = make_engine(settings.readonly_database_url(settings.test_postgres_db))
    yield engine
    engine.dispose()


def test_readonly_role_can_select(readonly_engine: Engine) -> None:
    with readonly_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM reservations")).scalar_one() > 0
        assert conn.execute(text("SHOW default_transaction_read_only")).scalar_one() == "on"
        assert conn.execute(text("SHOW statement_timeout")).scalar_one() == "5s"


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO dataset_metadata (key, value) VALUES ('x', 'y')",
        "UPDATE rooms SET housekeeping_status = 'clean'",
        "DELETE FROM guests",
        "DROP TABLE folio_charges",
        "CREATE TABLE sneaky (id int)",
    ],
)
def test_readonly_role_cannot_write(readonly_engine: Engine, statement: str) -> None:
    with readonly_engine.connect() as conn, pytest.raises(DBAPIError) as excinfo:
        conn.execute(text(statement))
    assert isinstance(
        excinfo.value.orig, pg_errors.ReadOnlySqlTransaction | pg_errors.InsufficientPrivilege
    )


def test_readonly_role_blocked_even_outside_read_only_transaction(
    readonly_engine: Engine,
) -> None:
    """Privileges, not just the session default, must prevent writes."""
    with readonly_engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ WRITE"))
        with pytest.raises(DBAPIError) as excinfo:
            conn.execute(text("DELETE FROM guests"))
    assert isinstance(excinfo.value.orig, pg_errors.InsufficientPrivilege)
