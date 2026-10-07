"""`hotel-agent`: single entry point for natural-language staff requests (no frontend).

    uv run hotel-agent ask "How many rooms are occupied tonight?" --as S-FD-01
    uv run hotel-agent ask "Create a deep clean task for room 219" --as S-FD-01 --mcp stdio
    uv run hotel-agent eval --output agent_eval.json
    uv run hotel-agent eval --cases adversarial --llm ollama --no-fallback --output x.json

The staff code is fixed for the session (no authentication in this phase). `--llm` picks
the router model (default: LLM_PROVIDER, i.e. the deterministic router); `ollama` needs a
local Ollama server on a loopback address (ADR-0025).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

import anyio

from hotel_agent.agent.evaluation import CASE_SETS, load_agent_cases, run_agent_eval
from hotel_agent.agent.factory import open_agent, router_models
from hotel_agent.agent.service import AgentInputError
from hotel_agent.config import PROJECT_ROOT, Settings, get_settings
from hotel_agent.logging_config import configure_logging
from hotel_agent.ops.principal import PrincipalError

EVAL_RESULTS = PROJECT_ROOT / "eval" / "results"


def _settings(llm: str | None, fallback: bool | None) -> Settings:
    settings = get_settings()
    update: dict[str, Any] = {}
    if llm:
        update["llm_provider"] = llm
    if fallback is not None:
        update["llm_fallback"] = fallback
    return settings.model_copy(update=update) if update else settings


async def _ask(
    settings: Settings, staff_code: str, question: str, database: str | None, mcp_mode: str
) -> dict[str, Any]:
    async with open_agent(settings, staff_code, database=database, mcp_mode=mcp_mode) as agent:  # type: ignore[arg-type]
        return await agent.ask(question)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hotel-agent", description="Hotel staff assistant (routing agent)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--as", dest="staff_code", required=True)
    ask.add_argument("--mcp", choices=["memory", "stdio"], default="memory")
    ask.add_argument("--database", default=None)
    ev = sub.add_parser("eval", help="Run an agent routing/safety evaluation set.")
    ev.add_argument("--cases", choices=sorted(CASE_SETS), default="dev")
    ev.add_argument("--database", default=None)
    for p in (ask, ev):
        p.add_argument("--llm", choices=["deterministic", "ollama"], default=None)
        p.add_argument(
            "--no-fallback",
            dest="fallback",
            action="store_false",
            default=None,
            help="Do not route model failures with the deterministic router.",
        )
    ev.add_argument("--output", default=None)
    ev.add_argument("--include-rows", action="store_true")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = _settings(args.llm, args.fallback)
    configure_logging(settings.log_level, stream="stderr")
    try:
        if args.command == "ask":
            result = anyio.run(
                _ask, settings, args.staff_code, args.question, args.database, args.mcp
            )
        else:
            report = anyio.run(
                run_agent_eval,
                settings,
                load_agent_cases(CASE_SETS[args.cases]),
                args.database,
                lambda: router_models(settings),
            )
            primary, fallback = router_models(settings)
            report = {
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "case_set": args.cases,
                "router_model": primary.name,
                "fallback_model": fallback.name if fallback else None,
                **report,
            }
            if not args.include_rows:
                report.pop("rows", None)
            if args.output:
                EVAL_RESULTS.mkdir(parents=True, exist_ok=True)
                (EVAL_RESULTS / args.output).write_text(
                    json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
            result = report
    except (PrincipalError, AgentInputError) as exc:
        print(f"hotel-agent: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
