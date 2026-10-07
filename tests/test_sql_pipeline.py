"""End-to-end pipeline against the seeded test database (as hotel_sql)."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from hotel_agent.logging_config import JsonFormatter
from hotel_agent.sql.context import load_context
from hotel_agent.sql.executor import SqlExecutor
from hotel_agent.sql.generators import Generated, RuleBasedFakeGenerator, ScriptedFakeGenerator
from hotel_agent.sql.guard import SqlGuard
from hotel_agent.sql.pipeline import QuestionValidationError, SqlPipeline, mask_sql

pytestmark = pytest.mark.db


def _pipeline(engine: Engine, script: dict[str, Generated] | None = None) -> SqlPipeline:
    generator = (
        ScriptedFakeGenerator(script, fallback=RuleBasedFakeGenerator())  # type: ignore[arg-type]
        if script
        else RuleBasedFakeGenerator()
    )
    return SqlPipeline(generator, SqlGuard(), SqlExecutor(engine), load_context(engine))


@pytest.fixture
def pipeline(sql_role_engine: Engine) -> SqlPipeline:
    return _pipeline(sql_role_engine)


@pytest.fixture
def audit_log() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("hotel_agent.sql.audit")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield stream
    logger.removeHandler(handler)


def test_occupancy_tonight(pipeline: SqlPipeline) -> None:
    result = pipeline.ask("How many rooms are occupied tonight?")
    assert result.status == "answered"
    assert result.answer == "Occupancy on 2026-10-01: 70.00% (56 of 80 rooms occupied)."
    assert result.executed_sql is not None
    assert "business_date" in result.executed_sql
    assert result.columns == ["stay_date", "occupied_rooms", "total_rooms", "occupancy_pct"]


def test_revenue_this_month_explains_zero(pipeline: SqlPipeline) -> None:
    result = pipeline.ask("What revenue was generated this month?")
    assert result.status == "answered"
    assert result.answer.startswith("Posted revenue (this month): ₹0.00.")
    assert "latest posted night is 2026-09-30" in result.answer


def test_revenue_last_month_is_positive(pipeline: SqlPipeline) -> None:
    result = pipeline.ask("What revenue was generated last month?")
    assert result.status == "answered"
    assert result.rows[0][0] > 0
    assert "no posted revenue" not in result.answer


def test_refusal_has_no_sql(pipeline: SqlPipeline) -> None:
    result = pipeline.ask("Cancel booking 42")
    assert result.status == "refused"
    assert result.code == "mutation_requested"
    assert result.executed_sql is None
    assert result.rows == []


def test_question_length_validated(pipeline: SqlPipeline) -> None:
    with pytest.raises(QuestionValidationError):
        pipeline.ask("   ")
    with pytest.raises(QuestionValidationError):
        pipeline.ask("x" * 501)


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT email FROM public.guests", "disallowed_relation"),
        ("WITH d AS (DELETE FROM reservations RETURNING *) SELECT * FROM d", "unsafe_sql"),
        ("SELECT pg_sleep(30)", "disallowed_function"),
        ("SELECT count(*) FROM reservations WHERE check_in_date = CURRENT_DATE",
         "disallowed_function"),
        ("SELECT count(*) FROM reservations a CROSS JOIN reservations b CROSS JOIN reservations c",
         "too_expensive"),
        ("SELECT missing_column FROM reservations", "invalid_sql"),
    ],
)  # fmt: skip
def test_compromised_model_output_is_blocked(sql_role_engine: Engine, sql: str, code: str) -> None:
    pipeline = _pipeline(sql_role_engine, {"q": Generated(sql, {}, "scripted")})
    result = pipeline.ask("q")
    assert result.status == "refused"
    assert result.code == code
    assert result.rows == []


def test_audit_log_has_no_question_text_or_literals(
    sql_role_engine: Engine, audit_log: io.StringIO
) -> None:
    secret_question = "How many reservations are confirmed?"
    _pipeline(sql_role_engine).ask(secret_question)
    _pipeline(
        sql_role_engine,
        {"q2": Generated("SELECT count(*) FROM reservations WHERE channel = 'corporate'", {}, "x")},
    ).ask("q2")
    lines = [json.loads(line) for line in audit_log.getvalue().splitlines()]
    assert len(lines) == 2
    assert all("question_sha256" in r and r["status"] == "answered" for r in lines)
    raw = audit_log.getvalue()
    assert secret_question not in raw
    assert "corporate" not in raw  # literal masked
    assert "confirmed" not in raw  # parameter value not logged
    assert lines[0]["param_names"] == ["status"]
    assert "?" in lines[1]["sql_masked"]


def test_mask_sql_replaces_literals() -> None:
    masked = mask_sql("SELECT count(*) FROM rooms WHERE room_number = '101' AND floor = 1")
    assert "101" not in masked
    assert masked.count("?") == 2
