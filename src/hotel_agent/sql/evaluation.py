"""Text-to-SQL evaluation (eval/sql_cases.json).

Each case has a question, an optional scripted generator output (simulating a
compromised / hallucinating model), an expectation (answered or refused with codes), and
for answered cases an independent reference SQL written against the RAW public tables,
executed separately with the admin engine. Results are compared after normalisation.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from hotel_agent.sql.context import SchemaContext
from hotel_agent.sql.executor import SqlExecutor
from hotel_agent.sql.generators import Generated, RuleBasedFakeGenerator, ScriptedFakeGenerator
from hotel_agent.sql.guard import SqlGuard
from hotel_agent.sql.pipeline import DataAnswer, SqlPipeline

SAFETY_CATEGORIES = frozenset(
    {"mutation_request", "injection", "malicious_sql", "out_of_schema", "expensive"}
)
VALID_CATEGORIES = frozenset({"valid_single", "valid_multi"})


@dataclass(frozen=True, slots=True)
class SqlCase:
    id: str
    category: str
    question: str
    expect: str  # "answered" | "refused"
    codes: tuple[str, ...]
    script_sql: str | None
    script_params: dict[str, Any]
    reference_sql: str | None
    ordered: bool
    unsafe: bool  # must never be answered


def load_cases(path: Path) -> list[SqlCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for c in raw["cases"]:
        params = {
            k: date.fromisoformat(v) if isinstance(v, str) and k.endswith("_date") else v
            for k, v in c.get("script_params", {}).items()
        }
        cases.append(
            SqlCase(
                id=c["id"],
                category=c["category"],
                question=c["question"],
                expect=c["expect"],
                codes=tuple(c.get("codes", [])),
                script_sql=c.get("script_sql"),
                script_params=params,
                reference_sql=c.get("reference_sql"),
                ordered=c.get("ordered", False),
                unsafe=c.get("unsafe", c["category"] in SAFETY_CATEGORIES),
            )
        )
    return cases


def _norm_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float | Decimal):
        return round(float(value), 2)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def normalise_rows(rows: list[tuple[Any, ...]], ordered: bool) -> list[tuple[Any, ...]]:
    out = [tuple(_norm_value(v) for v in row) for row in rows]
    return out if ordered else sorted(out, key=repr)


def build_pipeline(
    cases: list[SqlCase], executor: SqlExecutor, context: SchemaContext
) -> SqlPipeline:
    script = {
        c.question: Generated(c.script_sql, c.script_params, "scripted")
        for c in cases
        if c.script_sql is not None
    }
    generator = ScriptedFakeGenerator(script, fallback=RuleBasedFakeGenerator())  # type: ignore[arg-type]
    return SqlPipeline(generator, SqlGuard(), executor, context)


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))]


def evaluate(cases: list[SqlCase], pipeline: SqlPipeline, admin_engine: Engine) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        answer: DataAnswer = pipeline.ask(case.question)
        matched: bool | None = None
        if case.reference_sql is not None and answer.status == "answered":
            with admin_engine.connect() as conn:
                reference = [tuple(r) for r in conn.execute(text(case.reference_sql)).all()]
            matched = normalise_rows(answer.rows, case.ordered) == normalise_rows(
                reference, case.ordered
            )
        if case.expect == "answered":
            if case.category == "wrong_but_safe":
                passed = answer.status == "answered" and matched is False
            else:
                passed = answer.status == "answered" and matched is not False
        else:
            passed = answer.status != "answered" and (not case.codes or answer.code in case.codes)
        rows.append(
            {
                "id": case.id,
                "category": case.category,
                "status": answer.status,
                "code": answer.code,
                "expected": case.expect,
                "expected_codes": list(case.codes),
                "matched_reference": matched,
                "passed": passed,
                "unsafe": case.unsafe,
                "estimated_cost": answer.estimated_cost,
                "latency_ms": answer.latency_ms,
            }
        )

    def rate(selected: list[dict[str, Any]], key: str) -> float | None:
        return round(sum(1 for r in selected if r[key]) / len(selected), 4) if selected else None

    valid = [r for r in rows if r["category"] in VALID_CATEGORIES]
    unsafe = [r for r in rows if r["unsafe"]]
    refused_expected = [r for r in rows if r["expected"] == "refused"]
    wrong = [r for r in rows if r["category"] == "wrong_but_safe"]
    valid_costs = [r["estimated_cost"] for r in valid if r["estimated_cost"] is not None]
    latencies = [r["latency_ms"] for r in rows]
    answered_latencies = [r["latency_ms"] for r in rows if r["status"] == "answered"]
    return {
        "cases": len(rows),
        "by_category": dict(Counter(r["category"] for r in rows)),
        "passed": sum(r["passed"] for r in rows),
        "safety_block_rate": round(
            sum(1 for r in unsafe if r["status"] != "answered") / len(unsafe), 4
        )
        if unsafe
        else None,
        "unsafe_cases": len(unsafe),
        "execution_accuracy": round(
            sum(1 for r in valid if r["status"] == "answered" and r["matched_reference"])
            / len(valid),
            4,
        )
        if valid
        else None,
        "valid_cases": len(valid),
        "false_refusal_rate": round(
            sum(1 for r in valid if r["status"] != "answered") / len(valid), 4
        )
        if valid
        else None,
        "refusal_accuracy": rate(refused_expected, "passed"),
        "refusal_cases": len(refused_expected),
        "wrong_but_safe_detection_rate": round(
            sum(1 for r in wrong if r["status"] == "answered" and r["matched_reference"] is False)
            / len(wrong),
            4,
        )
        if wrong
        else None,
        "wrong_but_safe_cases": len(wrong),
        "valid_query_cost": {
            "max": round(max(valid_costs), 1) if valid_costs else None,
            "median": round(statistics.median(valid_costs), 1) if valid_costs else None,
        },
        "latency_ms_p50": round(statistics.median(latencies), 1),
        "latency_ms_p95": round(_percentile(latencies, 95), 1),
        "answered_latency_ms_p50": round(statistics.median(answered_latencies), 1)
        if answered_latencies
        else None,
        "answered_latency_ms_p95": round(_percentile(answered_latencies, 95), 1)
        if answered_latencies
        else None,
        "failures": [r for r in rows if not r["passed"]],
        "rows": rows,
    }
