"""Integrity of the evaluation set itself (so metrics measure what they claim)."""

from __future__ import annotations

from collections import Counter

from hotel_agent.config import PROJECT_ROOT
from hotel_agent.knowledge.documents import load_corpus
from hotel_agent.knowledge.evaluation import load_eval_set, normalise
from tests.test_kb_documents import KB_DIR

QUESTIONS = load_eval_set(PROJECT_ROOT / "eval" / "kb_questions.json")


def test_ids_unique_and_splits_balanced() -> None:
    assert len({q.id for q in QUESTIONS}) == len(QUESTIONS)
    splits = Counter((q.split, q.answerable) for q in QUESTIONS)
    for split in ("dev", "test"):
        assert splits[(split, True)] >= 20
        assert splits[(split, False)] >= 5


def test_answerable_evidence_is_verbatim_in_expected_document() -> None:
    bodies = {d.doc_key: normalise(d.body) for d in load_corpus(KB_DIR)}
    for q in QUESTIONS:
        if not q.answerable:
            assert q.evidence is None, q.id
            assert not q.expected_doc_keys, q.id
            continue
        assert q.evidence, q.id
        assert all(k in bodies for k in q.expected_doc_keys), q.id
        assert any(normalise(q.evidence) in bodies[k] for k in q.expected_doc_keys), q.id


def test_unanswerable_questions_have_no_verbatim_answer() -> None:
    """Sanity: key terms of 'not in KB' questions really are absent from the corpus."""
    corpus = " ".join(normalise(d.body) for d in load_corpus(KB_DIR))
    for term in ("casino", "wi-fi password", "parking", "gym", "bicycle", "star rating",
                 "conference", "kids club", "gst", "drone"):  # fmt: skip
        assert term not in corpus, term
