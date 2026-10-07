"""Liveness and readiness endpoints."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from hotel_agent import __version__
from hotel_agent.db.session import current_revision, head_revision

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


class LivenessResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["ok", "unavailable"]
    schema_revision: str | None
    expected_revision: str | None


@router.get("/health")
def liveness() -> LivenessResponse:
    """The process is up. Does not touch dependencies."""
    return LivenessResponse(status="ok", version=__version__)


@router.get("/health/ready", responses={503: {"model": ReadinessResponse}})
def readiness(request: Request, response: Response) -> ReadinessResponse:
    """Ready to serve: database reachable and migrated to the latest revision."""
    engine: Engine = request.app.state.engine
    expected = head_revision()
    try:
        current = current_revision(engine)
    except SQLAlchemyError as exc:
        logger.warning("readiness check failed: database unavailable", extra={"error": str(exc)})
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="not_ready",
            database="unavailable",
            schema_revision=None,
            expected_revision=expected,
        )

    ready = current is not None and current == expected
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        database="ok",
        schema_revision=current,
        expected_revision=expected,
    )
