"""`hotel-ops` CLI (human approval channel) against the isolated ops database."""

from __future__ import annotations

import json
from typing import Any

import pytest

from hotel_agent.ops import cli
from tests.conftest import OpsDatabase

pytestmark = pytest.mark.db


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else {}


def test_request_list_approve_flow(ops_db: OpsDatabase, capsys: pytest.CaptureFixture[str]) -> None:
    db = ("--database", ops_db.name)
    code, created = _run(
        capsys, "call", "create_housekeeping_task", "--as", "S-FD-01",
        "--args", json.dumps({"room_number": "318", "task_type": "inspection"}), *db,
    )  # fmt: skip
    assert code == 0
    approval_id = created["approval"]["approval_id"]

    code, listing = _run(capsys, "approvals", "list", "--as", "S-HS-01", *db)
    assert code == 0
    entry = next(p for p in listing["pending"] if p["approval_id"] == approval_id)
    assert entry["you_may_approve"] is True
    assert entry["requested_by"] == "S-FD-01"

    code, refused = _run(capsys, "approvals", "approve", approval_id, "--as", "S-FD-01", *db)
    assert code == 1
    assert refused["error"]["code"] == "forbidden"

    code, done = _run(
        capsys, "approvals", "approve", approval_id, "--as", "S-HS-01", "--note", "ok", *db
    )
    assert code == 0
    assert done["status"] == "executed"


def test_cli_rejects_bad_identity_and_bad_json(
    ops_db: OpsDatabase, capsys: pytest.CaptureFixture[str]
) -> None:
    db = ("--database", ops_db.name)
    assert cli.main(["approvals", "list", "--as", "S-FD-99", *db]) == 2  # inactive
    assert cli.main(["call", "get_occupancy", "--as", "S-FD-01", "--args", "not json", *db]) == 2
    code, result = _run(capsys, "call", "get_occupancy", "--as", "S-FD-01", "--args", "[]", *db)
    assert code == 1
    assert result["error"]["code"] == "invalid_arguments"
