"""Load the synthetic dataset into PostgreSQL.

Usage:
    uv run hotel-seed            # refuses if data already exists
    uv run hotel-seed --reset    # truncate all hotel tables, then load
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from sqlalchemy import Connection, Engine, Table, func, insert, select, text

from hotel_agent.config import get_settings
from hotel_agent.db.models import (
    Base,
    DatasetMetadata,
    FolioCharge,
    Guest,
    Hotel,
    HousekeepingTask,
    Reservation,
    Room,
    RoomType,
)
from hotel_agent.db.session import make_engine
from hotel_agent.logging_config import configure_logging
from hotel_agent.synthetic.generator import SyntheticDataset, generate

logger = logging.getLogger(__name__)

# Insertion order respects foreign keys.
LOAD_ORDER: tuple[tuple[str, type[Base]], ...] = (
    ("hotels", Hotel),
    ("room_types", RoomType),
    ("rooms", Room),
    ("guests", Guest),
    ("reservations", Reservation),
    ("folio_charges", FolioCharge),
    ("housekeeping_tasks", HousekeepingTask),
    ("dataset_metadata", DatasetMetadata),
)
TABLES_WITH_SERIAL_ID = tuple(model for name, model in LOAD_ORDER if name != "dataset_metadata")


class DatabaseNotEmptyError(RuntimeError):
    pass


def _plain(value: Any) -> Any:
    # psycopg would serialise Enum members by *name*; the schema stores values.
    return value.value if isinstance(value, Enum) else value


def _rows(items: list[Any]) -> list[dict[str, Any]]:
    return [{k: _plain(v) for k, v in asdict(item).items()} for item in items]


def _table(model: type[Base]) -> Table:
    table = model.__table__
    if not isinstance(table, Table):
        raise TypeError(f"{model.__name__} is not mapped to a Table")
    return table


def _truncate_all(conn: Connection) -> None:
    names = ", ".join(name for name, _ in LOAD_ORDER)
    conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


def load_dataset(engine: Engine, dataset: SyntheticDataset, *, reset: bool = False) -> None:
    """Insert the dataset in a single transaction (all-or-nothing)."""
    with engine.begin() as conn:
        if reset:
            _truncate_all(conn)
        elif conn.execute(select(func.count()).select_from(_table(Hotel))).scalar_one() > 0:
            raise DatabaseNotEmptyError("Database already contains data; rerun with --reset")

        for name, model in LOAD_ORDER:
            rows = _rows(getattr(dataset, name))
            if rows:
                conn.execute(insert(_table(model)), rows)

        conn.execute(
            insert(_table(DatasetMetadata)),
            [{"key": "loaded_at", "value": datetime.now(UTC).isoformat()}],
        )
        # Explicit ids were inserted; move sequences past them for future inserts.
        for model in TABLES_WITH_SERIAL_ID:
            table = _table(model)
            max_id = func.max(table.c.id)
            conn.execute(
                select(
                    func.setval(
                        func.pg_get_serial_sequence(table.name, "id"),
                        func.coalesce(max_id, 1),
                        max_id.is_not(None),
                    )
                )
            )


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Seed the database with synthetic hotel data.")
    parser.add_argument("--seed", type=int, default=settings.synthetic_seed)
    parser.add_argument(
        "--anchor-date",
        type=date.fromisoformat,
        default=settings.synthetic_anchor_date,
        help="Business date treated as 'today' (YYYY-MM-DD).",
    )
    parser.add_argument("--database", default=None, help="Target database (default: POSTGRES_DB)")
    parser.add_argument("--reset", action="store_true", help="Truncate existing data first.")
    args = parser.parse_args(argv)

    configure_logging(settings.log_level)
    dataset = generate(args.seed, args.anchor_date)
    engine = make_engine(settings.database_url(args.database))
    try:
        load_dataset(engine, dataset, reset=args.reset)
    except DatabaseNotEmptyError as exc:
        logger.error(str(exc))
        return 1
    finally:
        engine.dispose()

    counts = dataset.counts()
    logger.info(
        "synthetic data loaded",
        extra={"seed": args.seed, "anchor_date": args.anchor_date.isoformat(), "counts": counts},
    )
    print(json.dumps({"seed": args.seed, "anchor_date": str(args.anchor_date), **counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
