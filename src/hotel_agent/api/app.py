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
from hotel_agent.api.knowledge import router as knowledge_router
from hotel_agent.api.middleware import RequestContextMiddleware
from hotel_agent.config import Settings, get_settings
from hotel_agent.db.session import make_engine
from hotel_agent.knowledge.answer import EvidenceThresholds, ExtractiveAnswerer
from hotel_agent.knowledge.embeddings import FastEmbedEmbedder
from hotel_agent.knowledge.retrieval import Retriever
from hotel_agent.logging_config import configure_logging

# Fail fast instead of hanging when the database is unreachable.
CONNECT_TIMEOUT_SECONDS = 3


def _build_answerer(settings: Settings) -> tuple[ExtractiveAnswerer | None, Engine | None]:
    """Knowledge answerer on a read-only connection; None if no read-only credentials."""
    if settings.readonly_db_password is None:
        return None, None
    readonly = make_engine(
        settings.readonly_database_url(),
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )
    # The model is loaded lazily on the first question (and verified against its pinned hash).
    embedder = FastEmbedEmbedder(
        settings.embedding_model,
        settings.resolve_path(settings.embedding_cache_dir),
        threads=settings.embedding_threads,
    )
    thresholds = EvidenceThresholds(settings.kb_min_similarity, settings.kb_min_coverage)
    return ExtractiveAnswerer(Retriever(readonly, embedder), embedder, thresholds), readonly


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    answerer: ExtractiveAnswerer | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns_engine = engine is None
        app.state.engine = engine or make_engine(
            settings.database_url(),
            connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        )
        readonly: Engine | None = None
        if answerer is not None:
            app.state.answerer = answerer
        else:
            app.state.answerer, readonly = _build_answerer(settings)
        try:
            yield
        finally:
            if owns_engine:
                app.state.engine.dispose()
            if readonly is not None:
                readonly.dispose()

    app = FastAPI(title="Hotel Intelligence Agent", version=__version__, lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(knowledge_router)
    return app
