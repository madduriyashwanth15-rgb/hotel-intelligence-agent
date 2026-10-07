"""FastAPI application factory.

Run locally:
    uv run uvicorn hotel_agent.api.app:create_app --factory --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from hotel_agent import __version__
from hotel_agent.agent.service import HotelAgent
from hotel_agent.api.agent import AgentHolder
from hotel_agent.api.agent import router as agent_router
from hotel_agent.api.data import PipelineHolder
from hotel_agent.api.data import router as data_router
from hotel_agent.api.health import router as health_router
from hotel_agent.api.knowledge import router as knowledge_router
from hotel_agent.api.middleware import RequestContextMiddleware
from hotel_agent.config import Settings, get_settings
from hotel_agent.db.session import make_engine
from hotel_agent.knowledge.answer import EvidenceThresholds, ExtractiveAnswerer
from hotel_agent.knowledge.embeddings import FastEmbedEmbedder
from hotel_agent.knowledge.retrieval import Retriever
from hotel_agent.logging_config import configure_logging
from hotel_agent.sql.context import load_context
from hotel_agent.sql.executor import SqlExecutor
from hotel_agent.sql.generators import RuleBasedFakeGenerator
from hotel_agent.sql.guard import SqlGuard
from hotel_agent.sql.pipeline import SqlPipeline

# Fail fast instead of hanging when the database is unreachable.
CONNECT_TIMEOUT_SECONDS = 3
logger = logging.getLogger(__name__)


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


def _sql_pipeline_factory(settings: Settings) -> tuple[PipelineHolder, Engine | None]:
    """Text-to-SQL runs as hotel_sql (analytics views only); disabled without credentials."""
    if settings.sql_db_password is None:
        return PipelineHolder(None), None
    sql_engine = make_engine(
        settings.sql_database_url(),
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )

    def build() -> SqlPipeline:
        return SqlPipeline(
            RuleBasedFakeGenerator(), SqlGuard(), SqlExecutor(sql_engine), load_context(sql_engine)
        )

    return PipelineHolder(build), sql_engine


async def _open_agent(settings: Settings, stack: AsyncExitStack) -> HotelAgent | None:
    """Bind the agent to AGENT_STAFF_CODE (server-side; no authentication). None if unset."""
    if settings.agent_staff_code is None:
        return None
    from hotel_agent.agent.factory import agent_resources, agent_session, router_models

    try:
        llm, fallback = router_models(settings)
        resources = stack.enter_context(agent_resources(settings))
        return await stack.enter_async_context(
            agent_session(resources, settings.agent_staff_code, llm=llm, fallback=fallback)
        )
    except Exception as exc:  # misconfigured or unseeded database: serve 503, keep the API up
        logger.warning("agent not available", extra={"error": type(exc).__name__})
        return None


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    answerer: ExtractiveAnswerer | None = None,
    sql_pipeline: SqlPipeline | None = None,
    agent: HotelAgent | None = None,
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
        sql_engine: Engine | None = None
        if sql_pipeline is not None:
            app.state.sql_pipeline = PipelineHolder(lambda: sql_pipeline)
        else:
            app.state.sql_pipeline, sql_engine = _sql_pipeline_factory(settings)
        try:
            async with AsyncExitStack() as agent_stack:  # closes the MCP session and engines
                if agent is None:
                    app.state.agent = AgentHolder(await _open_agent(settings, agent_stack))
                yield
        finally:
            if owns_engine:
                app.state.engine.dispose()
            if readonly is not None:
                readonly.dispose()
            if sql_engine is not None:
                sql_engine.dispose()

    app = FastAPI(title="Hotel Intelligence Agent", version=__version__, lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(knowledge_router)
    app.include_router(data_router)
    app.include_router(agent_router)
    if agent is not None:  # injected (tests): available without running the lifespan
        app.state.agent = AgentHolder(agent)
    return app
