"""Health endpoints and request-id middleware."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.engine import URL

from hotel_agent import __version__
from hotel_agent.api.app import create_app
from hotel_agent.api.middleware import REQUEST_ID_HEADER
from hotel_agent.config import Settings
from hotel_agent.db.session import head_revision, make_engine
from tests.conftest import drop_database, recreate_database


def _client(settings: Settings, engine: Engine) -> TestClient:
    return TestClient(create_app(settings, engine=engine))


@pytest.fixture
def unreachable_engine() -> Iterator[Engine]:
    # Port 1 on localhost: nothing listens there, connection is refused immediately.
    url = URL.create("postgresql+psycopg", "nobody", "x", "127.0.0.1", 1, "nothing")
    engine = make_engine(url, connect_args={"connect_timeout": 1})
    yield engine
    engine.dispose()


def test_liveness_does_not_need_database(settings: Settings, unreachable_engine: Engine) -> None:
    with _client(settings, unreachable_engine) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_readiness_reports_unavailable_database(
    settings: Settings, unreachable_engine: Engine
) -> None:
    with _client(settings, unreachable_engine) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["database"] == "unavailable"


@pytest.mark.db
def test_readiness_ok_when_migrated(settings: Settings, migrated_engine: Engine) -> None:
    with _client(settings, migrated_engine) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "schema_revision": head_revision(),
        "expected_revision": head_revision(),
    }


@pytest.mark.db
def test_readiness_not_ready_when_unmigrated(settings: Settings, admin_engine: Engine) -> None:
    name = f"{settings.test_postgres_db}_empty"
    recreate_database(admin_engine, name)
    engine = make_engine(settings.database_url(name))
    try:
        with _client(settings, engine) as client:
            response = client.get("/health/ready")
    finally:
        engine.dispose()
        drop_database(admin_engine, name)
    assert response.status_code == 503
    body = response.json()
    assert body["database"] == "ok"
    assert body["schema_revision"] is None


def test_request_id_is_generated(settings: Settings, unreachable_engine: Engine) -> None:
    with _client(settings, unreachable_engine) as client:
        first = client.get("/health").headers[REQUEST_ID_HEADER]
        second = client.get("/health").headers[REQUEST_ID_HEADER]
    assert len(first) == 32
    assert first != second


def test_valid_request_id_is_propagated(settings: Settings, unreachable_engine: Engine) -> None:
    with _client(settings, unreachable_engine) as client:
        response = client.get("/health", headers={REQUEST_ID_HEADER: "abc-123.x_y"})
    assert response.headers[REQUEST_ID_HEADER] == "abc-123.x_y"


@pytest.mark.parametrize("bad", ["has spaces", "x" * 65, "line\tbreak", "<script>"])
def test_unsafe_request_id_is_replaced(
    settings: Settings, unreachable_engine: Engine, bad: str
) -> None:
    with _client(settings, unreachable_engine) as client:
        response = client.get("/health", headers={REQUEST_ID_HEADER: bad})
    assert response.headers[REQUEST_ID_HEADER] != bad
    assert len(response.headers[REQUEST_ID_HEADER]) == 32
