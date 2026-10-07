"""Ingestion into kb_documents / kb_chunks (uses the deterministic test embedder)."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence

import pytest
from psycopg import errors as pg_errors
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError

from hotel_agent.config import Settings
from hotel_agent.db.models import KbChunk, KbDocument
from hotel_agent.db.session import make_engine
from hotel_agent.knowledge.chunking import chunk_document
from hotel_agent.knowledge.documents import KnowledgeDocument, load_corpus
from hotel_agent.knowledge.embeddings import HashingEmbedder
from hotel_agent.knowledge.ingest import ingest_documents
from tests.conftest import clear_knowledge_base
from tests.test_kb_documents import KB_DIR

pytestmark = pytest.mark.db


@pytest.fixture
def kb(migrated_engine: Engine) -> Iterator[Engine]:
    clear_knowledge_base(migrated_engine)
    yield migrated_engine
    clear_knowledge_base(migrated_engine)


@pytest.fixture(scope="module")
def corpus() -> list[KnowledgeDocument]:
    return load_corpus(KB_DIR)


def _counts(engine: Engine) -> tuple[int, int]:
    with engine.connect() as conn:
        docs = conn.execute(select(func.count()).select_from(KbDocument)).scalar_one()
        chunks = conn.execute(select(func.count()).select_from(KbChunk)).scalar_one()
    return docs, chunks


class CountingEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += len(texts)
        return super().embed_documents(texts)


def test_first_ingest_stores_every_document_and_chunk(
    kb: Engine, corpus: list[KnowledgeDocument]
) -> None:
    report = ingest_documents(kb, corpus, HashingEmbedder())
    expected_chunks = sum(len(chunk_document(d)) for d in corpus)
    assert len(report.added) == len(corpus)
    assert report.chunks_written == expected_chunks
    assert _counts(kb) == (len(corpus), expected_chunks)


def test_reingest_is_idempotent_and_skips_embedding(
    kb: Engine, corpus: list[KnowledgeDocument]
) -> None:
    ingest_documents(kb, corpus, HashingEmbedder())
    before = _counts(kb)
    embedder = CountingEmbedder()
    report = ingest_documents(kb, corpus, embedder)
    assert report.as_dict()["unchanged"] == len(corpus)
    assert report.chunks_written == 0
    assert embedder.calls == 0
    assert _counts(kb) == before


def test_changed_document_is_replaced(kb: Engine, corpus: list[KnowledgeDocument]) -> None:
    ingest_documents(kb, corpus, HashingEmbedder())
    original = corpus[0]
    changed = dataclasses.replace(
        original,
        body=original.body + "\n\n## New Section\n\nA brand new rule.",
        content_sha256="f" * 64,
    )
    report = ingest_documents(kb, [changed, *corpus[1:]], HashingEmbedder())
    assert report.updated == [original.doc_key]
    assert len(report.unchanged) == len(corpus) - 1
    with kb.connect() as conn:
        paths = (
            conn.execute(
                select(KbChunk.section_path)
                .join(KbDocument)
                .where(KbDocument.doc_key == original.doc_key)
                .order_by(KbChunk.chunk_index)
            )
            .scalars()
            .all()
        )
    assert paths[-1].endswith("New Section")
    assert len(paths) == len(chunk_document(changed))


def test_removed_document_is_pruned(kb: Engine, corpus: list[KnowledgeDocument]) -> None:
    ingest_documents(kb, corpus, HashingEmbedder())
    report = ingest_documents(kb, corpus[1:], HashingEmbedder())
    assert report.removed == [corpus[0].doc_key]
    assert _counts(kb)[0] == len(corpus) - 1
    kept = ingest_documents(kb, corpus[2:], HashingEmbedder(), prune=False)
    assert kept.removed == []


def test_embedding_model_change_forces_reembedding(
    kb: Engine, corpus: list[KnowledgeDocument]
) -> None:
    ingest_documents(kb, corpus[:2], HashingEmbedder())

    class OtherModel(HashingEmbedder):
        @property
        def model_name(self) -> str:
            return "test/other-model"

    report = ingest_documents(kb, corpus[:2], OtherModel())
    assert len(report.updated) == 2


def test_dimension_mismatch_writes_nothing(kb: Engine, corpus: list[KnowledgeDocument]) -> None:
    class BadEmbedder(HashingEmbedder):
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            return [v[:10] for v in super().embed_documents(texts)]

    with pytest.raises(ValueError, match="dimension mismatch"):
        ingest_documents(kb, corpus, BadEmbedder())
    assert _counts(kb) == (0, 0)


def test_database_rejects_non_synthetic_documents(kb: Engine) -> None:
    with kb.connect() as conn, pytest.raises(IntegrityError) as excinfo:
        conn.execute(
            text(
                "INSERT INTO kb_documents (doc_key, title, category, version, effective_date,"
                " source_path, content_sha256, is_synthetic)"
                " VALUES ('REAL-1', 'x', 'policy', '1', '2026-01-01', 'x', 'x', false)"
            )
        )
    assert isinstance(excinfo.value.orig, pg_errors.CheckViolation)


def test_search_tsv_is_generated(kb: Engine, corpus: list[KnowledgeDocument]) -> None:
    ingest_documents(kb, corpus, HashingEmbedder())
    with kb.connect() as conn:
        hits = conn.execute(
            text(
                "SELECT count(*) FROM kb_chunks"
                " WHERE search_tsv @@ to_tsquery('english', 'lifeguard')"
            )
        ).scalar_one()
    assert hits >= 1


def test_readonly_role_can_read_but_not_write_kb(
    kb: Engine, settings: Settings, corpus: list[KnowledgeDocument]
) -> None:
    ingest_documents(kb, corpus[:1], HashingEmbedder())
    if settings.readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")
    ro = make_engine(settings.readonly_database_url(settings.test_postgres_db))
    try:
        with ro.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM kb_chunks")).scalar_one() > 0
            with pytest.raises(Exception, match=r"read-only|permission denied"):
                conn.execute(text("DELETE FROM kb_documents"))
    finally:
        ro.dispose()
