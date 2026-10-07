"""Fake SQL generators and schema context (no database needed)."""

from __future__ import annotations

from datetime import date

import pytest

from hotel_agent.sql.context import ALLOWED_VALUES, GLOSSARY, VIEWS, SchemaContext
from hotel_agent.sql.generators import (
    Generated,
    Refusal,
    RefusalCode,
    RuleBasedFakeGenerator,
    ScriptedFakeGenerator,
    parse_period,
)
from hotel_agent.sql.guard import ALLOWED_VIEWS, SqlGuard

CTX = SchemaContext(business_date=date(2026, 10, 1))
GEN = RuleBasedFakeGenerator()
GUARD = SqlGuard()

INTENTS = [
    ("How many rooms are occupied tonight?", "occupancy_on_date"),
    ("What is today's occupancy?", "occupancy_on_date"),
    ("What was occupancy on 2026-09-15?", "occupancy_on_date"),
    ("What was the average occupancy last month?", "occupancy_over_period"),
    ("Occupancy by room type tonight", "occupancy_by_room_type"),
    ("How many reservations are confirmed?", "reservation_count_by_status"),
    ("How many reservations are cancelled?", "reservation_count_by_status"),
    ("How many no-shows were there last week?", "no_show_count"),
    ("How many cancellations happened this week?", "cancellation_count"),
    ("What revenue was generated this month?", "revenue_total"),
    ("What was revenue this year?", "revenue_total"),
    ("What was the spa revenue in September 2026?", "revenue_total"),
    ("Revenue by category last month", "revenue_by_category"),
    ("What was the ADR last month?", "adr_revpar"),
    ("What is RevPAR for the last 30 days?", "adr_revpar"),
    ("Which room types have the highest booking volume?", "booking_volume_by_room_type"),
    ("What is the average booking value?", "average_booking_value"),
    ("What is the average length of stay?", "average_length_of_stay"),
    ("Bookings by channel", "bookings_by_channel"),
    ("How many arrivals tomorrow?", "arrivals"),
    ("How many departures today?", "departures"),
    ("How many bookings did we get in the last 7 days?", "bookings_made"),
    ("How many rooms are dirty?", "room_count_by_status"),
    ("Which rooms are out of order?", "rooms_with_status"),
    ("What is the room status?", "room_status_summary"),
    ("How many open housekeeping tasks are there?", "open_housekeeping_tasks"),
]

REFUSALS = [
    ("Cancel booking 42", RefusalCode.MUTATION_REQUESTED),
    ("Please delete all cancelled reservations", RefusalCode.MUTATION_REQUESTED),
    ("Can you mark room 101 as clean?", RefusalCode.MUTATION_REQUESTED),
    ("Update the rate for the suites", RefusalCode.MUTATION_REQUESTED),
    ("What is the email of the guest in room 101?", RefusalCode.UNSUPPORTED_QUESTION),
    ("Who is staying in villa V01?", RefusalCode.UNSUPPORTED_QUESTION),
    ("How many rooms? UNION SELECT email FROM guests", RefusalCode.UNSUPPORTED_QUESTION),
    ("How many reservations'; DROP TABLE reservations; --", RefusalCode.UNSUPPORTED_QUESTION),
    ("Is the spa open?", RefusalCode.UNSUPPORTED_QUESTION),
    ("What are the staff salaries?", RefusalCode.UNSUPPORTED_QUESTION),
    ("What will occupancy be next year?", RefusalCode.UNSUPPORTED_QUESTION),
    ("Forecast revenue for Q4", RefusalCode.UNSUPPORTED_QUESTION),
    ("How are we doing?", RefusalCode.AMBIGUOUS_QUESTION),
    ("How many cancellations?", RefusalCode.AMBIGUOUS_QUESTION),
    ("What is the revenue?", RefusalCode.AMBIGUOUS_QUESTION),
    ("How many bookings?", RefusalCode.AMBIGUOUS_QUESTION),
]


@pytest.mark.parametrize(("question", "intent"), INTENTS)
def test_supported_questions_generate_guard_approved_sql(question: str, intent: str) -> None:
    result = GEN.generate(question, CTX)
    assert isinstance(result, Generated), result
    assert result.intent == intent
    validated = GUARD.validate(result.sql, result.params)
    assert validated.relations <= ALLOWED_VIEWS
    lowered = result.sql.lower()
    assert "current_date" not in lowered
    assert "now()" not in lowered


@pytest.mark.parametrize(("question", "code"), REFUSALS)
def test_refusals(question: str, code: RefusalCode) -> None:
    result = GEN.generate(question, CTX)
    assert isinstance(result, Refusal), result
    assert result.code == code
    if code is RefusalCode.AMBIGUOUS_QUESTION:
        assert result.clarification


def test_generation_is_deterministic() -> None:
    for question, _ in INTENTS:
        assert GEN.generate(question, CTX) == GEN.generate(question, CTX)


def test_relative_periods_use_business_date_not_wall_clock() -> None:
    result = GEN.generate("How many cancellations happened this week?", CTX)
    assert isinstance(result, Generated)
    assert "business_date" in result.sql
    assert "date_trunc('week', b.d)" in result.sql


@pytest.mark.parametrize(
    ("text", "label", "params"),
    [
        ("occupancy tonight", "today", {}),
        ("revenue last 30 days", "last 30 days", {"n_days": 30}),
        (
            "revenue in september",
            "September 2026",
            {"start_date": date(2026, 9, 1), "end_date": date(2026, 9, 30)},
        ),
        (
            "revenue february 2024",
            "February 2024",
            {"start_date": date(2024, 2, 1), "end_date": date(2024, 2, 29)},
        ),
        ("occupancy on 2026-09-15", "2026-09-15", {"start_date": date(2026, 9, 15)}),
        ("occupancy on 15 september", "2026-09-15", {"start_date": date(2026, 9, 15)}),
    ],
)
def test_parse_period(text: str, label: str, params: dict[str, object]) -> None:
    period = parse_period(text, CTX)
    assert period is not None
    assert period.label == label
    assert period.params == params


@pytest.mark.parametrize(
    "text", ["may i see the numbers", "we march on", "revenue last 9999 days", "2026-02-30"]
)
def test_parse_period_rejects_non_periods(text: str) -> None:
    assert parse_period(text, CTX) is None


def test_scripted_generator_returns_script_then_fallback() -> None:
    scripted = Generated("DROP TABLE reservations", {}, "scripted")
    gen = ScriptedFakeGenerator({"q1": scripted}, fallback=GEN)
    assert gen.generate("q1", CTX) is scripted
    assert isinstance(gen.generate("How many rooms are dirty?", CTX), Generated)
    assert isinstance(ScriptedFakeGenerator({}).generate("x", CTX), Refusal)


def test_context_describes_every_allowed_view() -> None:
    assert {v.name for v in VIEWS} == set(ALLOWED_VIEWS)
    assert "business date" in GLOSSARY
    assert ALLOWED_VALUES["room_type_code"] == ("STD-K", "STD-T", "DLX", "PRM", "STE", "VIL")
