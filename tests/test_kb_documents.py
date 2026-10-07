"""Knowledge-base document loading, validation and corpus guarantees."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hotel_agent.config import PROJECT_ROOT
from hotel_agent.knowledge.documents import (
    MAX_DOCUMENT_BYTES,
    DocumentCategory,
    DocumentValidationError,
    KnowledgeDocument,
    load_corpus,
    parse_document,
)
from hotel_agent.synthetic.generator import ROOM_TYPE_SPECS

KB_DIR = PROJECT_ROOT / "data" / "knowledge_base"

VALID = """+++
doc_key = "TEST-DOC-001"
title = "Test Document"
category = "policy"
version = "1.0"
effective_date = 2026-01-01
synthetic = true
+++

# Test Document

Body text.
"""


def _with(old: str, new: str) -> str:
    assert old in VALID
    return VALID.replace(old, new)


def test_parse_valid_document() -> None:
    doc = parse_document(VALID, "test.md")
    assert doc.doc_key == "TEST-DOC-001"
    assert doc.category is DocumentCategory.POLICY
    assert doc.body.startswith("# Test Document")
    assert len(doc.content_sha256) == 64


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("# no front matter", "must start"),
        ("+++\ndoc_key = 'X'\n# never closed", "not closed"),
        (_with('version = "1.0"', "version = "), "invalid TOML"),
        (_with('version = "1.0"\n', ""), "missing front-matter fields"),
        (_with("synthetic = true", "synthetic = true\nauthor = 'x'"), "unknown front-matter"),
        (_with('"TEST-DOC-001"', '"test doc"'), "doc_key"),
        (_with('"policy"', '"marketing"'), "category"),
        (_with("2026-01-01", '"January"'), "effective_date"),
        (_with("synthetic = true", "synthetic = false"), "only synthetic"),
        (VALID.split("# Test")[0], "body is empty"),
    ],
)
def test_invalid_documents_rejected(text: str, problem: str) -> None:
    with pytest.raises(DocumentValidationError, match=problem):
        parse_document(text, "bad.md")


def test_oversized_document_rejected() -> None:
    with pytest.raises(DocumentValidationError, match="larger than"):
        parse_document(VALID + "x" * MAX_DOCUMENT_BYTES, "big.md")


def test_duplicate_doc_keys_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(VALID, encoding="utf-8")
    (tmp_path / "b.md").write_text(VALID, encoding="utf-8")
    with pytest.raises(DocumentValidationError, match="duplicate doc_key"):
        load_corpus(tmp_path)


def test_missing_directory_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_corpus(tmp_path / "nope")


# --- The real synthetic corpus ---


@pytest.fixture(scope="module")
def corpus() -> list[KnowledgeDocument]:
    return load_corpus(KB_DIR)


def test_corpus_loads_and_covers_all_categories(corpus: list[KnowledgeDocument]) -> None:
    assert len(corpus) >= 15
    assert {d.category for d in corpus} == set(DocumentCategory)


def test_corpus_mentions_only_the_fictional_hotel(corpus: list[KnowledgeDocument]) -> None:
    for doc in corpus:
        assert "@" not in doc.body, f"{doc.doc_key} contains an email-like string"


def test_room_guide_matches_phase1_room_types(corpus: list[KnowledgeDocument]) -> None:
    guide = next(d for d in corpus if d.doc_key == "ROOM-GUIDE-001").body
    for spec in ROOM_TYPE_SPECS:
        section = " ".join(guide.split(f"({spec.code})", 1)[1].split("##", 1)[0].split())
        rate = f"₹{int(spec.base_rate):,}"
        assert rate in section, f"{spec.code}: expected rack rate {rate}"
        assert re.search(rf"\b{spec.count} (rooms|suites|villas)\b", section), spec.code
        assert f"Maximum occupancy {spec.max_occupancy}" in section, spec.code
    assert f"{sum(s.count for s in ROOM_TYPE_SPECS)} rooms" in guide


def test_check_in_out_times_match_generator(corpus: list[KnowledgeDocument]) -> None:
    policy = next(d for d in corpus if d.doc_key == "POL-CHECKIN-001").body
    assert "Check-in time is 14:00 and check-out time is 11:00" in policy
