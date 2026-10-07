"""Capability interfaces used by the graph, and adapters over the existing services.

The graph only sees these protocols. The adapters call the Phase 2-4 components unchanged;
all security decisions (evidence gate, SqlGuard + hotel_sql role, ToolGateway + approval
workflow) stay inside those components.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from mcp import ClientSession, types

from hotel_agent.agent.router import ToolInfo
from hotel_agent.knowledge.answer import ExtractiveAnswerer
from hotel_agent.sql.pipeline import SqlPipeline


class KnowledgeService(Protocol):
    def answer(self, question: str) -> dict[str, Any]: ...


class DataService(Protocol):
    def ask(self, question: str) -> dict[str, Any]: ...


class ToolClient(Protocol):
    async def list_tools(self) -> list[ToolInfo]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


class KnowledgeAdapter:
    """Existing Phase 2 extractive answerer (evidence gate included)."""

    def __init__(self, answerer: ExtractiveAnswerer) -> None:
        self._answerer = answerer

    def answer(self, question: str) -> dict[str, Any]:
        result = self._answerer.answer(question)
        return {
            "status": result.status.value,
            "answer": result.answer,
            "citations": [asdict(c) for c in result.citations],
            "top_similarity": round(result.top_similarity, 4),
            "coverage": round(result.coverage, 4),
        }


class DataAdapter:
    """Existing Phase 3 Text-to-SQL pipeline (its own generator, SqlGuard, hotel_sql role)."""

    def __init__(self, pipeline: SqlPipeline) -> None:
        self._pipeline = pipeline

    def ask(self, question: str) -> dict[str, Any]:
        result = self._pipeline.ask(question)
        return {
            "status": result.status,
            "code": result.code,
            "answer": result.answer,
            "clarification": result.clarification,
            "executed_sql": result.executed_sql,
            "columns": result.columns,
            "rows": [[_plain(v) for v in row] for row in result.rows],
            "truncated": result.truncated,
        }


class McpToolClient:
    """Existing Phase 4 tools through an MCP client session (server = hotel-mcp / ToolGateway)."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._tools: list[ToolInfo] | None = None

    async def list_tools(self) -> list[ToolInfo]:
        if self._tools is None:
            listed = (await self._session.list_tools()).tools
            self._tools = [
                ToolInfo(
                    name=t.name,
                    description=t.description or "",
                    read_only=bool(t.annotations and t.annotations.readOnlyHint),
                    input_schema=dict(t.inputSchema),
                )
                for t in listed
            ]
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._session.call_tool(name, arguments)
        return _tool_payload(result.content)


def _tool_payload(content: Sequence[types.ContentBlock]) -> dict[str, Any]:
    for block in content:
        if isinstance(block, types.TextContent):
            payload: dict[str, Any] = json.loads(block.text)
            return payload
    return {"ok": False, "error": {"code": "internal_error", "message": "empty tool result"}}
