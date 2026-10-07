"""Composition root: wires the existing Phase 2-4 services into HotelAgents.

This is the only agent module that touches engines/settings; the graph itself is
database-free. Shared resources (engines, answerer, SQL pipeline) are created once;
each agent session gets its own staff principal and MCP session. Tools are always reached
through MCP: an in-memory session to the hotel-mcp server object, or a real `hotel-mcp`
subprocess over stdio.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import Engine

from hotel_agent.agent.capabilities import DataAdapter, KnowledgeAdapter, McpToolClient
from hotel_agent.agent.fake_llm import DeterministicRouterLLM
from hotel_agent.agent.llm import LLMClient
from hotel_agent.agent.ollama_llm import OllamaLLMClient
from hotel_agent.agent.service import HotelAgent
from hotel_agent.config import Settings
from hotel_agent.db.session import make_engine
from hotel_agent.knowledge.answer import EvidenceThresholds, ExtractiveAnswerer
from hotel_agent.knowledge.embeddings import Embedder, FastEmbedEmbedder
from hotel_agent.knowledge.retrieval import Retriever
from hotel_agent.ops.gateway import ToolGateway
from hotel_agent.ops.mcp_server import build_server
from hotel_agent.ops.principal import load_principal
from hotel_agent.sql.context import load_context
from hotel_agent.sql.executor import SqlExecutor
from hotel_agent.sql.generators import RuleBasedFakeGenerator
from hotel_agent.sql.guard import SqlGuard
from hotel_agent.sql.pipeline import SqlPipeline

McpMode = Literal["memory", "stdio"]


@dataclass(frozen=True, slots=True)
class AgentResources:
    ops_engine: Engine
    knowledge: KnowledgeAdapter
    data: DataAdapter
    database: str | None


@contextmanager
def agent_resources(
    settings: Settings, *, database: str | None = None, embedder: Embedder | None = None
) -> Iterator[AgentResources]:
    ops_engine = make_engine(settings.ops_database_url(database))
    kb_engine = make_engine(settings.readonly_database_url(database))
    sql_engine = make_engine(settings.sql_database_url(database))
    try:
        embed = embedder or FastEmbedEmbedder(
            settings.embedding_model,
            settings.resolve_path(settings.embedding_cache_dir),
            threads=settings.embedding_threads,
        )
        answerer = ExtractiveAnswerer(
            Retriever(kb_engine, embed),
            embed,
            EvidenceThresholds(settings.kb_min_similarity, settings.kb_min_coverage),
        )
        pipeline = SqlPipeline(
            RuleBasedFakeGenerator(), SqlGuard(), SqlExecutor(sql_engine), load_context(sql_engine)
        )
        yield AgentResources(
            ops_engine, KnowledgeAdapter(answerer), DataAdapter(pipeline), database
        )
    finally:
        for engine in (ops_engine, kb_engine, sql_engine):
            engine.dispose()


@asynccontextmanager
async def agent_session(
    resources: AgentResources,
    staff_code: str,
    *,
    llm: LLMClient | None = None,
    fallback: LLMClient | None = None,
    mcp_mode: McpMode = "memory",
) -> AsyncIterator[HotelAgent]:
    """One staff session: principal fixed here, MCP server built for that principal."""
    with resources.ops_engine.connect() as conn:
        principal = load_principal(conn, staff_code)
    async with AsyncExitStack() as stack:
        if mcp_mode == "memory":
            session = await stack.enter_async_context(
                create_connected_server_and_client_session(
                    build_server(ToolGateway(resources.ops_engine, principal))
                )
            )
        else:
            env = dict(os.environ)
            if resources.database:
                env["POSTGRES_DB"] = resources.database
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "hotel_agent.ops.mcp_server", "--staff-code", staff_code],
                env=env,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        yield HotelAgent(
            llm or DeterministicRouterLLM(),
            resources.knowledge,
            resources.data,
            McpToolClient(session),
            staff_code=principal.staff_code,
            role=principal.role.value,
            fallback=fallback,
        )


def router_models(settings: Settings) -> tuple[LLMClient, LLMClient | None]:
    """Primary router model and optional fallback, from settings (ADR-0025)."""
    if settings.llm_provider == "deterministic":
        return DeterministicRouterLLM(settings.synthetic_anchor_date), None
    primary = OllamaLLMClient(
        settings.ollama_base_url,
        settings.ollama_model,
        timeout_s=settings.ollama_timeout_s,
        num_ctx=settings.ollama_num_ctx,
    )
    fallback = (
        DeterministicRouterLLM(settings.synthetic_anchor_date) if settings.llm_fallback else None
    )
    return primary, fallback


@asynccontextmanager
async def open_agent(
    settings: Settings,
    staff_code: str,
    *,
    database: str | None = None,
    mcp_mode: McpMode = "memory",
    llm: LLMClient | None = None,
    embedder: Embedder | None = None,
) -> AsyncIterator[HotelAgent]:
    fallback: LLMClient | None = None
    if llm is None:
        llm, fallback = router_models(settings)
    with agent_resources(settings, database=database, embedder=embedder) as resources:
        async with agent_session(
            resources, staff_code, llm=llm, fallback=fallback, mcp_mode=mcp_mode
        ) as agent:
            yield agent
