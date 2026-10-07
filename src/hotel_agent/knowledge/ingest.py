"""Ingest knowledge-base documents into PostgreSQL (kb_documents / kb_chunks).

Idempotent: a document whose content hash and embedding model are unchanged is
skipped (no re-embedding). Changed documents are replaced; documents no longer
present in the corpus are removed (when `prune=True`). Embeddings are computed
before the write transaction, and all database changes commit atomically.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import Engine, delete, insert, select

from hotel_agent.db.models import KbChunk, KbDocument
from hotel_agent.knowledge.chunking import DEFAULT_MAX_TOKENS, Chunk, chunk_document
from hotel_agent.knowledge.documents import KnowledgeDocument
from hotel_agent.knowledge.embeddings import Embedder

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    chunks_written: int = 0
    embed_seconds: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "added": len(self.added),
            "updated": len(self.updated),
            "unchanged": len(self.unchanged),
            "removed": len(self.removed),
            "chunks_written": self.chunks_written,
            "embed_seconds": round(self.embed_seconds, 2),
        }


@dataclass(frozen=True, slots=True)
class _Prepared:
    doc: KnowledgeDocument
    chunks: list[Chunk]
    vectors: list[list[float]]


def ingest_documents(
    engine: Engine,
    documents: list[KnowledgeDocument],
    embedder: Embedder,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prune: bool = True,
) -> IngestReport:
    report = IngestReport()
    with engine.connect() as conn:
        existing = {
            row.doc_key: (row.content_sha256, row.embedding_model)
            for row in conn.execute(
                select(KbDocument.doc_key, KbDocument.content_sha256, KbChunk.embedding_model)
                .join(KbChunk, KbChunk.document_id == KbDocument.id, isouter=True)
                .distinct()
            )
        }

    to_write: list[_Prepared] = []
    for doc in documents:
        if existing.get(doc.doc_key) == (doc.content_sha256, embedder.model_name):
            report.unchanged.append(doc.doc_key)
            continue
        chunks = chunk_document(doc, max_tokens=max_tokens)
        start = time.perf_counter()
        vectors = embedder.embed_documents([c.embedding_text() for c in chunks])
        report.embed_seconds += time.perf_counter() - start
        if any(len(v) != embedder.dimension for v in vectors):
            raise ValueError(f"{doc.doc_key}: embedding dimension mismatch")
        to_write.append(_Prepared(doc, chunks, vectors))
        (report.updated if doc.doc_key in existing else report.added).append(doc.doc_key)

    corpus_keys = {d.doc_key for d in documents}
    if prune:
        report.removed = sorted(set(existing) - corpus_keys)

    with engine.begin() as conn:
        replaced = [p.doc.doc_key for p in to_write] + report.removed
        if replaced:
            conn.execute(delete(KbDocument).where(KbDocument.doc_key.in_(replaced)))
        for prepared in to_write:
            doc = prepared.doc
            document_id = conn.execute(
                insert(KbDocument)
                .values(
                    doc_key=doc.doc_key,
                    title=doc.title,
                    category=doc.category.value,
                    version=doc.version,
                    effective_date=doc.effective_date,
                    source_path=doc.source_path,
                    content_sha256=doc.content_sha256,
                    is_synthetic=True,
                )
                .returning(KbDocument.id)
            ).scalar_one()
            conn.execute(
                insert(KbChunk),
                [
                    {
                        "document_id": document_id,
                        "chunk_index": chunk.chunk_index,
                        "section_path": chunk.section_path,
                        "content": chunk.content,
                        "token_estimate": chunk.token_estimate,
                        "content_sha256": chunk.content_sha256,
                        "embedding": vector,
                        "embedding_model": embedder.model_name,
                    }
                    for chunk, vector in zip(prepared.chunks, prepared.vectors, strict=True)
                ],
            )
            report.chunks_written += len(prepared.chunks)

    logger.info("knowledge base ingested", extra={"report": report.as_dict()})
    return report
