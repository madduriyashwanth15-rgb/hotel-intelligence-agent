"""Retrieval (vector, full-text, hybrid RRF) as the read-only role."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from hotel_agent.config import Settings
from hotel_agent.db.session import make_engine
from hotel_agent.knowledge.documents import load_corpus
from hotel_agent.knowledge.embeddings import HashingEmbedder
from hotel_agent.knowledge.ingest import ingest_documents
from hotel_agent.knowledge.retrieval import (
    MAX_QUERY_CHARS,
    QueryValidationError,
    RetrievalMode,
    Retriever,
    or_tsquery_text,
    reciprocal_rank_fusion,
)
from tests.conftest import clear_knowledge_base
from tests.test_kb_documents import KB_DIR

# --- Pure functions ---


def test_rrf_rewards_items_ranked_well_in_both_lists() -> None:
    scores = reciprocal_rank_fusion([[1, 2, 3], [3, 1, 4]], k=60)
    assert scores[1] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)
    assert scores[4] == pytest.approx(1 / 63)
    assert max(scores, key=lambda i: scores[i]) == 1


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What time is check-out?", "what | time | is | check | out"),
        ("'; DROP TABLE kb_chunks; --", "drop | table | kb | chunks"),
        ("₹ !!! ???", None),
        ("late late LATE", "late"),
    ],
)
def test_or_tsquery_text_is_safe(query: str, expected: str | None) -> None:
    assert or_tsquery_text(query) == expected


# --- Against the database ---

pytestmark_db = pytest.mark.db


@pytest.fixture(scope="module")
def retriever(migrated_engine: Engine, settings: Settings) -> Iterator[Retriever]:
    clear_knowledge_base(migrated_engine)
    ingest_documents(migrated_engine, load_corpus(KB_DIR), HashingEmbedder())
    if settings.readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")
    ro = make_engine(settings.readonly_database_url(settings.test_postgres_db))
    yield Retriever(ro, HashingEmbedder())
    ro.dispose()
    clear_knowledge_base(migrated_engine)


@pytestmark_db
def test_vector_search_orders_by_similarity(retriever: Retriever) -> None:
    hits = retriever.search("swimming pool lifeguard children", top_k=5, mode=RetrievalMode.VECTOR)
    assert len(hits) == 5
    sims = [h.vector_similarity for h in hits]
    assert all(s is not None for s in sims)
    assert sims == sorted(sims, reverse=True)  # type: ignore[type-var]
    assert [h.vector_rank for h in hits] == [1, 2, 3, 4, 5]


@pytestmark_db
def test_fts_finds_exact_terms(retriever: Retriever) -> None:
    hits = retriever.search("lifeguard", top_k=3, mode=RetrievalMode.FTS)
    assert hits[0].doc_key == "POL-CONDUCT-001"
    assert "lifeguard" in hits[0].content
    assert hits[0].fts_rank == 1


@pytestmark_db
def test_fts_returns_nothing_for_unknown_terms(retriever: Retriever) -> None:
    assert retriever.search("zzzqqq xylophone", top_k=3, mode=RetrievalMode.FTS) == []


@pytestmark_db
def test_hybrid_fuses_both_rankings(retriever: Retriever) -> None:
    hits = retriever.search("Code Red fire assembly point", top_k=5)
    assert hits[0].doc_key == "EMG-FIRE-001"
    assert all(h.rrf_score is not None for h in hits)
    scores = [h.rrf_score for h in hits]
    assert scores == sorted(scores, reverse=True)  # type: ignore[type-var]
    assert len({h.chunk_id for h in hits}) == len(hits)
    assert hits[0].citation.startswith("EMG-FIRE-001#")


@pytestmark_db
def test_category_filter(retriever: Retriever) -> None:
    hits = retriever.search("menu prices", top_k=5, category="fnb")
    assert hits
    assert {h.category for h in hits} == {"fnb"}
    with pytest.raises(QueryValidationError, match="unknown category"):
        retriever.search("menu", category="casino")


@pytest.mark.parametrize("bad", ["", "   ", "x" * (MAX_QUERY_CHARS + 1)])
@pytestmark_db
def test_query_validation(retriever: Retriever, bad: str) -> None:
    with pytest.raises(QueryValidationError):
        retriever.search(bad)


@pytestmark_db
def test_top_k_bounds(retriever: Retriever) -> None:
    with pytest.raises(QueryValidationError, match="top_k"):
        retriever.search("pool", top_k=0)
    with pytest.raises(QueryValidationError, match="top_k"):
        retriever.search("pool", top_k=21)


@pytestmark_db
def test_injection_like_query_is_just_text(retriever: Retriever, migrated_engine: Engine) -> None:
    retriever.search("'; DROP TABLE kb_chunks; -- \\ $$ %s :param", top_k=3)
    with migrated_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM kb_chunks")).scalar_one() > 0
