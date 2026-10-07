"""Load knowledge-base documents: Markdown with a TOML front-matter block (ADR-0012).

+++
doc_key = "POL-CANCEL-001"
title = "Cancellation, No-Show and Refund Policy"
category = "cancellation"
version = "1.2"
effective_date = 2026-04-01
synthetic = true
+++

# Markdown body ...
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

MAX_DOCUMENT_BYTES = 200_000
FRONT_MATTER_DELIMITER = "+++"
DOC_KEY_PATTERN = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
REQUIRED_FIELDS = ("doc_key", "title", "category", "version", "effective_date", "synthetic")


class DocumentCategory(StrEnum):
    CANCELLATION = "cancellation"
    POLICY = "policy"
    SOP = "sop"
    ROOM_INFO = "room_info"
    FNB = "fnb"
    EMERGENCY = "emergency"
    SERVICE_RECOVERY = "service_recovery"


class DocumentValidationError(ValueError):
    def __init__(self, source: str, problem: str) -> None:
        super().__init__(f"{source}: {problem}")
        self.source = source
        self.problem = problem


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    doc_key: str
    title: str
    category: DocumentCategory
    version: str
    effective_date: date
    body: str
    source_path: str
    content_sha256: str


def parse_document(text: str, source: str) -> KnowledgeDocument:
    """Parse and validate one document's text. `source` is used for errors and provenance."""
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise DocumentValidationError(source, f"larger than {MAX_DOCUMENT_BYTES} bytes")
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_DELIMITER:
        raise DocumentValidationError(source, "must start with a '+++' front-matter line")
    try:
        end = next(
            i for i, line in enumerate(lines[1:], 1) if line.strip() == FRONT_MATTER_DELIMITER
        )
    except StopIteration:
        raise DocumentValidationError(source, "front matter is not closed with '+++'") from None

    try:
        meta = tomllib.loads("\n".join(lines[1:end]))
    except tomllib.TOMLDecodeError as exc:
        raise DocumentValidationError(source, f"invalid TOML front matter: {exc}") from None

    missing = [f for f in REQUIRED_FIELDS if f not in meta]
    if missing:
        raise DocumentValidationError(source, f"missing front-matter fields: {missing}")
    unknown = sorted(set(meta) - set(REQUIRED_FIELDS))
    if unknown:
        raise DocumentValidationError(source, f"unknown front-matter fields: {unknown}")

    doc_key, title, version = meta["doc_key"], meta["title"], meta["version"]
    if not isinstance(doc_key, str) or not DOC_KEY_PATTERN.match(doc_key):
        raise DocumentValidationError(source, "doc_key must look like 'POL-CANCEL-001'")
    if not isinstance(title, str) or not title.strip():
        raise DocumentValidationError(source, "title must be a non-empty string")
    if not isinstance(version, str) or not version.strip():
        raise DocumentValidationError(source, "version must be a non-empty string")
    try:
        category = DocumentCategory(meta["category"])
    except ValueError:
        allowed = [c.value for c in DocumentCategory]
        raise DocumentValidationError(source, f"category must be one of {allowed}") from None
    effective = meta["effective_date"]
    if not isinstance(effective, date):
        raise DocumentValidationError(source, "effective_date must be a TOML date (YYYY-MM-DD)")
    if meta["synthetic"] is not True:
        raise DocumentValidationError(
            source, "only synthetic documents are allowed (synthetic = true)"
        )

    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise DocumentValidationError(source, "document body is empty")

    return KnowledgeDocument(
        doc_key=doc_key,
        title=title.strip(),
        category=category,
        version=version.strip(),
        effective_date=effective,
        body=body,
        source_path=source,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def load_document(path: Path, root: Path | None = None) -> KnowledgeDocument:
    source = path.relative_to(root).as_posix() if root else path.name
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise DocumentValidationError(source, f"larger than {MAX_DOCUMENT_BYTES} bytes")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise DocumentValidationError(source, "file is not valid UTF-8") from None
    return parse_document(text, source)


def load_corpus(directory: Path) -> list[KnowledgeDocument]:
    """Load every `*.md` document in `directory` (non-recursive), sorted by doc_key."""
    if not directory.is_dir():
        raise FileNotFoundError(f"knowledge base directory not found: {directory}")
    documents = [load_document(p, directory) for p in sorted(directory.glob("*.md"))]
    seen: dict[str, str] = {}
    for doc in documents:
        if doc.doc_key in seen:
            raise DocumentValidationError(
                doc.source_path, f"duplicate doc_key {doc.doc_key} (also in {seen[doc.doc_key]})"
            )
        seen[doc.doc_key] = doc.source_path
    return sorted(documents, key=lambda d: d.doc_key)
