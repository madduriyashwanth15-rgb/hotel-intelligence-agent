"""POST /agent/ask — the LangGraph agent over HTTP (Phase 7, ADR-0027).

This router only calls `HotelAgent.ask`: routing, knowledge, data and tools run through the
same graph, SqlGuard, MCP session and ToolGateway as the `hotel-agent` CLI. It has no
database, SQL, gateway or approval access of its own (static test), and there is no
approval endpoint.

NO AUTHENTICATION (local development only): the staff principal is bound by the server
operator through `AGENT_STAFF_CODE` when the API starts. Requests cannot name a staff
member, role or hotel — unknown fields are rejected — so whoever can reach the API acts as
that one configured staff member, with that member's permissions.
"""

from __future__ import annotations

from typing import Annotated, Any

import anyio
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from hotel_agent.agent.service import MAX_QUESTION_CHARS, AgentInputError, HotelAgent

router = APIRouter(prefix="/agent", tags=["agent"])

AUTHENTICATION_NOTE = (
    "none: local development only; the staff principal is fixed by server configuration "
    "(AGENT_STAFF_CODE), not by the request"
)


class AgentHolder:
    """The single agent session this API process is bound to (or None if not configured)."""

    def __init__(self, agent: HotelAgent | None) -> None:
        self.agent = agent
        # One request at a time through the shared MCP session (local demo throughput).
        self.lock = anyio.Lock()


class AskAgentRequest(BaseModel):
    # extra="forbid": staff_code, role, hotel_id, approval flags... are rejected (422).
    model_config = ConfigDict(extra="forbid")

    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
    ]


class PrincipalOut(BaseModel):
    staff_code: str
    role: str
    authentication: str = AUTHENTICATION_NOTE


class AskAgentResponse(BaseModel):
    route: str
    status: str
    code: str | None = None
    message: str
    tool: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    executed_sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    data: Any = None
    approval: dict[str, Any] | None = None
    request_sha256: str
    model: str
    latency_ms: float
    principal: PrincipalOut
    trace: list[dict[str, Any]] = Field(description="Per-node trace (arguments masked)")


@router.post(
    "/ask",
    responses={503: {"description": "Agent not configured (set AGENT_STAFF_CODE)"}},
)
async def ask_agent(body: AskAgentRequest, request: Request) -> AskAgentResponse:
    holder: AgentHolder | None = getattr(request.app.state, "agent", None)
    if holder is None or holder.agent is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "agent not configured")
    agent = holder.agent
    try:
        async with holder.lock:
            result = await agent.ask(body.question)
    except AgentInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    payload = jsonable_encoder(result)
    payload["principal"] = PrincipalOut(staff_code=agent.staff_code, role=agent.role)
    return AskAgentResponse(**payload)
