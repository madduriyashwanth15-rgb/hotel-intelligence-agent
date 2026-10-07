"""Heading-aware chunking of Markdown documents.

Each chunk stays within one section (its `section_path`, e.g.
"Cancellation Policy > Peak Season"). Sections longer than `max_tokens` are
split on paragraph, then sentence boundaries; consecutive pieces of the same
section overlap by one sentence so context is not lost at the cut.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from hotel_agent.knowledge.documents import KnowledgeDocument

DEFAULT_MAX_TOKENS = 300
SECTION_SEPARATOR = " > "
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

TokenCounter = Callable[[str], int]


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for WordPiece models (overestimates rather than under).

    Real token counts are checked against the model tokenizer in tests that use the model.
    """
    words = len(text.split())
    return max(math.ceil(words * 1.4), math.ceil(len(text) / 3.5))


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_index: int
    section_path: str
    content: str
    token_estimate: int

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def embedding_text(self) -> str:
        """Text sent to the embedder: the section path gives the chunk its context."""
        return f"{self.section_path}\n\n{self.content}"


@dataclass(slots=True)
class _Section:
    path: tuple[str, ...]
    lines: list[str]


def _sections(doc: KnowledgeDocument) -> list[_Section]:
    stack: list[tuple[int, str]] = []
    sections = [_Section((doc.title,), [])]
    for line in doc.body.splitlines():
        match = _HEADING.match(line)
        if not match:
            sections[-1].lines.append(line)
            continue
        level, heading = len(match.group(1)), match.group(2).strip()
        if level == 1:
            # The H1 is the document title, already the root of every path.
            stack = []
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        sections.append(_Section((doc.title, *(h for _, h in stack)), []))
    return [s for s in sections if "\n".join(s.lines).strip()]


def _paragraphs(lines: list[str]) -> list[str]:
    """Blank-line separated blocks; a list stays together with its items."""
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line.rstrip())
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _pieces(paragraph: str, max_tokens: int, count: TokenCounter) -> list[str]:
    """Break one paragraph into pieces that fit, preferring line then sentence boundaries."""
    if count(paragraph) <= max_tokens:
        return [paragraph]
    units = paragraph.splitlines() if "\n" in paragraph else _split_sentences(paragraph)
    if len(units) == 1:
        # A single over-long sentence: hard split on words as a last resort.
        words = units[0].split()
        step = max(1, len(words) // math.ceil(count(units[0]) / max_tokens))
        return [" ".join(words[i : i + step]) for i in range(0, len(words), step)]
    out: list[str] = []
    for unit in units:
        out.extend(_pieces(unit, max_tokens, count))
    return out


def _last_sentence(text: str) -> str:
    sentences = _split_sentences(text.splitlines()[-1])
    return sentences[-1] if sentences else ""


def chunk_document(
    doc: KnowledgeDocument,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    count_tokens: TokenCounter = estimate_tokens,
) -> list[Chunk]:
    if max_tokens < 20:
        raise ValueError("max_tokens must be at least 20")
    chunks: list[Chunk] = []

    for section in _sections(doc):
        path = SECTION_SEPARATOR.join(section.path)
        pieces = [
            piece
            for paragraph in _paragraphs(section.lines)
            for piece in _pieces(paragraph, max_tokens, count_tokens)
        ]
        current: list[str] = []
        for piece in pieces:
            candidate = "\n\n".join([*current, piece])
            if current and count_tokens(candidate) > max_tokens:
                chunks.append(_make(len(chunks), path, current, count_tokens))
                overlap = _last_sentence(current[-1])
                current = [overlap] if overlap and overlap != piece else []
                if current and count_tokens("\n\n".join([*current, piece])) > max_tokens:
                    current = []
            current.append(piece)
        if current:
            chunks.append(_make(len(chunks), path, current, count_tokens))
    return chunks


def _make(index: int, path: str, parts: list[str], count: TokenCounter) -> Chunk:
    content = "\n\n".join(parts)
    return Chunk(index, path, content, count(content))
