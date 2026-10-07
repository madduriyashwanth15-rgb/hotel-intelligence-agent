"""Engine construction and small database helpers."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def make_engine(url: URL | str, **kwargs: object) -> Engine:
    """Create an engine with conservative pool settings for local development."""
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, **kwargs)


def alembic_config(database_url: URL | None = None) -> Config:
    """Alembic config pointing at our migrations; optionally targeting a specific database."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    if database_url is not None:
        cfg.attributes["database_url"] = database_url
    return cfg


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    """Revision recorded in alembic_version, or None if migrations never ran."""
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT to_regclass('public.alembic_version')")).scalar()
        if exists is None:
            return None
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
