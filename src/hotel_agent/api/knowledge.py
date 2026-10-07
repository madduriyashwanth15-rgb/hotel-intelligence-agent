"""POST /knowledge/ask — grounded, extractive answers from the hotel knowledge base."""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, StringConstraints

from hotel_agent.db.models import KB_CATEGORIES
from hotel_agent.knowledge.answer import ExtractiveAnswerer
from hotel_agent.knowledge.embeddings import ModelIntegrityError, ModelNotAvailableError
from hotel_agent.knowledge.retrieval import MAX_QUERY_CHARS, QueryValidationError, RetrievalMode

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
logger = logging.getLogger(__name__)

Category = Literal[KB_CATEGORIES]  # type: ignore[valid-type]


class AskRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUERY_CHARS)
    ]
    mode: RetrievalMode = RetrievalMode.HYBRID
    category: Category | None = None


class CitationOut(BaseModel):
    citation: str = Field(description="DOC-KEY#chunk_index")
    doc_key: str
    title: str
    section_path: str


class EvidenceOut(BaseModel):
    top_similarity: float
    coverage: float
    min_similarity: float
    min_coverage: float


class AskResponse(BaseModel):
    status: Literal["answered", "insufficient_evidence"]
    answer: str
    citations: list[CitationOut]
    evidence: EvidenceOut
    latency_ms: float


@router.post("/ask", responses={503: {"description": "Knowledge base not available"}})
def ask(body: AskRequest, request: Request) -> AskResponse:
    answerer: ExtractiveAnswerer | None = request.app.state.answerer
    if answerer is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "knowledge base not configured")
    try:
        result = answerer.answer(body.question, mode=body.mode, category=body.category)
    except QueryValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except (ModelNotAvailableError, ModelIntegrityError) as exc:
        logger.error("embedding model unavailable", extra={"error": str(exc)})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "embedding model unavailable"
        ) from None

    # Log a fingerprint, not the question text, so future real queries don't leak into logs.
    logger.info(
        "knowledge question answered",
        extra={
            "question_sha256": hashlib.sha256(body.question.encode()).hexdigest()[:16],
            "question_chars": len(body.question),
            "mode": body.mode.value,
            "status": result.status.value,
            "citations": [c.citation for c in result.citations],
            "top_similarity": round(result.top_similarity, 4),
            "coverage": round(result.coverage, 4),
            "latency_ms": round(result.latency_ms, 1),
        },
    )
    return AskResponse(
        status=result.status.value,
        answer=result.answer,
        citations=[
            CitationOut(
                citation=c.citation, doc_key=c.doc_key, title=c.title, section_path=c.section_path
            )
            for c in result.citations
        ],
        evidence=EvidenceOut(
            top_similarity=round(result.top_similarity, 4),
            coverage=round(result.coverage, 4),
            min_similarity=result.thresholds.min_similarity,
            min_coverage=result.thresholds.min_coverage,
        ),
        latency_ms=round(result.latency_ms, 1),
    )
