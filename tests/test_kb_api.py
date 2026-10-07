"""POST /knowledge/ask."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from hotel_agent.api.app import create_app
from hotel_agent.config import Settings
from hotel_agent.knowledge.answer import EvidenceThresholds, ExtractiveAnswerer
from hotel_agent.knowledge.embeddings import HashingEmbedder
from hotel_agent.knowledge.retrieval import MAX_QUERY_CHARS, Retriever
from hotel_agent.logging_config import JsonFormatter
from tests.test_kb_answer import ALWAYS_ANSWER, NEVER_ANSWER, kb_readonly  # noqa: F401

pytestmark = pytest.mark.db


def _client(settings: Settings, engine: Engine, thresholds: EvidenceThresholds) -> TestClient:
    embedder = HashingEmbedder()
    answerer = ExtractiveAnswerer(Retriever(engine, embedder), embedder, thresholds)
    return TestClient(create_app(settings, engine=engine, answerer=answerer))


@pytest.fixture
def answering_client(
    settings: Settings,
    kb_readonly: Engine,  # noqa: F811
) -> Iterator[TestClient]:
    with _client(settings, kb_readonly, ALWAYS_ANSWER) as client:
        yield client


def test_ask_returns_answer_with_citations(answering_client: TestClient) -> None:
    response = answering_client.post("/knowledge/ask", json={"question": "Is there a lifeguard?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["citations"]
    first = body["citations"][0]
    assert set(first) == {"citation", "doc_key", "title", "section_path"}
    assert f"[{first['citation']}]" in body["answer"]
    assert body["evidence"]["min_similarity"] == ALWAYS_ANSWER.min_similarity
    assert response.headers["X-Request-ID"]


def test_ask_refuses_without_evidence(settings: Settings, kb_readonly: Engine) -> None:  # noqa: F811
    with _client(settings, kb_readonly, NEVER_ANSWER) as client:
        body = client.post("/knowledge/ask", json={"question": "Is there a casino?"}).json()
    assert body["status"] == "insufficient_evidence"
    assert body["citations"] == []
    assert body["answer"].startswith("I don't know")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": "x" * (MAX_QUERY_CHARS + 1)},
        {"question": "pool", "category": "casino"},
        {"question": "pool", "mode": "telepathy"},
        {"question": 42},
    ],
)
def test_ask_validates_input(answering_client: TestClient, payload: dict[str, object]) -> None:
    assert answering_client.post("/knowledge/ask", json=payload).status_code == 422


def test_ask_accepts_category_and_mode(answering_client: TestClient) -> None:
    response = answering_client.post(
        "/knowledge/ask", json={"question": "menu prices", "category": "fnb", "mode": "fts"}
    )
    assert response.status_code == 200
    assert all(c["doc_key"].startswith("FNB-") for c in response.json()["citations"])


def test_ask_logs_fingerprint_not_question(answering_client: TestClient) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("hotel_agent.api.knowledge")
    logger.addHandler(handler)
    try:
        secretish = "guest Ms Example asked about late checkout"
        response = answering_client.post("/knowledge/ask", json={"question": secretish})
    finally:
        logger.removeHandler(handler)
    assert response.status_code == 200
    record = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert record["message"] == "knowledge question answered"
    assert len(record["question_sha256"]) == 16
    assert record["question_chars"] == len(secretish)
    assert record["status"] == "answered"
    assert secretish not in stream.getvalue()


def test_ask_unavailable_without_readonly_credentials(
    settings: Settings,
    kb_readonly: Engine,  # noqa: F811
) -> None:
    no_ro = settings.model_copy(update={"readonly_db_password": None})
    with TestClient(create_app(no_ro, engine=kb_readonly)) as client:
        response = client.post("/knowledge/ask", json={"question": "pool"})
    assert response.status_code == 503
