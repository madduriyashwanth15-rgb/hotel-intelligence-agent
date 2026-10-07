"""MCP layer: in-memory client sessions over the real gateway, plus one stdio process test."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from hotel_agent.ops.gateway import ToolGateway
from hotel_agent.ops.mcp_server import build_server
from hotel_agent.ops.principal import load_principal
from tests.conftest import OpsDatabase

pytestmark = [pytest.mark.db, pytest.mark.anyio]

EXPECTED_TOOLS = {
    "get_reservation": True, "get_room_status": True, "get_occupancy": True,
    "get_housekeeping_tasks": True, "calculate_refund": True, "get_approval_status": True,
    "create_housekeeping_task": False, "request_cancellation_refund": False,
}  # fmt: skip


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _gateway(db: OpsDatabase, code: str) -> ToolGateway:
    with db.ops.connect() as conn:
        return ToolGateway(db.ops, load_principal(conn, code))


def _payload(result: types.CallToolResult) -> dict[str, Any]:
    assert result.content
    first = result.content[0]
    assert isinstance(first, types.TextContent)
    data: dict[str, Any] = json.loads(first.text)
    assert result.structuredContent == data
    return data


async def test_list_tools_classification_and_strict_schemas(ops_db: OpsDatabase) -> None:
    async with create_connected_server_and_client_session(
        build_server(_gateway(ops_db, "S-FD-01"))
    ) as client:
        tools = (await client.list_tools()).tools
    assert {
        t.name: bool(t.annotations and t.annotations.readOnlyHint) for t in tools
    } == EXPECTED_TOOLS
    for tool in tools:
        assert tool.inputSchema.get("additionalProperties") is False
        assert tool.annotations is not None
        assert tool.annotations.openWorldHint is False
        if not EXPECTED_TOOLS[tool.name]:
            assert "requires human approval" in (tool.description or "")
    refund = next(t for t in tools if t.name == "request_cancellation_refund")
    assert refund.annotations is not None
    assert refund.annotations.destructiveHint is True
    assert "SENSITIVE" in (refund.description or "")
    assert not any("approve" in t.name for t in tools)


async def test_read_tool_over_mcp(ops_db: OpsDatabase) -> None:
    async with create_connected_server_and_client_session(
        build_server(_gateway(ops_db, "S-FD-01"))
    ) as client:
        result = await client.call_tool("get_occupancy", {})
    data = _payload(result)
    assert result.isError is False
    assert data["data"]["total_rooms"] == 80


async def test_mutating_tool_over_mcp_only_creates_pending_request(ops_db: OpsDatabase) -> None:
    async with create_connected_server_and_client_session(
        build_server(_gateway(ops_db, "S-FD-01"))
    ) as client:
        result = await client.call_tool(
            "create_housekeeping_task", {"room_number": "312", "task_type": "deep_clean"}
        )
        data = _payload(result)
        assert data["status"] == "pending_approval"
        status = _payload(
            await client.call_tool(
                "get_approval_status", {"approval_id": data["approval"]["approval_id"]}
            )
        )
    assert status["data"]["status"] == "pending"


@pytest.mark.parametrize(
    ("name", "args", "code"),
    [
        ("approve_request", {"approval_id": "a" * 24}, "unknown_tool"),
        ("get_reservation", {"confirmation_code": "x"}, "invalid_arguments"),
        ("get_occupancy", {"hotel_id": 2}, "invalid_arguments"),
        ("get_room_status", {"room_number": "101", "staff_code": "S-GM-01"}, "invalid_arguments"),
        ("request_cancellation_refund",
         {"confirmation_code": "GHR-AAAAAA", "guest_last_name": "X", "reason": "guest_request",
          "amount": "999999"}, "invalid_arguments"),
    ],
)  # fmt: skip
async def test_mcp_errors_are_tool_errors(
    ops_db: OpsDatabase, name: str, args: dict[str, Any], code: str
) -> None:
    async with create_connected_server_and_client_session(
        build_server(_gateway(ops_db, "S-FD-01"))
    ) as client:
        result = await client.call_tool(name, args)
    assert result.isError is True
    assert _payload(result)["error"]["code"] == code


async def test_principal_is_fixed_by_the_server(ops_db: OpsDatabase) -> None:
    """A housekeeping session cannot read reservations, whatever the client sends."""
    async with create_connected_server_and_client_session(
        build_server(_gateway(ops_db, "S-HK-01"))
    ) as client:
        result = await client.call_tool(
            "get_reservation", {"confirmation_code": "GHR-AAAAAA", "guest_last_name": "Smith"}
        )
    assert _payload(result)["error"]["code"] == "forbidden"


async def test_stdio_server_process(ops_db: OpsDatabase) -> None:
    """Start the real `hotel-mcp` entry point over stdio (logs must not corrupt stdout)."""
    env = {**os.environ, "POSTGRES_DB": ops_db.name, "LOG_LEVEL": "INFO"}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "hotel_agent.ops.mcp_server", "--staff-code", "S-FD-01"],
        env=env,
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.serverInfo.name == "hotel-intelligence-ops"
        tools = await session.list_tools()
        assert len(tools.tools) == len(EXPECTED_TOOLS)
        result = await session.call_tool("get_room_status", {"room_number": "101"})
        assert _payload(result)["data"]["room_number"] == "101"
