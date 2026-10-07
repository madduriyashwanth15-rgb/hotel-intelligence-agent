"""Heading-aware chunking."""

from __future__ import annotations

from datetime import date
from itertools import pairwise

import pytest

from hotel_agent.knowledge.chunking import chunk_document, estimate_tokens
from hotel_agent.knowledge.documents import DocumentCategory, KnowledgeDocument, load_corpus
from tests.test_kb_documents import KB_DIR


def _doc(body: str) -> KnowledgeDocument:
    return KnowledgeDocument(
        doc_key="T-1",
        title="Policy",
        category=DocumentCategory.POLICY,
        version="1",
        effective_date=date(2026, 1, 1),
        body=body,
        source_path="t.md",
        content_sha256="0" * 64,
    )


def test_sections_become_chunks_with_paths() -> None:
    body = (
        "# Policy\n\nIntro text.\n\n## Refunds\n\nRefund text.\n\n"
        "### Cash\n\nCash text.\n\n## Fees\n\nFee text."
    )
    chunks = chunk_document(_doc(body))
    assert [c.section_path for c in chunks] == [
        "Policy",
        "Policy > Refunds",
        "Policy > Refunds > Cash",
        "Policy > Fees",
    ]
    assert [c.chunk_index for c in chunks] == [0, 1, 2, 3]
    assert chunks[1].content == "Refund text."


def test_empty_sections_are_skipped() -> None:
    chunks = chunk_document(_doc("# Policy\n\n## Empty\n\n## Full\n\nText."))
    assert [c.section_path for c in chunks] == ["Policy > Full"]


def test_long_section_is_split_with_one_sentence_overlap() -> None:
    sentences = [f"Sentence number {i} explains a rule in some detail." for i in range(40)]
    body = "# Policy\n\n## Long\n\n" + " ".join(sentences)
    chunks = chunk_document(_doc(body), max_tokens=60)
    assert len(chunks) > 1
    assert all(c.section_path == "Policy > Long" for c in chunks)
    assert all(c.token_estimate <= 60 for c in chunks)
    for prev, nxt in pairwise(chunks):
        last = prev.content.split("\n\n")[-1]
        assert last in nxt.content  # overlap carries the previous chunk's last sentence
    joined = " ".join(c.content for c in chunks)
    assert all(s in joined for s in sentences)  # nothing is lost


def test_lists_split_on_item_boundaries() -> None:
    items = "\n".join(f"- Item {i} with several words of explanation here." for i in range(30))
    chunks = chunk_document(_doc(f"# Policy\n\n## List\n\n{items}"), max_tokens=50)
    assert len(chunks) > 1
    for c in chunks:
        for line in c.content.splitlines():
            assert line.startswith("- Item") or not line.strip()


def test_overlong_single_sentence_is_hard_split() -> None:
    body = "# Policy\n\n## Run-on\n\n" + " ".join(["word"] * 400)
    chunks = chunk_document(_doc(body), max_tokens=50)
    assert len(chunks) > 1
    assert sum(c.content.count("word") for c in chunks) >= 400


def test_max_tokens_floor() -> None:
    with pytest.raises(ValueError, match="at least 20"):
        chunk_document(_doc("# P\n\ntext"), max_tokens=5)


def test_embedding_text_includes_section_path() -> None:
    chunk = chunk_document(_doc("# Policy\n\n## Refunds\n\nRefund text."))[0]
    assert chunk.embedding_text() == "Policy > Refunds\n\nRefund text."


def test_estimate_is_conservative() -> None:
    assert estimate_tokens("hello world") >= 2
    assert estimate_tokens("x" * 350) >= 100


def test_real_corpus_chunks_are_deterministic_and_bounded() -> None:
    corpus = load_corpus(KB_DIR)
    first = [chunk_document(d) for d in corpus]
    second = [chunk_document(d) for d in corpus]
    assert first == second
    all_chunks = [c for chunks in first for c in chunks]
    assert 40 <= len(all_chunks) <= 300
    assert all(c.token_estimate <= 300 for c in all_chunks)
    assert all(c.content.strip() for c in all_chunks)
    # No chunk contains a heading line: headings live in section_path.
    assert not any(line.startswith("#") for c in all_chunks for line in c.content.splitlines())
