"""Grounded extractive answers with citations (ADR-0010).

No LLM is involved: the answer is made of sentences copied verbatim from the
retrieved chunks, each tagged with its citation (`DOC-KEY#chunk_index`). When
the evidence is too weak the answerer refuses with `insufficient_evidence`.

Evidence sufficiency uses two signals, with thresholds calibrated on the
evaluation set's dev split (see eval/ and docs/DECISIONS.md, ADR-0013):
- top vector similarity between the query and the best retrieved chunk;
- coverage: the IDF-weighted share of the query's content-word stems found in the
  top evidence chunks. Rare terms count more, and a term that never occurs in the
  knowledge base (e.g. "casino") weighs the most, so off-topic questions fail.
  Question-framing words ("how much", "how long") are ignored.
Retrieved text is treated purely as data: it is quoted, never executed or obeyed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import StrEnum

from hotel_agent.knowledge.chunking import split_sentences
from hotel_agent.knowledge.embeddings import Embedder
from hotel_agent.knowledge.retrieval import RetrievalMode, RetrievedChunk, Retriever

INSUFFICIENT_EVIDENCE_MESSAGE = (
    "I don't know. The hotel's documents do not contain enough information to answer this "
    "question. Please check with a manager."
)
_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")
# Stems (PostgreSQL 'english' config) that frame a question rather than name its topic.
QUESTION_WORDS = frozenset(
    {
        "much", "mani", "long", "far", "big", "fast", "quick", "get", "got", "mean",
        "someon", "doe", "happen", "need", "use", "can", "may", "allow", "offer",
        "ask", "say", "tell", "know", "want", "like", "way",
    }
)  # fmt: skip


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class EvidenceThresholds:
    min_similarity: float
    min_coverage: float


@dataclass(frozen=True, slots=True)
class Citation:
    citation: str
    doc_key: str
    title: str
    section_path: str
    chunk_index: int


@dataclass(frozen=True, slots=True)
class AnswerSentence:
    text: str
    citation: str
    score: float


@dataclass(frozen=True, slots=True)
class Answer:
    question: str
    status: AnswerStatus
    answer: str
    sentences: list[AnswerSentence]
    citations: list[Citation]
    top_similarity: float
    coverage: float
    thresholds: EvidenceThresholds
    retrieved: list[RetrievedChunk] = field(repr=False)
    latency_ms: float = 0.0


def _units(content: str) -> list[str]:
    """Answerable units of a chunk: list items stay whole, prose splits into sentences."""
    units: list[str] = []
    for block in content.split("\n\n"):
        lines = block.splitlines()
        if lines and all(_LIST_ITEM.match(line) or line[:1].isspace() for line in lines):
            items: list[str] = []
            for line in lines:
                if _LIST_ITEM.match(line) or not items:
                    items.append(_LIST_ITEM.sub("", line).strip())
                else:
                    items[-1] += " " + line.strip()
            units.extend(items)
        else:
            units.extend(split_sentences(" ".join(line.strip() for line in lines)))
    return [u for u in units if len(u) >= 3]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class ExtractiveAnswerer:
    def __init__(
        self,
        retriever: Retriever,
        embedder: Embedder,
        thresholds: EvidenceThresholds,
        *,
        top_k: int = 5,
        evidence_chunks: int = 3,
        max_sentences: int = 3,
        relative_margin: float = 0.08,
    ) -> None:
        self._retriever = retriever
        self._embedder = embedder
        self.thresholds = thresholds
        self._top_k = top_k
        self._evidence_chunks = evidence_chunks
        self._max_sentences = max_sentences
        self._margin = relative_margin

    def assess(self, question: str, hits: list[RetrievedChunk]) -> tuple[float, float]:
        """Return (top vector similarity, IDF-weighted query coverage by the evidence)."""
        sims = [h.vector_similarity for h in hits if h.vector_similarity is not None]
        missing = [h.chunk_id for h in hits if h.vector_similarity is None]
        if missing:  # e.g. full-text-only mode: the gate always needs the vector signal
            sims.extend(self._retriever.vector_similarities(question, missing).values())
        top_similarity = max(sims, default=0.0)
        query_stems = self._retriever.query_lexemes(question) - QUESTION_WORDS
        if not query_stems:
            return top_similarity, 0.0
        idf = self._retriever.term_idf()
        unseen = self._retriever.unseen_term_idf()
        evidence = frozenset().union(*(h.lexemes for h in hits[: self._evidence_chunks]))
        total = sum(idf.get(t, unseen) for t in query_stems)
        covered = sum(idf.get(t, unseen) for t in query_stems & evidence)
        return top_similarity, covered / total if total else 0.0

    def answer(
        self, question: str, mode: RetrievalMode = RetrievalMode.HYBRID, category: str | None = None
    ) -> Answer:
        start = time.perf_counter()
        hits = self._retriever.search(question, top_k=self._top_k, mode=mode, category=category)
        top_similarity, coverage = self.assess(question, hits)
        sufficient = (
            bool(hits)
            and top_similarity >= self.thresholds.min_similarity
            and coverage >= self.thresholds.min_coverage
        )
        if not sufficient:
            return Answer(
                question=question,
                status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                answer=INSUFFICIENT_EVIDENCE_MESSAGE,
                sentences=[],
                citations=[],
                top_similarity=top_similarity,
                coverage=coverage,
                thresholds=self.thresholds,
                retrieved=hits,
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        sentences = self._select_sentences(question, hits[: self._evidence_chunks])
        by_citation = {h.citation: h for h in hits}
        # Citations can only point at chunks retrieval actually returned.
        if any(s.citation not in by_citation for s in sentences):
            raise RuntimeError("answer cites a chunk that was not retrieved")
        cited = list(dict.fromkeys(s.citation for s in sentences))
        citations = [
            Citation(
                citation=c,
                doc_key=by_citation[c].doc_key,
                title=by_citation[c].title,
                section_path=by_citation[c].section_path,
                chunk_index=by_citation[c].chunk_index,
            )
            for c in cited
        ]
        return Answer(
            question=question,
            status=AnswerStatus.ANSWERED,
            answer=" ".join(f"{s.text} [{s.citation}]" for s in sentences),
            sentences=sentences,
            citations=citations,
            top_similarity=top_similarity,
            coverage=coverage,
            thresholds=self.thresholds,
            retrieved=hits,
            latency_ms=(time.perf_counter() - start) * 1000,
        )

    def _select_sentences(
        self, question: str, evidence: list[RetrievedChunk]
    ) -> list[AnswerSentence]:
        candidates = [
            (chunk_pos, unit_pos, unit, chunk.citation)
            for chunk_pos, chunk in enumerate(evidence)
            for unit_pos, unit in enumerate(_units(chunk.content))
        ]
        if not candidates:
            return []
        query_vec = self._embedder.embed_query(question)
        # Score each sentence with its section heading: "65 square metres" only answers
        # "how big is the suite" in the context of the "Hillside Suite" section.
        sections = {chunk.citation: chunk.section_path for chunk in evidence}
        unit_vecs = self._embedder.embed_documents(
            [f"{sections[c[3]]}: {c[2]}" for c in candidates]
        )
        scored = [
            (_cosine(query_vec, vec), cand) for vec, cand in zip(unit_vecs, candidates, strict=True)
        ]
        best = max(score for score, _ in scored)
        chosen = sorted(
            (item for item in scored if item[0] >= best - self._margin),
            key=lambda item: -item[0],
        )[: self._max_sentences]
        # Present in reading order: by chunk rank, then position within the chunk.
        chosen.sort(key=lambda item: (item[1][0], item[1][1]))
        return [
            AnswerSentence(text=unit, citation=citation, score=round(score, 4))
            for score, (_, _, unit, citation) in chosen
        ]
