"""`hotel-sql` command line: ask, eval."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from hotel_agent.config import PROJECT_ROOT, Settings, get_settings
from hotel_agent.db.session import make_engine
from hotel_agent.logging_config import configure_logging
from hotel_agent.sql.context import load_context
from hotel_agent.sql.evaluation import build_pipeline, evaluate, load_cases
from hotel_agent.sql.executor import SqlExecutor
from hotel_agent.sql.generators import RuleBasedFakeGenerator
from hotel_agent.sql.guard import SqlGuard
from hotel_agent.sql.pipeline import SqlPipeline

EVAL_CASES = PROJECT_ROOT / "eval" / "sql_cases.json"
EVAL_RESULTS = PROJECT_ROOT / "eval" / "results"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def _cmd_ask(settings: Settings, args: argparse.Namespace) -> int:
    engine = make_engine(settings.sql_database_url(args.database))
    try:
        pipeline = SqlPipeline(
            RuleBasedFakeGenerator(), SqlGuard(), SqlExecutor(engine), load_context(engine)
        )
        result = pipeline.ask(args.question)
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "status": result.status,
                "code": result.code,
                "answer": result.answer,
                "clarification": result.clarification,
                "executed_sql": result.executed_sql,
                "params": result.params,
                "columns": result.columns,
                "rows": [list(r) for r in result.rows],
                "truncated": result.truncated,
                "business_date": result.business_date,
                "latency_ms": result.latency_ms,
            },
            default=_json_default,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_eval(settings: Settings, args: argparse.Namespace) -> int:
    cases = load_cases(EVAL_CASES)
    sql_engine = make_engine(settings.sql_database_url(args.database))
    admin_engine = make_engine(settings.database_url(args.database))
    try:
        executor = SqlExecutor(sql_engine)
        pipeline = build_pipeline(cases, executor, load_context(sql_engine))
        report = evaluate(cases, pipeline, admin_engine)
    finally:
        sql_engine.dispose()
        admin_engine.dispose()
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generator": "fake-rules-v1 + fake-scripted",
        "max_cost_ceiling": executor.max_cost,
        **report,
    }
    if not args.include_rows:
        report.pop("rows")
    out = json.dumps(report, default=_json_default, ensure_ascii=False, indent=2)
    if args.output:
        EVAL_RESULTS.mkdir(parents=True, exist_ok=True)
        (EVAL_RESULTS / args.output).write_text(out + "\n", encoding="utf-8", newline="\n")
    print(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="hotel-sql", description="Controlled Text-to-SQL.")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="Answer an operational-data question (read-only).")
    ask.add_argument("question")
    ask.add_argument("--database", default=None)
    ev = sub.add_parser("eval", help="Run the Text-to-SQL evaluation (eval/sql_cases.json).")
    ev.add_argument("--database", default=None)
    ev.add_argument("--output", default=None, help="File name under eval/results/.")
    ev.add_argument("--include-rows", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    configure_logging(settings.log_level, stream="stderr")  # stdout carries the JSON result
    handlers = {"ask": _cmd_ask, "eval": _cmd_eval}
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
