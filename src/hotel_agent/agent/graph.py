"""LangGraph workflow: route -> validate -> {knowledge | data | tool | clarify | refuse} -> respond.

This module has no database, SQL or approval access (checked by a static test). It only
calls the capability protocols; every authorization decision is made downstream.
"""

from __future__ import annotations

import hashlib
import operator
from typing import Annotated, Any, TypedDict

import anyio
from langgraph.graph import END, START, StateGraph

from hotel_agent.agent.capabilities import DataService, KnowledgeService, ToolClient
from hotel_agent.agent.router import Router
from hotel_agent.agent.schemas import Route, RouteDecision
from hotel_agent.ops.audit import mask_arguments

REFUSAL_MESSAGES = {
    "personal_data_request": "Guest personal or contact data cannot be provided.",
    "out_of_scope": "This request is outside hotel operations.",
    "approval_not_allowed": "The assistant cannot approve or execute requests. Approvals are "
    "made by an authorised staff member in the approval system.",
    "unsupported_action": "That action is not supported. The assistant can look things up, "
    "create housekeeping-task requests and cancellation-refund requests for approval.",
    "sql_content_not_accepted": "Please ask in plain language; SQL is not accepted.",
    "other_hotel_not_allowed": "This session only covers your own hotel.",
    "unknown_tool": "The requested tool does not exist.",
    "invalid_model_output": "The request could not be routed safely.",
    "model_error": "The request could not be routed.",
}
DEFAULT_REFUSAL = "The request cannot be handled."


class AgentState(TypedDict, total=False):
    question: str
    request_sha256: str
    decision: dict[str, Any]
    outcome: dict[str, Any]
    response: dict[str, Any]
    trace: Annotated[list[dict[str, Any]], operator.add]


