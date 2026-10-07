"""Hybrid retrieval over kb_chunks (ADR-0011).

- vector: cosine similarity with pgvector (exact scan; small corpus)
- fts: PostgreSQL full-text search; query words are OR-ed so natural-language
  questions still match, and `ts_rank_cd` rewards chunks covering more words
- hybrid: Reciprocal Rank Fusion of both rankings

Retrieval should run on an engine connected as the read-only role (ADR-0005).
All SQL uses bound parameters; the query text never becomes SQL syntax.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from sqlalchemy import Engine, Select, Text, cast, func, literal, select, text
from sqlalchemy.dialects.postgresql import REGCONFIG

from hotel_agent.db.models import KB_CATEGORIES, KbChunk, KbDocument
from hotel_agent.knowledge.embeddings import Embedder

MAX_QUERY_CHARS = 500
MAX_TOP_K = 20
DEFAULT_CANDIDATES = 20
RRF_K = 60
_MAX_QUERY_TERMS = 32
_TERM = re.compile(r"[a-z0-9]+")


class RetrievalMode(StrEnum):
    VECTOR = "vector"
    FTS = "fts"
    HYBRID = "hybrid"


class QueryValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: int
    doc_key: str
    title: str
    category: str
    chunk_index: int
    section_path: str
    content: str
    vector_similarity: float | None = None
    vector_rank: int | None = None
    fts_score: float | None = None
    fts_rank: int | None = None
    rrf_score: float | None = None
    # Stemmed lexemes of the chunk (from search_tsv), used for evidence coverage checks.
    lexemes: frozenset[str] = frozenset()

    @property
    def citation(self) -> str:
        return f"{self.doc_key}#{self.chunk_index}"


def validate_query(query: str) -> str:
    cleaned = " ".join(query.split())
    if not cleaned:
        raise QueryValidationError("query must not be empty")
    if len(cleaned) > MAX_QUERY_CHARS:
        raise QueryValidationError(f"query must be at most {MAX_QUERY_CHARS} characters")
    return cleaned


def or_tsquery_text(query: str) -> str | None:
    """Turn free text into a safe `to_tsquery` input: alphanumeric words joined by `|`."""
    terms = list(dict.fromkeys(_TERM.findall(query.lower())))[:_MAX_QUERY_TERMS]
    return " | ".join(terms) if terms else None


def reciprocal_rank_fusion(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> dict[int, float]:
    """RRF: score(id) = sum over rankings of 1 / (k + rank), ranks starting at 1."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


