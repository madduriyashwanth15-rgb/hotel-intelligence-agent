"""SqlGuard: validate untrusted, generated SQL before it reaches the database (ADR-0015).

Security posture: this module treats its input as hostile. It is an ALLOWLIST:
every node of the parsed syntax tree must belong to a small set of known-safe node
types, every relation must be an approved analytics view (or a CTE defined in the same
query), every function must be explicitly allowed, and casts may only target plain
data types. Anything unrecognised is rejected. The database role (ADR-0014) is a second,
independent layer; this guard must never be weakened on the assumption that the
database will catch something.

Output: canonical SQL re-rendered from the validated tree (comments dropped, outer LIMIT
enforced, placeholders as `:name`) — the executor runs exactly this text.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

MAX_SQL_CHARS = 4_000
MAX_AST_NODES = 600
MAX_JOINS = 6
MAX_SUBQUERY_DEPTH = 4
MAX_OFFSET = 1_000
MAX_STRING_PARAM_CHARS = 64

ALLOWED_VIEWS = frozenset(
    {
        "business_date",
        "room_types",
        "rooms",
        "reservations",
        "daily_occupancy",
        "daily_revenue",
        "housekeeping_tasks",
    }
)
ANALYTICS_SCHEMA = "analytics"

# Structural / operator nodes that are safe in a read-only query.
_STRUCTURAL_NODES: tuple[type[exp.Expression], ...] = (
    exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery, exp.With, exp.CTE,
    exp.From, exp.Join, exp.Where, exp.Group, exp.Having, exp.Order, exp.Ordered,
    exp.Limit, exp.Offset, exp.Distinct, exp.Table, exp.TableAlias, exp.Alias,
    exp.Column, exp.Identifier, exp.Star, exp.Literal, exp.Boolean, exp.Null, exp.Paren,
    exp.Placeholder, exp.Tuple, exp.Var, exp.Interval, exp.DataType, exp.DataTypeParam,
    exp.And, exp.Or, exp.Not, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
    exp.Is, exp.In, exp.Between, exp.Like, exp.ILike, exp.Exists,
    exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Neg,
    exp.Case, exp.If, exp.Filter,
)  # fmt: skip

# Functions (sqlglot Func subclasses) that are allowed. No anonymous functions at all.
_ALLOWED_FUNCTIONS: tuple[type[exp.Expression], ...] = (
    exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max,
    exp.Round, exp.Coalesce, exp.Nullif, exp.Greatest, exp.Least, exp.Abs,
    exp.Lower, exp.Upper, exp.Extract, exp.TimestampTrunc, exp.DateTrunc, exp.Cast,
)  # fmt: skip

_UNIT_PARENTS: tuple[type[exp.Expression], ...] = (
    exp.Interval, exp.Extract, exp.TimestampTrunc, exp.DateTrunc,
)  # fmt: skip

_WALL_CLOCK: tuple[type[exp.Expression], ...] = (
    exp.CurrentDate, exp.CurrentTimestamp, exp.CurrentTime, exp.CurrentDatetime,
)  # fmt: skip

_ALLOWED_CAST_TYPES = frozenset(
    {
        exp.DataType.Type.INT, exp.DataType.Type.BIGINT, exp.DataType.Type.SMALLINT,
        exp.DataType.Type.DECIMAL, exp.DataType.Type.DOUBLE, exp.DataType.Type.FLOAT,
        exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR, exp.DataType.Type.DATE,
        exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ,
        exp.DataType.Type.BOOLEAN, exp.DataType.Type.INTERVAL,
    }
)  # fmt: skip

_PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SAFE_STRING = re.compile(r"^[A-Za-z0-9 _\-.]*$")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

ParamValue = bool | int | float | Decimal | date | str


class RejectionCode(StrEnum):
    INVALID_SQL = "invalid_sql"
    UNSAFE_SQL = "unsafe_sql"
    DISALLOWED_RELATION = "disallowed_relation"
    DISALLOWED_FUNCTION = "disallowed_function"
    INVALID_PARAMETERS = "invalid_parameters"
    TOO_EXPENSIVE = "too_expensive"


class SqlRejectedError(Exception):
    def __init__(self, code: RejectionCode, rule: str, message: str) -> None:
        super().__init__(f"{code}: {rule}: {message}")
        self.code = code
        self.rule = rule
        self.message = message


@dataclass(frozen=True, slots=True)
class ValidatedQuery:
    sql: str  # canonical SQL to execute (placeholders as :name)
    params: dict[str, ParamValue]
    relations: frozenset[str]
    max_rows: int


def _reject(code: RejectionCode, rule: str, message: str) -> SqlRejectedError:
    return SqlRejectedError(code, rule, message)


class SqlGuard:
    def __init__(self, max_rows: int = 100, allowed_views: frozenset[str] = ALLOWED_VIEWS) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        self.max_rows = max_rows
        self.allowed_views = allowed_views

    # --- public API ---

    def validate(self, sql: str, params: dict[str, ParamValue] | None = None) -> ValidatedQuery:
        params = dict(params or {})
        tree = self._parse(sql)
        self._check_nodes(tree)
        relations = self._check_relations(tree)
        self._check_complexity(tree)
        self._check_parameters(tree, params)
        tree = self._enforce_limit(tree)
        return ValidatedQuery(
            sql=self._render(tree), params=params, relations=relations, max_rows=self.max_rows
        )

    # --- steps ---

    @staticmethod
    def _parse(sql: str) -> exp.Expression:
        if not isinstance(sql, str) or not sql.strip():
            raise _reject(RejectionCode.INVALID_SQL, "empty", "no SQL was generated")
        if len(sql) > MAX_SQL_CHARS:
            raise _reject(
                RejectionCode.INVALID_SQL, "too_long", f"SQL exceeds {MAX_SQL_CHARS} characters"
            )
        if _CONTROL_CHARS.search(sql):
            raise _reject(
                RejectionCode.INVALID_SQL, "control_chars", "SQL contains control characters"
            )
        try:
            statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
        except (ParseError, TokenError) as exc:
            raise _reject(
                RejectionCode.INVALID_SQL, "parse_error", str(exc).splitlines()[0][:200]
            ) from None
        if len(statements) != 1:
            raise _reject(
                RejectionCode.UNSAFE_SQL,
                "single_statement",
                f"expected exactly 1 statement, got {len(statements)}",
            )
        tree = statements[0]
        if not isinstance(tree, exp.Select | exp.Union | exp.Intersect | exp.Except):
            raise _reject(
                RejectionCode.UNSAFE_SQL,
                "select_only",
                f"only SELECT queries are allowed, got {type(tree).__name__}",
            )
        return tree

    @staticmethod
    def _check_nodes(tree: exp.Expression) -> None:
        for node in tree.walk():
            if isinstance(node, _WALL_CLOCK):
                raise _reject(
                    RejectionCode.DISALLOWED_FUNCTION, "wall_clock",
                    "CURRENT_DATE/now() are not allowed; use analytics.business_date",
                )  # fmt: skip
            if isinstance(node, exp.Select) and not node.expressions:
                raise _reject(RejectionCode.INVALID_SQL, "empty_select", "SELECT without columns")
            if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
                raise _reject(
                    RejectionCode.DISALLOWED_FUNCTION, "function_allowlist",
                    f"schema-qualified function {node.sql()!r} is not allowed",
                )  # fmt: skip
            if type(node) in _ALLOWED_FUNCTIONS:  # exact type: subclasses are not implied
                if isinstance(node, exp.Cast):
                    _check_cast(node)
                continue
            if type(node) is exp.Var and not isinstance(node.parent, _UNIT_PARENTS):
                # Var renders back as raw text; only allow it as a date/interval unit.
                raise _reject(
                    RejectionCode.UNSAFE_SQL, "var_position", f"unexpected token {node.name!r}"
                )
            if type(node) in _STRUCTURAL_NODES:
                if isinstance(node, exp.With) and node.args.get("recursive"):
                    raise _reject(
                        RejectionCode.UNSAFE_SQL,
                        "no_recursive_cte",
                        "recursive CTEs are not allowed",
                    )
                if isinstance(node, exp.Select) and node.args.get("into") is not None:
                    raise _reject(
                        RejectionCode.UNSAFE_SQL, "no_select_into", "SELECT INTO is not allowed"
                    )
                if isinstance(node, exp.Select) and node.args.get("locks"):
                    raise _reject(
                        RejectionCode.UNSAFE_SQL, "no_locking", "locking clauses are not allowed"
                    )
                continue
            if isinstance(node, exp.Func):
                name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
                raise _reject(
                    RejectionCode.DISALLOWED_FUNCTION,
                    "function_allowlist",
                    f"function {name!r} is not allowed",
                )
            raise _reject(
                RejectionCode.UNSAFE_SQL, "node_allowlist", f"{type(node).__name__} is not allowed"
            )

    def _check_relations(self, tree: exp.Expression) -> frozenset[str]:
        cte_names = {
            _pg_name(cte.args["alias"].this) if cte.args.get("alias") else cte.alias_or_name
            for cte in tree.find_all(exp.CTE)
        }
        used: set[str] = set()
        for table in tree.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                raise _reject(
                    RejectionCode.DISALLOWED_RELATION,
                    "no_table_functions",
                    "table functions are not allowed in FROM",
                )
            # PostgreSQL folds unquoted identifiers to lower case; quoted ones are exact.
            name = _pg_name(table.this)
            schema = _pg_name(table.args["db"]) if table.args.get("db") else ""
            catalog = table.catalog
            if catalog:
                raise _reject(
                    RejectionCode.DISALLOWED_RELATION,
                    "no_catalog",
                    f"catalog-qualified relation {table.sql()!r}",
                )
            if not schema and name in cte_names:
                continue
            if schema and schema != ANALYTICS_SCHEMA:
                raise _reject(
                    RejectionCode.DISALLOWED_RELATION,
                    "schema_allowlist",
                    f"schema {schema!r} is not allowed",
                )
            if name not in self.allowed_views:
                raise _reject(
                    RejectionCode.DISALLOWED_RELATION,
                    "view_allowlist",
                    f"relation {name!r} is not an approved analytics view",
                )
            used.add(name)
        return frozenset(used)

    @staticmethod
    def _check_complexity(tree: exp.Expression) -> None:
        nodes = sum(1 for _ in tree.walk())
        if nodes > MAX_AST_NODES:
            raise _reject(
                RejectionCode.TOO_EXPENSIVE, "max_nodes", f"query too complex ({nodes} nodes)"
            )
        joins = sum(1 for _ in tree.find_all(exp.Join))
        if joins > MAX_JOINS:
            raise _reject(RejectionCode.TOO_EXPENSIVE, "max_joins", f"too many joins ({joins})")
        for sub in tree.find_all(exp.Subquery, exp.Select):
            depth = len([a for a in _ancestors(sub) if isinstance(a, exp.Subquery | exp.CTE)])
            if depth > MAX_SUBQUERY_DEPTH:
                raise _reject(
                    RejectionCode.TOO_EXPENSIVE, "max_depth", "subqueries nested too deeply"
                )
        for join in tree.find_all(exp.Join):
            if (
                not join.args.get("on")
                and not join.args.get("using")
                and (join.args.get("kind") or "").upper() != "CROSS"
            ):
                # Implicit cartesian products via comma joins are handled by the cost check;
                # an explicit JOIN without ON is almost always a mistake.
                raise _reject(
                    RejectionCode.TOO_EXPENSIVE, "join_condition", "JOIN without ON/USING"
                )

    @staticmethod
    def _check_parameters(tree: exp.Expression, params: dict[str, ParamValue]) -> None:
        names: set[str] = set()
        for ph in tree.find_all(exp.Placeholder):
            name = ph.name
            if not name or not _PARAM_NAME.match(name):
                raise _reject(
                    RejectionCode.INVALID_PARAMETERS,
                    "named_placeholders",
                    "only named placeholders like :name are allowed",
                )
            names.add(name)
        if names != set(params):
            raise _reject(
                RejectionCode.INVALID_PARAMETERS, "params_match",
                f"placeholders {sorted(names)} do not match parameters {sorted(params)}",
            )  # fmt: skip
        for name, value in params.items():
            _check_param_value(name, value)

    def _enforce_limit(self, tree: exp.Expression) -> exp.Expression:
        cap = self.max_rows + 1  # one extra row reveals truncation
        if not isinstance(tree, exp.Select):
            wrapped = exp.select("*").from_(
                exp.Subquery(this=tree, alias=exp.TableAlias(this=exp.to_identifier("q")))
            )
            wrapped.set("limit", exp.Limit(expression=exp.Literal.number(cap)))
            return wrapped
        limit = tree.args.get("limit")
        if limit is not None:
            value = limit.expression
            if not (
                isinstance(value, exp.Literal) and not value.is_string and value.this.isdigit()
            ):
                raise _reject(
                    RejectionCode.UNSAFE_SQL,
                    "literal_limit",
                    "LIMIT must be a non-negative integer literal",
                )
            limit.set("expression", exp.Literal.number(min(int(value.this), cap)))
        else:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(cap)))
        offset = tree.args.get("offset")
        if offset is not None:
            value = offset.expression
            if not (
                isinstance(value, exp.Literal) and not value.is_string and value.this.isdigit()
            ):
                raise _reject(
                    RejectionCode.UNSAFE_SQL,
                    "literal_offset",
                    "OFFSET must be a non-negative integer literal",
                )
            if int(value.this) > MAX_OFFSET:
                raise _reject(
                    RejectionCode.TOO_EXPENSIVE, "max_offset", f"OFFSET above {MAX_OFFSET}"
                )
        return tree

    @staticmethod
    def _render(tree: exp.Expression) -> str:
        def to_named(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Placeholder):
                return exp.Var(this=f":{node.name}")
            return node

        return tree.transform(to_named).sql(dialect="postgres", comments=False)


def _pg_name(identifier: exp.Expression) -> str:
    if isinstance(identifier, exp.Identifier):
        raw = str(identifier.this)
        return raw if identifier.quoted else raw.lower()
    return str(identifier.name)


def _ancestors(node: object) -> list[object]:
    out: list[object] = []
    parent = getattr(node, "parent", None)
    while parent is not None:
        out.append(parent)
        parent = getattr(parent, "parent", None)
    return out


def _check_cast(cast: exp.Cast) -> None:
    target = cast.args.get("to")
    if not isinstance(target, exp.DataType) or target.this not in _ALLOWED_CAST_TYPES:
        raise _reject(
            RejectionCode.DISALLOWED_FUNCTION,
            "cast_types",
            f"cast to {target.sql() if target else '?'} is not allowed",
        )


def _check_param_value(name: str, value: object) -> None:
    if isinstance(value, bool | date):
        return
    if isinstance(value, int):
        if abs(value) > 10**9:
            raise _reject(RejectionCode.INVALID_PARAMETERS, "int_range", f"{name} out of range")
        return
    if isinstance(value, float | Decimal):
        if not math.isfinite(float(value)):
            raise _reject(RejectionCode.INVALID_PARAMETERS, "finite", f"{name} must be finite")
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_PARAM_CHARS or not _SAFE_STRING.match(value):
            raise _reject(
                RejectionCode.INVALID_PARAMETERS,
                "string_shape",
                f"{name} has an invalid string value",
            )
        return
    raise _reject(
        RejectionCode.INVALID_PARAMETERS,
        "param_type",
        f"{name} has unsupported type {type(value).__name__}",
    )
