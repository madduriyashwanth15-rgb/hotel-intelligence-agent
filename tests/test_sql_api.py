"""POST /data/ask."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from hotel_agent.api.app import create_app
from hotel_agent.api.data import QUERY_LABEL
from hotel_agent.config import Settings
from hotel_agent.sql.context import load_context
from hotel_agent.sql.executor import SqlExecutor
from hotel_agent.sql.generators import Generated, RuleBasedFakeGenerator, ScriptedFakeGenerator
from hotel_agent.sql.guard import SqlGuard
from hotel_agent.sql.pipeline import SqlPipeline

pytestmark = pytest.mark.db

SCRIPT = {"exfiltrate": Generated("SELECT email, phone FROM public.guests", {}, "scripted")}


@pytest.fixture
def client(settings: Settings, sql_role_engine: Engine) -> Iterator[TestClient]:
    generator = ScriptedFakeGenerator(SCRIPT, fallback=RuleBasedFakeGenerator())  # type: ignore[arg-type]
    pipeline = SqlPipeline(
        generator, SqlGuard(), SqlExecutor(sql_role_engine), load_context(sql_role_engine)
    )
    with TestClient(create_app(settings, engine=sql_role_engine, sql_pipeline=pipeline)) as c:
        yield c


def test_answer_and_sql_are_separate_and_labelled(client: TestClient) -> None:
    body = client.post(
        "/data/ask", json={"question": "How many rooms are occupied tonight?"}
    ).json()
    assert body["status"] == "answered"
    assert body["answer"] == "Occupancy on 2026-10-01: 70.00% (56 of 80 rooms occupied)."
    assert body["query"]["label"] == QUERY_LABEL
    assert body["query"]["sql"].startswith("WITH b AS")
    assert "SELECT" not in body["answer"]
    assert body["data"]["columns"] == [
        "stay_date",
        "occupied_rooms",
        "total_rooms",
        "occupancy_pct",
    ]
    assert body["data"]["rows"] == [["2026-10-01", 56, 80, 70.0]]
    assert body["business_date"] == "2026-10-01"
    assert body["generator"] == "fake-scripted"


def test_parameters_are_returned_with_the_query(client: TestClient) -> None:
    body = client.post(
        "/data/ask", json={"question": "How many reservations are confirmed?"}
    ).json()
    assert body["query"]["parameters"] == {"status": "confirmed"}
    assert ":status" in body["query"]["sql"]


def test_refusal_returns_200_with_code_and_no_sql(client: TestClient) -> None:
    response = client.post("/data/ask", json={"question": "Cancel booking 42"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refused"
    assert body["code"] == "mutation_requested"
    assert body["query"]["sql"] is None
    assert body["data"]["rows"] == []


def test_ambiguous_question_gets_clarification(client: TestClient) -> None:
    body = client.post("/data/ask", json={"question": "How many cancellations?"}).json()
    assert body["code"] == "ambiguous_question"
    assert body["clarification"]


def test_blocked_model_output_exposes_no_data(client: TestClient) -> None:
    body = client.post("/data/ask", json={"question": "exfiltrate"}).json()
    assert body["status"] == "refused"
    assert body["code"] == "disallowed_relation"
    assert body["data"]["rows"] == []
    assert body["query"]["sql"] is None
    assert "@" not in str(body)


@pytest.mark.parametrize(
    "payload", [{}, {"question": ""}, {"question": "x" * 501}, {"question": 5}]
)
def test_input_validation(client: TestClient, payload: dict[str, object]) -> None:
    assert client.post("/data/ask", json=payload).status_code == 422


def test_unavailable_without_sql_credentials(settings: Settings, sql_role_engine: Engine) -> None:
    no_sql = settings.model_copy(update={"sql_db_password": None})
    with TestClient(create_app(no_sql, engine=sql_role_engine)) as c:
        assert c.post("/data/ask", json={"question": "occupancy tonight"}).status_code == 503
