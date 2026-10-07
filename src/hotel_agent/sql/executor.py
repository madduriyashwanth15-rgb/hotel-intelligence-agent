"""Execute validated SQL as the hotel_sql role (ADR-0014, ADR-0015).

Every query runs in its own read-only transaction with a per-statement timeout and a
fixed search_path, is cost-checked with EXPLAIN first, fetches at most max_rows + 1 rows,
and is always rolled back. Database errors are mapped to stable codes; raw database
messages are never returned to callers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from hotel_agent.sql.guard import RejectionCode, SqlRejectedError, ValidatedQuery

DEFAULT_TIMEOUT_MS = 2_000
# Planner cost ceiling; calibrated from the valid evaluation queries (see eval results).
DEFAULT_MAX_COST = 500_000.0


class ExecutionCode:
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"


class SqlExecutionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    estimated_cost: float
    elapsed_ms: float


class SqlExecutor:
    def __init__(
        self,
        engine: Engine,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        max_cost: float = DEFAULT_MAX_COST,
    ) -> None:
        if not 100 <= timeout_ms <= 10_000:
            raise ValueError("timeout_ms must be between 100 and 10000")
        self._engine = engine
        self.timeout_ms = timeout_ms
        self.max_cost = max_cost

    def _prepare(self, conn: Connection) -> None:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        # Values are code constants (int validated above), not user input.
        conn.execute(text(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}"))
        conn.execute(text("SET LOCAL search_path = analytics"))

    @staticmethod
    def _estimated_cost(conn: Connection, query: ValidatedQuery) -> float:
        plan = conn.execute(text("EXPLAIN (FORMAT JSON) " + query.sql), query.params).scalar_one()
        return float(plan[0]["Plan"]["Total Cost"])

    def explain_cost(self, query: ValidatedQuery) -> float:
        with self._engine.connect() as conn:
            trans = conn.begin()
            try:
                self._prepare(conn)
                return self._estimated_cost(conn, query)
            except DBAPIError as exc:
                raise _map_error(exc) from None
            finally:
                trans.rollback()

    def execute(self, query: ValidatedQuery) -> QueryResult:
        start = time.perf_counter()
        with self._engine.connect() as conn:
            trans = conn.begin()
            try:
                self._prepare(conn)
                cost = self._estimated_cost(conn, query)
                if cost > self.max_cost:
                    raise SqlRejectedError(
                        RejectionCode.TOO_EXPENSIVE,
                        "cost_ceiling",
                        f"estimated cost {cost:.0f} exceeds {self.max_cost:.0f}",
                    )
                result = conn.execute(text(query.sql), query.params)
                columns = list(result.keys())
                fetched = result.fetchmany(query.max_rows + 1)
            except DBAPIError as exc:
                raise _map_error(exc) from None
            finally:
                trans.rollback()  # never commit anything
        rows = [tuple(r) for r in fetched[: query.max_rows]]
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=len(fetched) > query.max_rows,
            estimated_cost=cost,
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )


def _map_error(exc: DBAPIError) -> Exception:
    """Map by SQLSTATE (psycopg error classes are not grouped by SQLSTATE class)."""
    state = getattr(exc.orig, "sqlstate", None) or ""
    if state == "57014":  # query_canceled (statement_timeout)
        return SqlExecutionError(ExecutionCode.TIMEOUT, "the query took too long and was cancelled")
    if state == "42501":  # insufficient_privilege
        return SqlRejectedError(
            RejectionCode.DISALLOWED_RELATION, "db_privilege", "the database denied access"
        )
    if state == "25006":  # read_only_sql_transaction
        return SqlRejectedError(RejectionCode.UNSAFE_SQL, "db_read_only", "writes are not allowed")
    if state.startswith("42"):  # syntax error or access rule violation (undefined column, ...)
        return SqlRejectedError(
            RejectionCode.INVALID_SQL, "db_rejected", "the database could not run this query"
        )
    return SqlExecutionError(ExecutionCode.EXECUTION_ERROR, "the query failed to execute")
