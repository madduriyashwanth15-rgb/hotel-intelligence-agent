"""SQL generators behind an interface (ADR-0016). No LLM is used in Phase 3.

- RuleBasedFakeGenerator: deterministic intent rules for a realistic subset of
  operational questions. Relative periods are computed in SQL from
  analytics.business_date (never CURRENT_DATE / now()); explicit dates are bound
  parameters. Mutation requests, personal-data requests and questions containing SQL
  syntax are refused before any SQL is produced.
- ScriptedFakeGenerator: evaluation only. Emits a given SQL string for a question so the
  rest of the pipeline can be tested against a compromised or hallucinating "model".

Whatever a generator returns is untrusted: the SqlGuard and the hotel_sql role decide.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Protocol

from hotel_agent.sql.context import SchemaContext
from hotel_agent.sql.guard import ParamValue


class RefusalCode(StrEnum):
    UNSUPPORTED_QUESTION = "unsupported_question"
    AMBIGUOUS_QUESTION = "ambiguous_question"
    MUTATION_REQUESTED = "mutation_requested"


@dataclass(frozen=True, slots=True)
class Generated:
    sql: str
    params: dict[str, ParamValue] = field(default_factory=dict)
    intent: str = "unknown"
    period_label: str | None = None


@dataclass(frozen=True, slots=True)
class Refusal:
    code: RefusalCode
    message: str
    clarification: str | None = None


class SqlGenerator(Protocol):
    @property
    def name(self) -> str: ...

    def generate(self, question: str, context: SchemaContext) -> Generated | Refusal: ...


# --------------------------------------------------------------------------- periods

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}
_MONTH_RE = "|".join(_MONTHS)
B_CTE = "WITH b AS (SELECT business_date AS d FROM business_date) "


@dataclass(frozen=True, slots=True)
class Period:
    label: str
    start: str  # SQL expression (may reference b.d or :start_date)
    end: str
    params: dict[str, ParamValue] = field(default_factory=dict)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return start, date.fromordinal(nxt.toordinal() - 1)


def parse_period(q: str, ctx: SchemaContext) -> Period | None:
    """Recognise a time period in a normalised question; None if no period is mentioned."""
    week_start = "date_trunc('week', b.d)::date"
    month_start = "date_trunc('month', b.d)::date"
    if re.search(r"\b(today|tonight|right now|currently|current)\b", q):
        return Period("today", "b.d", "b.d")
    if re.search(r"\byesterday|last night\b", q):
        return Period("yesterday", "b.d - 1", "b.d - 1")
    if re.search(r"\btomorrow\b", q):
        return Period("tomorrow", "b.d + 1", "b.d + 1")
    if m := re.search(r"\b(?:last|past|previous)\s+(\d{1,3})\s+days?\b", q):
        n = int(m.group(1))
        if not 1 <= n <= 366:
            return None
        return Period(f"last {n} days", "b.d - CAST(:n_days AS INT) + 1", "b.d", {"n_days": n})
    if m := re.search(r"\bnext\s+(\d{1,3})\s+days?\b", q):
        n = int(m.group(1))
        if not 1 <= n <= 366:
            return None
        return Period(f"next {n} days", "b.d + 1", "b.d + CAST(:n_days AS INT)", {"n_days": n})
    if re.search(r"\bthis week\b", q):
        return Period("this week", week_start, "b.d")
    if re.search(r"\b(last|previous) week\b", q):
        return Period("last week", f"{week_start} - 7", f"{week_start} - 1")
    if re.search(r"\bnext week\b", q):
        return Period("next week", f"{week_start} + 7", f"{week_start} + 13")
    if re.search(r"\b(this month|month to date|mtd)\b", q):
        return Period("this month", month_start, "b.d")
    if re.search(r"\b(last|previous) month\b", q):
        return Period(
            "last month",
            "(date_trunc('month', b.d) - interval '1 month')::date",
            f"{month_start} - 1",
        )
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", q):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        return Period(d.isoformat(), ":start_date", ":start_date", {"start_date": d})
    if m := re.search(rf"\b(\d{{1,2}})\s+({_MONTH_RE})(?:\s+(\d{{4}}))?\b", q):  # 15 september
        try:
            d = date(
                int(m.group(3) or ctx.business_date.year), _MONTHS[m.group(2)], int(m.group(1))
            )
        except ValueError:
            return None
        return Period(d.isoformat(), ":start_date", ":start_date", {"start_date": d})
    # A bare month name needs "in/for/during/of" before it or a year after it ("may", "march"
    # are also verbs).
    m = re.search(rf"\b(?:in|for|during|of)\s+({_MONTH_RE})(?:\s+(\d{{4}}))?\b", q) or re.search(
        rf"\b({_MONTH_RE})\s+(\d{{4}})\b", q
    )
    if m:
        year = int(m.group(2) or ctx.business_date.year)
        start, end = _month_bounds(year, _MONTHS[m.group(1)])
        return Period(
            f"{m.group(1).title()} {year}", ":start_date", ":end_date",
            {"start_date": start, "end_date": end},
        )  # fmt: skip
    return None


# --------------------------------------------------------------------------- refusals

_SQL_SYNTAX = re.compile(
    r"(;|--|/\*|\*/|\bselect\b.+\bfrom\b|\bunion\b|\bdrop\s+table\b|\binsert\s+into\b|"
    r"\bdelete\s+from\b|\bupdate\b.+\bset\b|\btruncate\b|\balter\s+table\b|\bpg_\w+|"
    r"\binformation_schema\b|\bexec(ute)?\b|\bgrant\b|\bxp_)"
)
_MUTATION = re.compile(
    r"^(please\s+|can you\s+|could you\s+|i want to\s+|i need to\s+|go ahead and\s+)?"
    r"(cancel|delete|remove|drop|update|change|modify|insert|create|add|set|mark|assign|"
    r"truncate|alter|book|reserve|move|upgrade|refund|reset|clear|wipe|check (in|out)|"
    r"increase|decrease|edit|rename|close|reopen|approve)\b"
)
_PII = re.compile(
    r"\b(name|names|email|e-mail|phone|mobile|contact|address|passport|id card|"
    r"who is|who's|whose|guest list|personal|card number|credit card)\b"
)
_VAGUE = re.compile(
    r"^(how are we doing|how is business|show me (the )?(numbers|data|everything)|"
    r"give me (a|the) report|report|stats|statistics|summary|dashboard|kpis?)\b"
)


def _normalise(question: str) -> str:
    q = question.lower().replace("\u2019", "'")
    q = re.sub(r"\bno[- ]shows?\b", "noshow", q)
    q = re.sub(r"\bchecked[- ]in\b", "checked_in", q)
    q = re.sub(r"\bchecked[- ]out\b", "checked_out", q)
    q = re.sub(r"\bin[- ]house\b", "checked_in", q)
    q = re.sub(r"\bout[- ]of[- ]order\b", "out_of_order", q)
    q = re.sub(r"\bf\s*&\s*b\b|\bfood and beverage\b", "food_beverage", q)
    return " ".join(q.split())


def _ambiguous_period(metric: str) -> Refusal:
    return Refusal(
        RefusalCode.AMBIGUOUS_QUESTION,
        f"The question asks for {metric} but does not say for which period.",
        "Add a period, for example 'this week', 'last month', 'September 2026' or "
        "'the last 30 days'.",
    )


# --------------------------------------------------------------------------- intents

Handler = Callable[[str, SchemaContext], Generated | Refusal | None]

_STATUS_WORDS = {
    "confirmed": "confirmed",
    "checked_in": "checked_in",
    "checked_out": "checked_out",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "noshow": "no_show",
}
_HK_WORDS = {
    "dirty": "dirty",
    "clean": "clean",
    "inspected": "inspected",
    "out_of_order": "out_of_order",
}
_CATEGORY_WORDS = {
    "room revenue": "room", "food_beverage": "food_beverage", "spa": "spa",
    "minibar": "minibar", "laundry": "laundry",
}  # fmt: skip


def _occupancy(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not re.search(r"\boccupan|\boccupied\b|\bhow full\b", q):
        return None
    if re.search(r"\bby room type|per room type|each room type\b", q):
        period = parse_period(q, ctx) or Period("today", "b.d", "b.d")
        if period.start != period.end:
            return None
        sql = (
            B_CTE + "SELECT rt.room_type_code, rt.room_type_name, rt.room_count, "
            "count(r.reservation_id) AS occupied_rooms "
            "FROM room_types rt CROSS JOIN b LEFT JOIN reservations r "
            "ON r.room_type_code = rt.room_type_code AND r.is_active "
            f"AND r.check_in_date <= {period.start} AND r.check_out_date > {period.start} "
            "GROUP BY rt.room_type_code, rt.room_type_name, rt.room_count "
            "ORDER BY rt.room_type_code"
        )
        return Generated(sql, period.params, "occupancy_by_room_type", period.label)
    period = parse_period(q, ctx) or Period("today", "b.d", "b.d")
    if period.start == period.end:
        sql = (
            B_CTE + "SELECT o.stay_date, o.occupied_rooms, o.total_rooms, o.occupancy_pct "
            f"FROM daily_occupancy o CROSS JOIN b WHERE o.stay_date = {period.start}"
        )
        return Generated(sql, period.params, "occupancy_on_date", period.label)
    sql = (
        B_CTE + "SELECT min(o.stay_date) AS from_date, max(o.stay_date) AS to_date, "
        "round(avg(o.occupancy_pct), 2) AS avg_occupancy_pct, "
        "sum(o.occupied_rooms) AS occupied_room_nights "
        "FROM daily_occupancy o CROSS JOIN b "
        f"WHERE o.stay_date BETWEEN {period.start} AND {period.end}"
    )
    return Generated(sql, period.params, "occupancy_over_period", period.label)


def _revenue(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not re.search(r"\brevenue|\bincome|\bsales\b|\bearn(ed|ings)?\b|\bmoney\b", q):
        return None
    if re.search(r"\badr\b|average daily rate|revpar", q):
        return None
    period = parse_period(q, ctx)
    if period is None:
        return _ambiguous_period("revenue")
    params = dict(period.params)
    where = f"r.charge_date BETWEEN {period.start} AND {period.end}"
    category = next((c for word, c in _CATEGORY_WORDS.items() if word in q), None)
    latest = "(SELECT max(charge_date) FROM daily_revenue) AS latest_posted_date"
    if re.search(r"\bby category|per category|breakdown|split\b", q):
        sql = (
            B_CTE + f"SELECT r.category, sum(r.amount) AS revenue, {latest} "
            f"FROM daily_revenue r CROSS JOIN b WHERE {where} "
            "GROUP BY r.category ORDER BY revenue DESC"
        )
        return Generated(sql, params, "revenue_by_category", period.label)
    if category is not None:
        where += " AND r.category = :category"
        params["category"] = category
    sql = (
        B_CTE + f"SELECT coalesce(sum(r.amount), 0) AS revenue, {latest} "
        f"FROM daily_revenue r CROSS JOIN b WHERE {where}"
    )
    return Generated(sql, params, "revenue_total", period.label)


def _adr_revpar(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not re.search(r"\badr\b|average daily rate|\brevpar\b", q):
        return None
    period = parse_period(q, ctx)
    if period is None:
        return _ambiguous_period("ADR/RevPAR")
    sql = (
        "WITH b AS (SELECT business_date AS d FROM business_date), "
        "rev AS (SELECT coalesce(sum(r.amount), 0) AS room_revenue, "
        "coalesce(sum(r.charge_count), 0) AS sold_room_nights FROM daily_revenue r CROSS JOIN b "
        f"WHERE r.category = 'room' AND r.charge_date BETWEEN {period.start} AND {period.end}), "
        "cap AS (SELECT coalesce(sum(o.total_rooms), 0) AS available_room_nights "
        "FROM daily_occupancy o CROSS JOIN b "
        f"WHERE o.stay_date BETWEEN {period.start} AND {period.end}) "
        "SELECT round(rev.room_revenue / NULLIF(rev.sold_room_nights, 0), 2) AS adr, "
        "round(rev.room_revenue / NULLIF(cap.available_room_nights, 0), 2) AS revpar, "
        "rev.room_revenue, (SELECT max(charge_date) FROM daily_revenue) AS latest_posted_date "
        "FROM rev CROSS JOIN cap"
    )
    return Generated(sql, period.params, "adr_revpar", period.label)


def _cancellations(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not re.search(r"\bcancell?ations?\b|\bcancell?ed\b|\bnoshow\b", q):
        return None
    noshow = "noshow" in q
    period = parse_period(q, ctx)
    if period is None:
        return _ambiguous_period("no-shows" if noshow else "cancellations")
    if noshow:
        sql = (
            B_CTE + "SELECT count(*) AS no_shows FROM reservations r CROSS JOIN b "
            "WHERE r.status = 'no_show' "
            f"AND r.check_in_date BETWEEN {period.start} AND {period.end}"
        )
        return Generated(sql, period.params, "no_show_count", period.label)
    sql = (
        B_CTE + "SELECT count(*) AS cancellations FROM reservations r CROSS JOIN b "
        f"WHERE r.status = 'cancelled' AND r.cancelled_date BETWEEN {period.start} AND {period.end}"
    )
    return Generated(sql, period.params, "cancellation_count", period.label)


def _arrivals_departures(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    m = re.search(r"\b(arrivals?|arriving|check-?ins?|departures?|departing|check-?outs?)\b", q)
    if not m:
        return None
    departing = m.group(1).startswith(
        ("depart", "check-out", "checkout", "check-outs", "checkouts")
    )
    period = parse_period(q, ctx) or Period("today", "b.d", "b.d")
    column = "check_out_date" if departing else "check_in_date"
    sql = (
        B_CTE + f"SELECT count(*) AS {'departures' if departing else 'arrivals'} "
        f"FROM reservations r CROSS JOIN b WHERE r.is_active AND r.{column} "
        f"BETWEEN {period.start} AND {period.end}"
    )
    return Generated(sql, period.params, "departures" if departing else "arrivals", period.label)


def _room_type_volume(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not (
        re.search(r"\broom types?\b", q)
        and re.search(r"\b(book|reserv|volume|popular|most|highest)", q)
    ):
        return None
    period = parse_period(q, ctx)
    where = "r.status <> 'cancelled'"
    params: dict[str, ParamValue] = {}
    label = "all reservations"
    prefix = ""
    join_b = ""
    if period is not None:
        prefix, join_b = B_CTE, " CROSS JOIN b"
        where += f" AND r.check_in_date BETWEEN {period.start} AND {period.end}"
        params, label = dict(period.params), period.label
    sql = (
        prefix + "SELECT rt.room_type_code, rt.room_type_name, count(r.reservation_id) AS bookings "
        f"FROM reservations r JOIN room_types rt ON rt.room_type_code = r.room_type_code{join_b} "
        f"WHERE {where} GROUP BY rt.room_type_code, rt.room_type_name ORDER BY bookings DESC, "
        "rt.room_type_code"
    )
    return Generated(sql, params, "booking_volume_by_room_type", label)


def _avg_booking_value(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not re.search(r"\baverage (booking|reservation) (value|amount|size)\b|\bavg booking\b", q):
        return None
    period = parse_period(q, ctx)
    where = "r.status NOT IN ('cancelled', 'no_show')"
    params: dict[str, ParamValue] = {}
    prefix, join_b, label = "", "", "all reservations"
    if period is not None:
        prefix, join_b = B_CTE, " CROSS JOIN b"
        where += f" AND r.check_in_date BETWEEN {period.start} AND {period.end}"
        params, label = dict(period.params), period.label
    sql = (
        prefix + "SELECT round(avg(r.total_amount), 2) AS average_booking_value, "
        f"count(*) AS reservations FROM reservations r{join_b} WHERE {where}"
    )
    return Generated(sql, params, "average_booking_value", label)


def _length_of_stay(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not re.search(r"\b(average|avg|mean) (length of stay|stay length|nights)\b|\balos\b", q):
        return None
    sql = (
        "SELECT round(avg(r.nights), 2) AS average_nights FROM reservations r "
        "WHERE r.status NOT IN ('cancelled', 'no_show')"
    )
    return Generated(sql, {}, "average_length_of_stay", "all reservations")


def _channels(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not re.search(r"\bchannels?\b|\bota\b|\bdirect bookings?\b", q):
        return None
    sql = (
        "SELECT r.channel, count(*) AS bookings FROM reservations r "
        "WHERE r.status <> 'cancelled' GROUP BY r.channel ORDER BY bookings DESC, r.channel"
    )
    return Generated(sql, {}, "bookings_by_channel", "all reservations")


def _status_counts(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not re.search(r"\b(reservations?|bookings?|stays?|guests?)\b", q):
        return None
    status = next((s for word, s in _STATUS_WORDS.items() if re.search(rf"\b{word}\b", q)), None)
    period = parse_period(q, ctx)
    if (
        status is None
        and period is not None
        and re.search(r"\b(made|received|taken|new|get|got)\b", q)
    ):
        sql = (
            B_CTE + "SELECT count(*) AS reservations_made FROM reservations r CROSS JOIN b "
            f"WHERE r.booked_date BETWEEN {period.start} AND {period.end}"
        )
        return Generated(sql, period.params, "bookings_made", period.label)
    if status is None:
        if re.search(r"\bhow many\b", q) and period is None:
            return Refusal(
                RefusalCode.AMBIGUOUS_QUESTION,
                "The question does not say which reservations to count.",
                "Say a status (confirmed, checked in, checked out, cancelled, no-show) or ask "
                "about arrivals, departures or a period.",
            )
        return None
    sql = "SELECT count(*) AS reservations FROM reservations r WHERE r.status = :status"
    return Generated(sql, {"status": status}, "reservation_count_by_status", status)


def _room_status(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not re.search(r"\brooms?\b", q):
        return None
    hk = next((s for word, s in _HK_WORDS.items() if re.search(rf"\b{word}\b", q)), None)
    if hk is not None:
        if re.search(r"\b(which|list)\b", q):
            sql = (
                "SELECT room_number, room_type_code FROM rooms "
                "WHERE housekeeping_status = :hk ORDER BY room_number"
            )
            return Generated(sql, {"hk": hk}, "rooms_with_status", hk)
        sql = "SELECT count(*) AS rooms FROM rooms WHERE housekeeping_status = :hk"
        return Generated(sql, {"hk": hk}, "room_count_by_status", hk)
    if re.search(r"\b(room status|housekeeping status|status of (the )?rooms)\b", q):
        sql = (
            "SELECT housekeeping_status, count(*) AS rooms FROM rooms "
            "GROUP BY housekeeping_status ORDER BY housekeeping_status"
        )
        return Generated(sql, {}, "room_status_summary", "today")
    if re.search(r"\bhow many rooms\b", q) and re.search(r"\b(total|have|are there)\b", q):
        return Generated("SELECT count(*) AS rooms FROM rooms", {}, "room_count", "total")
    return None


def _housekeeping(q: str, ctx: SchemaContext) -> Generated | Refusal | None:
    if not re.search(r"\b(housekeeping|tasks?|cleaning)\b", q):
        return None
    if re.search(r"\b(open|pending|outstanding|in progress|to do)\b", q):
        sql = (
            "SELECT task_type, count(*) AS open_tasks FROM housekeeping_tasks "
            "WHERE status IN ('open', 'in_progress') GROUP BY task_type ORDER BY task_type"
        )
        return Generated(sql, {}, "open_housekeeping_tasks", "now")
    return None


HANDLERS: tuple[Handler, ...] = (
    _adr_revpar, _occupancy, _revenue, _cancellations, _avg_booking_value,
    _length_of_stay, _room_type_volume, _arrivals_departures, _channels,
    _housekeeping, _room_status, _status_counts,
)  # fmt: skip


class RuleBasedFakeGenerator:
    """Deterministic, rule-based stand-in for an LLM (Phase 3 only)."""

    @property
    def name(self) -> str:
        return "fake-rules-v1"

    def generate(self, question: str, context: SchemaContext) -> Generated | Refusal:
        q = _normalise(question)
        if _SQL_SYNTAX.search(q):
            return Refusal(
                RefusalCode.UNSUPPORTED_QUESTION,
                "Please ask in plain language; questions containing SQL are not accepted.",
            )
        if _MUTATION.search(q):
            return Refusal(
                RefusalCode.MUTATION_REQUESTED,
                "This assistant can only read data. It cannot cancel, create, change or "
                "delete anything.",
            )
        if _PII.search(q):
            return Refusal(
                RefusalCode.UNSUPPORTED_QUESTION,
                "Guest identities and contact details are not available for analytics.",
            )
        if _VAGUE.search(q):
            return Refusal(
                RefusalCode.AMBIGUOUS_QUESTION,
                "The question is too broad to answer with one query.",
                "Ask about one metric, e.g. occupancy tonight, revenue last month or "
                "cancellations this week.",
            )
        for handler in HANDLERS:
            result = handler(q, context)
            if result is not None:
                return result
        return Refusal(
            RefusalCode.UNSUPPORTED_QUESTION,
            "This question is not supported by the operational data assistant.",
            "Supported topics: occupancy, arrivals/departures, reservations by status or "
            "channel, cancellations, no-shows, revenue, ADR/RevPAR, room status, "
            "housekeeping tasks.",
        )


class ScriptedFakeGenerator:
    """Evaluation-only generator that returns pre-scripted outputs per question."""

    def __init__(
        self, script: dict[str, Generated | Refusal], fallback: SqlGenerator | None = None
    ) -> None:
        self._script = script
        self._fallback = fallback

    @property
    def name(self) -> str:
        return "fake-scripted"

    def generate(self, question: str, context: SchemaContext) -> Generated | Refusal:
        if question in self._script:
            return self._script[question]
        if self._fallback is not None:
            return self._fallback.generate(question, context)
        return Refusal(RefusalCode.UNSUPPORTED_QUESTION, "no scripted output for this question")