def request_hash(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def build_graph(
    router: Router,
    knowledge: KnowledgeService,
    data: DataService,
    tools: ToolClient,
) -> Any:
    async def route_node(state: AgentState) -> dict[str, Any]:
        catalog = await tools.list_tools()
        decision = await anyio.to_thread.run_sync(router.decide, state["question"], catalog)
        event = {
            "node": "route",
            "model": router.llm.name,
            "route": decision.route.value,
            "reason": decision.reason,
            "tool": decision.tool.name if decision.tool else None,
        }
        return {"decision": decision.model_dump(mode="json"), "trace": [event]}

    async def validate_node(state: AgentState) -> dict[str, Any]:
        decision = RouteDecision.model_validate(state["decision"])
        if decision.route is Route.TOOL and decision.tool is not None:
            names = {t.name for t in await tools.list_tools()}
            if decision.tool.name not in names:
                decision = RouteDecision(route=Route.REFUSAL, reason="unknown_tool")
                return {
                    "decision": decision.model_dump(mode="json"),
                    "trace": [{"node": "validate", "ok": False, "reason": "unknown_tool"}],
                }
        return {"trace": [{"node": "validate", "ok": True}]}

    def dispatch(state: AgentState) -> str:
        return str(state["decision"]["route"])

    async def knowledge_node(state: AgentState) -> dict[str, Any]:
        result = await anyio.to_thread.run_sync(knowledge.answer, state["question"])
        cites = [c["citation"] for c in result.get("citations", [])]
        return {
            "outcome": {"kind": "knowledge", **result},
            "trace": [{"node": "knowledge", "status": result["status"], "citations": cites}],
        }

    async def data_node(state: AgentState) -> dict[str, Any]:
        result = await anyio.to_thread.run_sync(data.ask, state["question"])
        sql = result.get("executed_sql")
        event = {
            "node": "data",
            "status": result["status"],
            "code": result.get("code"),
            "sql_sha256": hashlib.sha256(sql.encode()).hexdigest()[:16] if sql else None,
        }
        return {"outcome": {"kind": "data", **result}, "trace": [event]}

    async def tool_node(state: AgentState) -> dict[str, Any]:
        decision = RouteDecision.model_validate(state["decision"])
        if decision.tool is None:  # impossible after RouteDecision validation
            raise RuntimeError("TOOL route without a tool")
        catalog = {t.name: t for t in await tools.list_tools()}
        # Arguments go to the ToolGateway unchanged; it validates, authorizes and scopes.
        payload = await tools.call_tool(decision.tool.name, dict(decision.tool.arguments))
        approval = payload.get("approval") or {}
        event = {
            "node": "tool",
            "tool": decision.tool.name,
            "read_only": catalog[decision.tool.name].read_only,
            "arguments": mask_arguments(decision.tool.arguments),
            "gateway_ok": payload.get("ok"),
            "gateway_code": (payload.get("error") or {}).get("code"),
            "gateway_status": payload.get("status"),
            "approval_id": approval.get("approval_id"),
        }
        return {
            "outcome": {"kind": "tool", "tool": decision.tool.name, **payload},
            "trace": [event],
        }

    async def clarify_node(state: AgentState) -> dict[str, Any]:
        decision = RouteDecision.model_validate(state["decision"])
        return {
            "outcome": {"kind": "clarification", "message": decision.clarification},
            "trace": [{"node": "clarify", "reason": decision.reason}],
        }

    async def refuse_node(state: AgentState) -> dict[str, Any]:
        reason = str(state["decision"]["reason"])
        return {
            "outcome": {
                "kind": "refusal",
                "reason": reason,
                "message": REFUSAL_MESSAGES.get(reason, DEFAULT_REFUSAL),
            },
            "trace": [{"node": "refuse", "reason": reason}],
        }

    async def respond_node(state: AgentState) -> dict[str, Any]:
        response = compose_response(state["decision"], state["outcome"])
        return {"response": response, "trace": [{"node": "respond", "status": response["status"]}]}

    graph = StateGraph(AgentState)
    graph.add_node("route", route_node)
    graph.add_node("validate", validate_node)
    graph.add_node("knowledge", knowledge_node)
    graph.add_node("data", data_node)
    graph.add_node("tool", tool_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("refuse", refuse_node)
    graph.add_node("respond", respond_node)
    graph.add_edge(START, "route")
    graph.add_edge("route", "validate")
    graph.add_conditional_edges(
        "validate",
        dispatch,
        {
            Route.KNOWLEDGE.value: "knowledge",
            Route.DATA.value: "data",
            Route.TOOL.value: "tool",
            Route.CLARIFICATION.value: "clarify",
            Route.REFUSAL.value: "refuse",
        },
    )
    for node in ("knowledge", "data", "tool", "clarify", "refuse"):
        graph.add_edge(node, "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def compose_response(decision: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    kind = outcome["kind"]
    route = decision["route"]
    if kind == "knowledge":
        answered = outcome["status"] == "answered"
        return {
            "route": route,
            "status": "answered" if answered else "insufficient_evidence",
            "message": outcome["answer"],
            "citations": outcome.get("citations", []),
        }
    if kind == "data":
        status = outcome["status"]
        if outcome.get("code") == "ambiguous_question":
            status = "clarification"
        message = outcome["answer"] + (
            f" {outcome['clarification']}" if outcome.get("clarification") else ""
        )
        return {
            "route": route,
            "status": status,
            "code": outcome.get("code"),
            "message": message,
            "executed_sql": outcome.get("executed_sql"),
            "columns": outcome.get("columns", []),
            "rows": outcome.get("rows", []),
        }
    if kind == "tool":
        if not outcome.get("ok"):
            err = outcome.get("error") or {}
            return {
                "route": route,
                "status": "refused",
                "code": err.get("code"),
                "message": err.get("message", "The tool refused the request."),
                "tool": outcome["tool"],
            }
        if outcome.get("status") == "pending_approval":
            approval = outcome["approval"]
            return {
                "route": route,
                "status": "pending_approval",
                "tool": outcome["tool"],
                "message": f"{approval['summary']} Request {approval['approval_id']} is awaiting "
                "human approval; nothing has been executed.",
                "approval": approval,
            }
        return {
            "route": route,
            "status": "answered",
            "tool": outcome["tool"],
            "message": f"Result from {outcome['tool']}.",
            "data": outcome.get("data"),
        }
    if kind == "clarification":
        return {"route": route, "status": "clarification", "message": outcome["message"]}
    return {
        "route": route,
        "status": "refused",
        "code": outcome["reason"],
        "message": outcome["message"],
    }
