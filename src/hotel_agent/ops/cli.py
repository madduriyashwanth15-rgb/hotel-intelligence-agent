"""`hotel-ops`: the HUMAN channel for approvals, plus manual tool calls for staff.

    uv run hotel-ops approvals list --as S-DM-01
    uv run hotel-ops approvals approve <approval_id> --as S-DM-01 [--note TEXT]
    uv run hotel-ops approvals reject  <approval_id> --as S-DM-01 [--note TEXT]
    uv run hotel-ops call get_occupancy --as S-FD-01 --args '{}'

Approvals are intentionally not reachable through MCP (ADR-0019).
"""

from __future__ import annotations

import argparse
import json
import sys

from hotel_agent.config import get_settings
from hotel_agent.db.session import make_engine
from hotel_agent.logging_config import configure_logging
from hotel_agent.ops.approvals import ApprovalService
from hotel_agent.ops.gateway import ToolGateway
from hotel_agent.ops.principal import PrincipalError, load_principal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hotel-ops", description="Hotel operations (human channel)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    appr = sub.add_parser("approvals", help="List or decide approval requests.")
    appr_sub = appr.add_subparsers(dest="action", required=True)
    lst = appr_sub.add_parser("list")
    lst.add_argument("--as", dest="staff_code", required=True)
    for action in ("approve", "reject"):
        p = appr_sub.add_parser(action)
        p.add_argument("approval_id")
        p.add_argument("--as", dest="staff_code", required=True)
        p.add_argument("--note", default=None)
    call = sub.add_parser("call", help="Call a tool through the gateway (as a staff member).")
    call.add_argument("tool")
    call.add_argument("--as", dest="staff_code", required=True)
    call.add_argument("--args", default="{}", help="JSON object of tool arguments.")
    for p in (call, *appr_sub.choices.values()):  # lst is one of the choices
        p.add_argument("--database", default=None)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    configure_logging(settings.log_level, stream="stderr")
    engine = make_engine(settings.ops_database_url(args.database))
    try:
        with engine.connect() as conn:
            principal = load_principal(conn, args.staff_code)
        if args.command == "call":
            try:
                tool_args = json.loads(args.args)
            except json.JSONDecodeError:
                print("hotel-ops: --args must be a JSON object", file=sys.stderr)
                return 2
            result = ToolGateway(engine, principal).call(args.tool, tool_args)
        elif args.action == "list":
            result = {"pending": ApprovalService(engine).list_pending(principal)}
        else:
            result = ApprovalService(engine).decide(
                principal, args.approval_id, approve=args.action == "approve", note=args.note
            )
    except PrincipalError as exc:
        print(f"hotel-ops: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
