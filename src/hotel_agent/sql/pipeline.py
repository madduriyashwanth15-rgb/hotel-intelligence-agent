"""Text-to-SQL pipeline: generate -> guard -> cost check + execute -> summarize -> audit log.

Every stage's failure becomes a clear refusal/error with a stable code. The executed SQL is
returned separately from the natural-language answer (ADR-0015). The audit log never
contains the question text or literal values (JSON logs only; ADR-0015).
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import sqlglot
from sqlglot import exp

from hotel_agent.sql.context import SchemaContext
from hotel_agent.sql.executor import SqlExecutionError, SqlExecutor
from hotel_agent.sql.formatting import summarize
from hotel_agent.sql.generators import Generated, Refusal, SqlGenerator
from hotel_agent.sql.guard import SqlGuard, SqlRejectedError

MAX_QUESTION_CHARS = 500
audit_logger = logging.getLogger("hotel_agent.sql.audit")

USER_MESSAGES = {
    "invalid_sql": "The generated query was not valid, so it was not run.",
    "unsafe_sql": "The generated query was blocked because it was not a safe read-only query.",
    "disallowed_relation": "The generated query tried to read data that is not available "
    "for analytics, so it was blocked.",
    "disallowed_function": "The generated query used an operation that is not allowed, so it "
    "was blocked.",
    "invalid_parameters": "The generated query had invalid parameters, so it was not run.",
    "too_expensive": "The generated query would be too expensive to run, so it was blocked.",
    "timeout": "The query took too long and was cancelled.",
    "execution_error": "The query could not be completed.",
}


class QuestionValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DataAnswer:
    question_sha256: str
    status: str  # answered | refused | error
    code: str | None
    answer: str
    clarification: str | None = None
    executed_sql: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    truncated: bool = False
    intent: str | None = None
    period_label: str | None = None
    business_date: date | None = None
    generator: str = ""
    estimated_cost: float | None = None
    latency_ms: float = 0.0
    rule: str | None = None


def fingerprint(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def mask_sql(sql: str) -> str:
    """SQL with every literal replaced by '?' (for logs). Falls back to a placeholder."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        return "<unparseable>"
    if tree is None:
        return "<empty>"
    masked = tree.transform(lambda n: exp.Var(this="?") if isinstance(n, exp.Literal) else n)
    return masked.sql(dialect="postgres", comments=False)[:2000]


class SqlPipeline:
    def __init__(
        self,
        generator: SqlGenerator,
        guard: SqlGuard,
        executor: SqlExecutor,
        context: SchemaContext,
    ) -> None:
        self.generator = generator
        self.guard = guard
        self.executor = executor
        self.context = context

    def ask(self, question: str) -> DataAnswer:
        start = time.perf_counter()
        cleaned = " ".join(question.split()) if isinstance(question, str) else ""
        if not cleaned or len(cleaned) > MAX_QUESTION_CHARS:
            raise QuestionValidationError(f"question must be 1-{MAX_QUESTION_CHARS} characters")
        base: dict[str, Any] = {
            "question_sha256": fingerprint(cleaned),
            "business_date": self.context.business_date,
            "generator": self.generator.name,
        }

        generated = self.generator.generate(cleaned, self.context)
        if isinstance(generated, Refusal):
            result = DataAnswer(
                **base,
                status="refused",
                code=generated.code.value,
                answer=generated.message,
                clarification=generated.clarification,
            )
            return self._log(result, start, None)

        if not isinstance(generated, Generated):  # defensive: protocol violation
            raise TypeError("generator returned an unexpected type")
        base.update(intent=generated.intent, period_label=generated.period_label)
        try:
            validated = self.guard.validate(generated.sql, generated.params)
            outcome = self.executor.execute(validated)
        except SqlRejectedError as exc:
            result = DataAnswer(
                **base, status="refused", code=exc.code.value, rule=exc.rule,
                answer=USER_MESSAGES[exc.code.value],
            )  # fmt: skip
            return self._log(result, start, generated.sql)
        except SqlExecutionError as exc:
            result = DataAnswer(
                **base, status="error", code=exc.code, answer=USER_MESSAGES[exc.code],
                executed_sql=validated.sql, params=dict(validated.params),
            )  # fmt: skip
            return self._log(result, start, validated.sql)

        answer = summarize(
            generated.intent, generated.period_label, outcome.columns, outcome.rows,
            self.context.business_date, outcome.truncated,
        )  # fmt: skip
        result = DataAnswer(
            **base,
            status="answered",
            code=None,
            answer=answer,
            executed_sql=validated.sql,
            params=dict(validated.params),
            columns=outcome.columns,
            rows=outcome.rows,
            truncated=outcome.truncated,
            estimated_cost=outcome.estimated_cost,
        )
        return self._log(result, start, validated.sql)

    @staticmethod
    def _log(result: DataAnswer, start: float, sql: str | None) -> DataAnswer:
        latency = (time.perf_counter() - start) * 1000
        final = DataAnswer(**{**_as_dict(result), "latency_ms": round(latency, 1)})
        audit_logger.info(
            "sql question processed",
            extra={
                "question_sha256": final.question_sha256,
                "generator": final.generator,
                "intent": final.intent,
                "status": final.status,
                "code": final.code,
                "rule": final.rule,
                "sql_masked": mask_sql(sql) if sql else None,
                "sql_sha256": hashlib.sha256(sql.encode()).hexdigest()[:16] if sql else None,
                "param_names": sorted(final.params),
                "row_count": len(final.rows),
                "truncated": final.truncated,
                "estimated_cost": final.estimated_cost,
                "latency_ms": final.latency_ms,
            },
        )
        return final


def _as_dict(answer: DataAnswer) -> dict[str, Any]:
    return {name: getattr(answer, name) for name in DataAnswer.__dataclass_fields__}