class Retriever:
    def __init__(
        self,
        engine: Engine,
        embedder: Embedder,
        *,
        candidates: int = DEFAULT_CANDIDATES,
        rrf_k: int = RRF_K,
    ) -> None:
        self._engine = engine
        self._embedder = embedder
        self._candidates = candidates
        self._rrf_k = rrf_k
        # (chunk count, max chunk id) -> idf; the signature detects re-ingestion.
        self._idf_cache: tuple[tuple[int, int | None], dict[str, float]] | None = None

    @staticmethod
    def _base(
        category: str | None,
    ) -> Select[Any]:
        # Explicit columns: never load the 384-dim vectors back into Python.
        stmt = select(
            KbChunk.id,
            KbDocument.doc_key,
            KbDocument.title,
            KbDocument.category,
            KbChunk.chunk_index,
            KbChunk.section_path,
            KbChunk.content,
            func.tsvector_to_array(KbChunk.search_tsv),
        ).join(KbDocument, KbChunk.document_id == KbDocument.id)
        if category is not None:
            if category not in KB_CATEGORIES:
                raise QueryValidationError(f"unknown category {category!r}")
            stmt = stmt.where(KbDocument.category == category)
        return stmt

    @staticmethod
    def _to_result(
        row: Sequence[Any],
    ) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=row[0],
            doc_key=row[1],
            title=row[2],
            category=row[3],
            chunk_index=row[4],
            section_path=row[5],
            content=row[6],
            lexemes=frozenset(row[7]),
        )

    def vector_search(
        self, query: str, limit: int, category: str | None = None
    ) -> list[RetrievedChunk]:
        vector = self._embedder.embed_query(validate_query(query))
        distance = KbChunk.embedding.cosine_distance(vector)
        stmt = (
            self._base(category).add_columns(distance).order_by(distance, KbChunk.id).limit(limit)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            replace(self._to_result(row), vector_similarity=1.0 - float(row[8]), vector_rank=rank)
            for rank, row in enumerate(rows, start=1)
        ]

    def vector_similarities(self, query: str, chunk_ids: Sequence[int]) -> dict[int, float]:
        """Cosine similarity between the query and specific chunks (any retrieval mode)."""
        if not chunk_ids:
            return {}
        vector = self._embedder.embed_query(validate_query(query))
        distance = KbChunk.embedding.cosine_distance(vector)
        stmt = select(KbChunk.id, distance).where(KbChunk.id.in_(list(chunk_ids)))
        with self._engine.connect() as conn:
            return {int(row[0]): 1.0 - float(row[1]) for row in conn.execute(stmt).all()}

    def fts_search(
        self, query: str, limit: int, category: str | None = None
    ) -> list[RetrievedChunk]:
        ts_text = or_tsquery_text(validate_query(query))
        if ts_text is None:
            return []
        tsquery = func.to_tsquery(cast(literal("english"), REGCONFIG), cast(literal(ts_text), Text))
        rank = func.ts_rank_cd(KbChunk.search_tsv, tsquery)
        stmt = (
            self._base(category)
            .add_columns(rank)
            .where(KbChunk.search_tsv.op("@@")(tsquery))
            .order_by(rank.desc(), KbChunk.id)
            .limit(limit)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            replace(self._to_result(row), fts_score=float(row[8]), fts_rank=position)
            for position, row in enumerate(rows, start=1)
        ]

    def term_idf(self) -> dict[str, float]:
        """Inverse chunk frequency of every stem in the knowledge base.

        Computed with `ts_stat` and cached until the chunk table changes
        (count / max id signature), so re-ingestion is picked up automatically.
        """
        with self._engine.connect() as conn:
            count, max_id = conn.execute(select(func.count(KbChunk.id), func.max(KbChunk.id))).one()
            signature = (int(count), max_id)
            if self._idf_cache is not None and self._idf_cache[0] == signature:
                return self._idf_cache[1]
            # Constant SQL: no user input reaches this statement.
            stats = conn.execute(
                text("SELECT word, ndoc FROM ts_stat('SELECT search_tsv FROM kb_chunks')")
            ).all()
        total = signature[0]
        idf = {str(row[0]): math.log((total + 1) / (int(row[1]) + 0.5)) for row in stats}
        self._idf_cache = (signature, idf)
        return idf

    def unseen_term_idf(self) -> float:
        """IDF assigned to stems that never occur in the knowledge base."""
        total = self._idf_cache[0][0] if self._idf_cache else 0
        return math.log((total + 1) / 0.5)

    def query_lexemes(self, query: str) -> frozenset[str]:
        """Content-word stems of the query, as PostgreSQL's english config sees them."""
        stmt = select(
            func.tsvector_to_array(
                func.to_tsvector(cast(literal("english"), REGCONFIG), cast(literal(query), Text))
            )
        )
        with self._engine.connect() as conn:
            return frozenset(conn.execute(stmt).scalar_one())

    def search(
        self,
        query: str,
        top_k: int = 5,
        mode: RetrievalMode = RetrievalMode.HYBRID,
        category: str | None = None,
    ) -> list[RetrievedChunk]:
        if not 1 <= top_k <= MAX_TOP_K:
            raise QueryValidationError(f"top_k must be between 1 and {MAX_TOP_K}")
        if mode is RetrievalMode.VECTOR:
            return self.vector_search(query, top_k, category)
        if mode is RetrievalMode.FTS:
            return self.fts_search(query, top_k, category)

        vector_hits = self.vector_search(query, self._candidates, category)
        fts_hits = self.fts_search(query, self._candidates, category)
        fused = reciprocal_rank_fusion(
            [[h.chunk_id for h in vector_hits], [h.chunk_id for h in fts_hits]], k=self._rrf_k
        )
        by_id: dict[int, RetrievedChunk] = {h.chunk_id: h for h in vector_hits}
        for hit in fts_hits:
            existing = by_id.get(hit.chunk_id)
            by_id[hit.chunk_id] = (
                replace(existing, fts_score=hit.fts_score, fts_rank=hit.fts_rank)
                if existing
                else hit
            )
        ordered = sorted(fused, key=lambda cid: (-fused[cid], cid))[:top_k]
        return [replace(by_id[cid], rrf_score=fused[cid]) for cid in ordered]
