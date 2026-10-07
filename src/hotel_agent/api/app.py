"""FastAPI application factory.

Run locally:
    uv run uvicorn hotel_agent.api.app:create_app --factory --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from hotel_agent import __version__
from hotel_agent.api.health import router as health_router
from hotel_agent.api.middleware import RequestContextMiddleware
from hotel_agent.config import Settings, get_settings
from hotel_agent.db.session import make_engine
from hotel_agent.logging_config import configure_logging

# Fail fast instead of hanging when the database is unreachable.
CONNECT_TIMEOUT_SECONDS = 3


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns_engine = engine is None
        app.state.engine = engine or make_engine(
            settings.database_url(),
            connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        )
        try:
            yield
        finally:
            if owns_engine:
                app.state.engine.dispose()

    app = FastAPI(title="Hotel Intelligence Agent", version=__version__, lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    return app
