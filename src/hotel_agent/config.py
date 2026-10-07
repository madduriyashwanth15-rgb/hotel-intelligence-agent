"""Typed application settings loaded from environment variables (and a local .env file)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parents[2]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """All configuration comes from the environment; there are no secret defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: LogLevel = "INFO"

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "hotel_agent"
    postgres_user: str
    postgres_password: SecretStr

    readonly_db_user: str = "hotel_reader"
    readonly_db_password: SecretStr | None = None

    # Login role that executes generated SQL against analytics views only (ADR-0014).
    sql_db_user: str = "hotel_sql"
    sql_db_password: SecretStr | None = None

    # Login role used by the MCP tool gateway (ADR-0020).
    ops_db_user: str = "hotel_ops"
    ops_db_password: SecretStr | None = None

    test_postgres_db: str = "hotel_agent_test"

    synthetic_seed: int = 42
    synthetic_anchor_date: date = date(2026, 10, 1)

    # Knowledge base / embeddings (Phase 2). Relative paths resolve against the project root.
    kb_dir: Path = Path("data/knowledge_base")
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: Path = Path(".models")
    embedding_threads: int = Field(default=2, ge=1, le=8)
    # "I don't know" thresholds (calibrated on the eval dev split; see eval/results).
    kb_min_similarity: float = Field(default=0.58, ge=0.0, le=1.0)
    kb_min_coverage: float = Field(default=0.45, ge=0.0, le=1.0)

    def resolve_path(self, path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path

    def database_url(self, database: str | None = None) -> URL:
        """SQLAlchemy URL for the admin/application user (password safely escaped)."""
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=database or self.postgres_db,
        )

    def sql_database_url(self, database: str | None = None) -> URL:
        """SQLAlchemy URL for the hotel_sql role (analytics views only)."""
        if self.sql_db_password is None:
            raise ValueError("SQL_DB_PASSWORD is not set")
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.sql_db_user,
            password=self.sql_db_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=database or self.postgres_db,
        )

    def ops_database_url(self, database: str | None = None) -> URL:
        """SQLAlchemy URL for the hotel_ops role (tool gateway, least privilege)."""
        if self.ops_db_password is None:
            raise ValueError("OPS_DB_PASSWORD is not set")
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.ops_db_user,
            password=self.ops_db_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=database or self.postgres_db,
        )

    def readonly_database_url(self, database: str | None = None) -> URL:
        """SQLAlchemy URL for the read-only login role."""
        if self.readonly_db_password is None:
            raise ValueError("READONLY_DB_PASSWORD is not set")
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.readonly_db_user,
            password=self.readonly_db_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=database or self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
