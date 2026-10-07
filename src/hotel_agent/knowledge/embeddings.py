"""Text embedders (ADR-0009).

- `FastEmbedEmbedder`: BAAI/bge-small-en-v1.5 via fastembed + ONNX Runtime, CPU only.
  Loads strictly from the local cache (`local_files_only`), verifies the pinned model
  file hash, and never downloads at runtime. Download once with
  `uv run hotel-kb download-model`.
- `HashingEmbedder`: deterministic, dependency-free embedder for tests. It is NOT a
  semantic model; it only lets tests exercise the pipeline without the real model.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

# Must be set before huggingface_hub is imported (fastembed imports it lazily below).
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

if TYPE_CHECKING:
    from fastembed import TextEmbedding

EMBEDDING_DIM = 384
Vector = list[float]


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...

    def embed_query(self, text: str) -> Vector: ...


class ModelNotAvailableError(RuntimeError):
    pass


class ModelIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """A pinned model artifact: what we expect to find in the local cache."""

    name: str
    hf_repo: str
    revision: str
    model_file: str
    sha256: str
    dimension: int
    # BGE's suggested instruction for short queries against passages. On by default: it
    # improved end-to-end answers on the eval dev split (see eval/results/kb_eval.json).
    query_instruction: str


PINNED_MODELS: dict[str, ModelSpec] = {
    "BAAI/bge-small-en-v1.5": ModelSpec(
        name="BAAI/bge-small-en-v1.5",
        hf_repo="Qdrant/bge-small-en-v1.5-onnx-Q",
        revision="aa8f8b060edb00e03bfdd08813a2949946c8ba55",
        model_file="model_optimized.onnx",
        sha256="51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431",
        dimension=EMBEDDING_DIM,
        query_instruction="Represent this sentence for searching relevant passages: ",
    ),
}


def model_file_path(spec: ModelSpec, cache_dir: Path) -> Path:
    repo_dir = "models--" + spec.hf_repo.replace("/", "--")
    return cache_dir / repo_dir / "snapshots" / spec.revision / spec.model_file


def verify_model_file(spec: ModelSpec, cache_dir: Path) -> Path:
    path = model_file_path(spec, cache_dir)
    if not path.is_file():
        raise ModelNotAvailableError(
            f"Embedding model {spec.name} (revision {spec.revision[:8]}) not found in "
            f"{cache_dir}. Run `uv run hotel-kb download-model` once."
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != spec.sha256:
        raise ModelIntegrityError(
            f"{path} has sha256 {digest}, expected {spec.sha256}; refusing to load it."
        )
    return path


def download_model(model_name: str, cache_dir: Path) -> Path:
    """One-time download from Hugging Face (anonymous, free), then verify the pinned hash."""
    from fastembed import TextEmbedding  # heavy import, only when needed

    spec = PINNED_MODELS[model_name]
    cache_dir.mkdir(parents=True, exist_ok=True)
    TextEmbedding(model_name, cache_dir=str(cache_dir), threads=1, lazy_load=True)
    return verify_model_file(spec, cache_dir)


class FastEmbedEmbedder:
    """bge-small-en-v1.5 on CPU. Thread count is kept low for small machines."""

    def __init__(
        self,
        model_name: str,
        cache_dir: Path,
        threads: int = 2,
        use_query_instruction: bool = True,
        batch_size: int = 16,
    ) -> None:
        if model_name not in PINNED_MODELS:
            raise ValueError(f"model {model_name!r} is not pinned in PINNED_MODELS")
        self._spec = PINNED_MODELS[model_name]
        self._cache_dir = cache_dir
        self._threads = threads
        self._batch_size = batch_size
        self.use_query_instruction = use_query_instruction
        self._model: TextEmbedding | None = None

    @property
    def model_name(self) -> str:
        return self._spec.name

    @property
    def dimension(self) -> int:
        return self._spec.dimension

    def _load(self) -> TextEmbedding:
        if self._model is None:
            verify_model_file(self._spec, self._cache_dir)
            from fastembed import TextEmbedding  # heavy import, deferred

            self._model = TextEmbedding(
                self._spec.name,
                cache_dir=str(self._cache_dir),
                threads=self._threads,
                local_files_only=True,  # never touch the network at runtime
            )
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        vectors = self._load().embed(list(texts), batch_size=self._batch_size)
        return [v.astype(float).tolist() for v in vectors]

    def embed_query(self, text: str) -> Vector:
        query = self._spec.query_instruction + text if self.use_query_instruction else text
        return self.embed_documents([query])[0]

    def count_tokens(self, text: str) -> int:
        return int(self._load().token_count(text))


_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Deterministic feature-hashing embedder for tests (word unigrams + bigrams)."""

    def __init__(self, dimension: int = EMBEDDING_DIM) -> None:
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return "test/hashing-embedder"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _embed(self, text: str) -> Vector:
        words = _WORD.findall(text.lower())
        features = words + [f"{a}_{b}" for a, b in pairwise(words)]
        vec = [0.0] * self._dimension
        for feature in features:
            h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
            vec[h % self._dimension] += 1.0 if (h >> 32) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> Vector:
        return self._embed(text)
