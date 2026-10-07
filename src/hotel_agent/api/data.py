"""POST /data/ask — read-only answers about operational data (controlled Text-to-SQL)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, StringConstraints

from hotel_agent.sql.pipeline import MAX_QUESTION_CHARS, QuestionValidationError, SqlPipeline

router = APIRouter(prefix="/data", tags=["data"])

QUERY_LABEL = "Generated and executed SQL (read-only; validated by the SQL guard)"


class PipelineHolder:
    """Builds the pipeline lazily (the business date needs a seeded database)."""

    def __init__(self, factory: Callable[[], SqlPipeline] | None) -> None:
        self._factory = factory
        self._pipeline: SqlPipeline | None = None
        self._lock = threading.Lock()

    def get(self) -> SqlPipeline | None:
        if self._pipeline is None and self._factory is not None:
            with self._lock:
                if self._pipeline is None:
                    try:
                        self._pipeline = self._factory()
                    except Exception:
                        return None
        return self._pipeline


class AskDataRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
    ]


class QueryOut(BaseModel):
    label: str = QUERY_LABEL
    sql: str | None = Field(description="Exact SQL that was executed, or null if none ran")
    parameters: dict[str, Any]


class DataOut(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool


class AskDataResponse(BaseModel):
    status: Literal["answered", "refused", "error"]
    code: str | None
    answer: str = Field(description="Natural-language answer, separate from the SQL")
    clarification: str | None
    data: DataOut
    query: QueryOut
    business_date: date | None
    generator: str
    latency_ms: float


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


@router.post("/ask", responses={503: {"description": "Text-to-SQL not available"}})
def ask_data(body: AskDataRequest, request: Request) -> AskDataResponse:
    holder: PipelineHolder = request.app.state.sql_pipeline
    pipeline = holder.get()
    if pipeline is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "operational data not available")
    try:
        result = pipeline.ask(body.question)
    except QuestionValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return AskDataResponse(
        status=result.status,  # type: ignore[arg-type]
        code=result.code,
        answer=result.answer,
        clarification=result.clarification,
        data=DataOut(
            columns=result.columns,
            rows=[[_json_value(v) for v in row] for row in result.rows],
            row_count=len(result.rows),
            truncated=result.truncated,
        ),
        query=QueryOut(
            sql=result.executed_sql,
            parameters={k: _json_value(v) for k, v in result.params.items()},
        ),
        business_date=result.business_date,
        generator=result.generator,
        latency_ms=result.latency_ms,
    )
