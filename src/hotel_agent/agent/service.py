"""HotelAgent: the single agent entry point (ADR-0022).

A HotelAgent is bound to one staff session: its MCP tool client talks to a hotel-mcp
server built for that staff member. Nothing in a request can change that binding.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from hotel_agent.agent.capabilities import DataService, KnowledgeService, ToolClient
from hotel_agent.agent.graph import build_graph, request_hash
from hotel_agent.agent.llm import LLMClient
from hotel_agent.agent.router import Router

MAX_QUESTION_CHARS = 500
audit_logger = logging.getLogger("hotel_agent.agent.audit")


class AgentInputError(ValueError):
    pass


class HotelAgent:
    def __init__(
        self,
        llm: LLMClient,
        knowledge: KnowledgeService,
        data: DataService,
        tools: ToolClient,
        *,
        staff_code: str,
        role: str,
    ) -> None:
        self.staff_code = staff_code
        self.role = role
        self.model_name = llm.name
        self._graph = build_graph(Router(llm), knowledge, data, tools)

    async def ask(self, question: str) -> dict[str, Any]:
        if not isinstance(question, str):
            raise AgentInputError("question must be text")
        cleaned = " ".join(question.split())
        if not cleaned or len(cleaned) > MAX_QUESTION_CHARS:
            raise AgentInputError(f"question must be 1-{MAX_QUESTION_CHARS} characters")
        start = time.perf_counter()
        digest = request_hash(cleaned)
        state = await self._graph.ainvoke(
            {"question": cleaned, "request_sha256": digest, "trace": []}
        )
        latency = round((time.perf_counter() - start) * 1000, 1)
        response: dict[str, Any] = dict(state["response"])
        response.update(
            request_sha256=digest, model=self.model_name, latency_ms=latency, trace=state["trace"]
        )
        tool_event: dict[str, Any] = next((e for e in state["trace"] if e["node"] == "tool"), {})
        audit_logger.info(
            "agent request processed",
            extra={
                "event": "agent.request",
                "request_sha256": digest,
                "question_chars": len(cleaned),
                "staff_code": self.staff_code,
                "role": self.role,
                "model": self.model_name,
                "route": response["route"],
                "reason": state["decision"]["reason"],
                "tool": tool_event.get("tool"),
                "tool_arguments": tool_event.get("arguments"),
                "authorization": tool_event.get("gateway_code")
                or ("allowed" if tool_event.get("gateway_ok") else None),
                "approval_id": tool_event.get("approval_id"),
                "approval_status": "pending" if response["status"] == "pending_approval" else None,
                "status": response["status"],
                "code": response.get("code"),
                "latency_ms": latency,
            },
        )
        return response
