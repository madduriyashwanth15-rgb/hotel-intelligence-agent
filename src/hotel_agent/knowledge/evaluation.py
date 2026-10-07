"""Knowledge-base evaluation: retrieval metrics, refusal calibration, end-to-end answers.

Evaluation set: eval/kb_questions.json. Answerable questions carry the expected
document key(s) and a verbatim evidence quote; unanswerable questions must be
refused. Refusal thresholds are calibrated on the `dev` split only and reported
on the held-out `test` split.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hotel_agent.knowledge.answer import AnswerStatus, EvidenceThresholds, ExtractiveAnswerer
from hotel_agent.knowledge.retrieval import RetrievalMode, Retriever

SIMILARITY_GRID = [round(0.50 + i * 0.005, 3) for i in range(61)]  # 0.50 .. 0.80
COVERAGE_GRID = [round(i * 0.05, 2) for i in range(21)]  # 0.00 .. 1.00


@dataclass(frozen=True, slots=True)
class EvalQuestion:
    id: str
    question: str
    answerable: bool
    expected_doc_keys: tuple[str, ...]
    evidence: str | None
    kind: str
    split: str


def normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def load_eval_set(path: Path) -> list[EvalQuestion]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        EvalQuestion(
            id=q["id"],
            question=q["question"],
            answerable=q["answerable"],
            expected_doc_keys=tuple(q["expected_doc_keys"]),
            evidence=q["evidence"],
            kind=q["kind"],
            split=q["split"],
        )
        for q in data["questions"]
    ]


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def retrieval_metrics(
    retriever: Retriever, questions: Iterable[EvalQuestion], mode: RetrievalMode, depth: int = 10
) -> dict[str, Any]:
    """Doc-level Recall@1/3/5 and MRR@depth, chunk-level evidence Recall@5, latency."""
    answerable = [q for q in questions if q.answerable]
    ranks: list[int | None] = []
    evidence_hits = 0
    latencies: list[float] = []
    for q in answerable:
        start = time.perf_counter()
        hits = retriever.search(q.question, top_k=depth, mode=mode)
        latencies.append((time.perf_counter() - start) * 1000)
        rank = next((i for i, h in enumerate(hits, 1) if h.doc_key in q.expected_doc_keys), None)
        ranks.append(rank)
        if q.evidence and any(normalise(q.evidence) in normalise(h.content) for h in hits[:5]):
            evidence_hits += 1
    n = len(answerable)
    return {
        "mode": mode.value,
        "questions": n,
        "doc_recall@1": round(sum(1 for r in ranks if r is not None and r <= 1) / n, 4),
        "doc_recall@3": round(sum(1 for r in ranks if r is not None and r <= 3) / n, 4),
        "doc_recall@5": round(sum(1 for r in ranks if r is not None and r <= 5) / n, 4),
        f"mrr@{depth}": round(sum(1 / r for r in ranks if r is not None) / n, 4),
        "evidence_recall@5": round(evidence_hits / n, 4),
        "latency_ms_p50": round(statistics.median(latencies), 1),
        "latency_ms_p95": round(_percentile(latencies, 95), 1),
    }


@dataclass(frozen=True, slots=True)
class _Features:
    question: EvalQuestion
    similarity: float
    coverage: float


def _decision_scores(features: list[_Features], thresholds: EvidenceThresholds) -> dict[str, float]:
    answerable = [f for f in features if f.question.answerable]
    unanswerable = [f for f in features if not f.question.answerable]

    def answers(f: _Features) -> bool:
        return f.similarity >= thresholds.min_similarity and f.coverage >= thresholds.min_coverage

    answer_rate = sum(answers(f) for f in answerable) / max(len(answerable), 1)
    refusal_rate = sum(not answers(f) for f in unanswerable) / max(len(unanswerable), 1)
    return {
        "answer_rate_on_answerable": answer_rate,
        "refusal_rate_on_unanswerable": refusal_rate,
        "balanced_accuracy": (answer_rate + refusal_rate) / 2,
    }


def calibrate(
    answerer: ExtractiveAnswerer, retriever: Retriever, questions: list[EvalQuestion]
) -> tuple[EvidenceThresholds, dict[str, float]]:
    """Grid-search thresholds maximising balanced accuracy on the given (dev) questions.

    Ties prefer answering more answerable questions, then the lowest thresholds.
    """
    features = []
    for q in questions:
        hits = retriever.search(q.question, top_k=5, mode=RetrievalMode.HYBRID)
        similarity, coverage = answerer.assess(q.question, hits)
        features.append(_Features(q, similarity, coverage))
    best: tuple[tuple[float, float, float, float], EvidenceThresholds] | None = None
    for sim in SIMILARITY_GRID:
        for cov in COVERAGE_GRID:
            t = EvidenceThresholds(sim, cov)
            s = _decision_scores(features, t)
            key = (s["balanced_accuracy"], s["answer_rate_on_answerable"], -sim, -cov)
            if best is None or key > best[0]:
                best = (key, t)
    if best is None:
        raise ValueError("calibration needs at least one question")
    chosen = best[1]
    return chosen, {k: round(v, 4) for k, v in _decision_scores(features, chosen).items()}


def end_to_end(answerer: ExtractiveAnswerer, questions: list[EvalQuestion]) -> dict[str, Any]:
    """Answer every question and score refusals, evidence and citations."""
    answerable = [q for q in questions if q.answerable]
    unanswerable = [q for q in questions if not q.answerable]
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for q in questions:
        result = answerer.answer(q.question)
        latencies.append(result.latency_ms)
        answered = result.status is AnswerStatus.ANSWERED
        rows.append(
            {
                "id": q.id,
                "answerable": q.answerable,
                "status": result.status.value,
                "evidence_in_answer": bool(
                    answered and q.evidence and normalise(q.evidence) in normalise(result.answer)
                ),
                "cited_expected_doc": bool(
                    answered and any(c.doc_key in q.expected_doc_keys for c in result.citations)
                ),
                "top_similarity": round(result.top_similarity, 4),
                "coverage": round(result.coverage, 4),
                "answer": result.answer,
            }
        )
    by_id = {r["id"]: r for r in rows}
    answered_ok = [by_id[q.id] for q in answerable if by_id[q.id]["status"] == "answered"]
    refused_bad = [by_id[q.id] for q in unanswerable if by_id[q.id]["status"] == "answered"]
    total_refusals = sum(1 for r in rows if r["status"] == "insufficient_evidence")
    correct_refusals = sum(
        1 for q in unanswerable if by_id[q.id]["status"] == "insufficient_evidence"
    )
    n_a, n_u = max(len(answerable), 1), max(len(unanswerable), 1)
    return {
        "questions": len(questions),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "answer_rate_on_answerable": round(len(answered_ok) / n_a, 4),
        "evidence_in_answer_rate": round(sum(r["evidence_in_answer"] for r in rows) / n_a, 4),
        "cited_expected_doc_rate": round(sum(r["cited_expected_doc"] for r in rows) / n_a, 4),
        "refusal_recall": round(correct_refusals / n_u, 4),
        "refusal_precision": round(correct_refusals / total_refusals, 4)
        if total_refusals
        else None,
        "false_answers_on_unanswerable": [r["id"] for r in refused_bad],
        "latency_ms_p50": round(statistics.median(latencies), 1),
        "latency_ms_p95": round(_percentile(latencies, 95), 1),
        "rows": rows,
    }
