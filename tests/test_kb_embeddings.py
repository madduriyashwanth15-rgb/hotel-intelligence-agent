"""Embedders: the deterministic test embedder and the real local model."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from hotel_agent.config import Settings
from hotel_agent.knowledge.chunking import chunk_document, estimate_tokens
from hotel_agent.knowledge.documents import load_corpus
from hotel_agent.knowledge.embeddings import (
    EMBEDDING_DIM,
    PINNED_MODELS,
    FastEmbedEmbedder,
    HashingEmbedder,
    ModelIntegrityError,
    ModelNotAvailableError,
    model_file_path,
    verify_model_file,
)
from tests.test_kb_documents import KB_DIR

SPEC = PINNED_MODELS["BAAI/bge-small-en-v1.5"]


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# --- HashingEmbedder (used by the rest of the test suite) ---


def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    e = HashingEmbedder()
    a, b = e.embed_documents(["late check-out policy", "late check-out policy"])
    assert a == b
    assert len(a) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0)


def test_hashing_embedder_rewards_word_overlap() -> None:
    e = HashingEmbedder()
    q = e.embed_query("late check-out charge")
    near, far = e.embed_documents(["Late check-out charge is 1500.", "The pool opens at 07:00."])
    assert _cos(q, near) > _cos(q, far)


# --- Model file verification (no model needed) ---


def test_missing_model_gives_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(ModelNotAvailableError, match="hotel-kb download-model"):
        verify_model_file(SPEC, tmp_path)


def test_tampered_model_file_is_rejected(tmp_path: Path) -> None:
    path = model_file_path(SPEC, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not the real model")
    with pytest.raises(ModelIntegrityError, match="refusing to load"):
        verify_model_file(SPEC, tmp_path)


def test_unpinned_model_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not pinned"):
        FastEmbedEmbedder("some/other-model", tmp_path)


# --- Real model (skipped unless downloaded; never downloads) ---


@pytest.fixture(scope="module")
def real_embedder(settings: Settings) -> FastEmbedEmbedder:
    cache = settings.resolve_path(settings.embedding_cache_dir)
    if not model_file_path(SPEC, cache).is_file():
        pytest.skip("embedding model not downloaded (run `uv run hotel-kb download-model`)")
    return FastEmbedEmbedder(settings.embedding_model, cache, threads=settings.embedding_threads)


@pytest.mark.model
def test_real_model_dimensions_and_normalisation(real_embedder: FastEmbedEmbedder) -> None:
    v1, v2 = real_embedder.embed_documents(["Check-in time is 14:00.", "Check-in time is 14:00."])
    assert len(v1) == EMBEDDING_DIM == real_embedder.dimension
    assert math.isclose(math.sqrt(sum(x * x for x in v1)), 1.0, rel_tol=1e-3)
    assert v1 == pytest.approx(v2)


@pytest.mark.model
def test_real_model_is_semantic(real_embedder: FastEmbedEmbedder) -> None:
    q = real_embedder.embed_query("Can I get my money back if I cancel?")
    refund, pool = real_embedder.embed_documents(
        [
            "Approved refunds are returned to the original payment method within 7 days.",
            "The main pool is open from 07:00 to 20:00.",
        ]
    )
    assert _cos(q, refund) > _cos(q, pool) + 0.1


@pytest.mark.model
def test_chunk_token_estimates_are_conservative(real_embedder: FastEmbedEmbedder) -> None:
    """The chunker's estimate must never undercount the real tokenizer (512-token limit)."""
    for doc in load_corpus(KB_DIR):
        for chunk in chunk_document(doc):
            text = chunk.embedding_text()
            real = real_embedder.count_tokens(text)
            assert real <= 512, (doc.doc_key, chunk.chunk_index, real)
            assert estimate_tokens(text) >= real, (doc.doc_key, chunk.chunk_index, real)
