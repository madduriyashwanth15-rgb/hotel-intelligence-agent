"""`hotel-kb` command line: download-model, ingest, ask, eval."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from hotel_agent.config import PROJECT_ROOT, Settings, get_settings
from hotel_agent.db.session import make_engine
from hotel_agent.knowledge.answer import EvidenceThresholds, ExtractiveAnswerer
from hotel_agent.knowledge.documents import load_corpus
from hotel_agent.knowledge.embeddings import FastEmbedEmbedder, download_model
from hotel_agent.knowledge.evaluation import (
    calibrate,
    end_to_end,
    load_eval_set,
    retrieval_metrics,
)
from hotel_agent.knowledge.ingest import ingest_documents
from hotel_agent.knowledge.retrieval import RetrievalMode, Retriever
from hotel_agent.logging_config import configure_logging

EVAL_SET = PROJECT_ROOT / "eval" / "kb_questions.json"
EVAL_RESULTS = PROJECT_ROOT / "eval" / "results"


def build_embedder(settings: Settings, use_query_instruction: bool = True) -> FastEmbedEmbedder:
    return FastEmbedEmbedder(
        settings.embedding_model,
        settings.resolve_path(settings.embedding_cache_dir),
        threads=settings.embedding_threads,
        use_query_instruction=use_query_instruction,
    )


def thresholds_from(settings: Settings) -> EvidenceThresholds:
    return EvidenceThresholds(settings.kb_min_similarity, settings.kb_min_coverage)


def _readonly_engine(settings: Settings, database: str | None) -> Engine:
    return make_engine(settings.readonly_database_url(database))


def _cmd_download_model(settings: Settings, _: argparse.Namespace) -> int:
    path = download_model(
        settings.embedding_model, settings.resolve_path(settings.embedding_cache_dir)
    )
    print(json.dumps({"model": settings.embedding_model, "verified_file": str(path)}))
    return 0


def _cmd_ingest(settings: Settings, args: argparse.Namespace) -> int:
    documents = load_corpus(settings.resolve_path(settings.kb_dir))
    engine = make_engine(settings.database_url(args.database))
    try:
        report = ingest_documents(
            engine, documents, build_embedder(settings), prune=not args.no_prune
        )
    finally:
        engine.dispose()
    print(json.dumps({"documents": len(documents), **report.as_dict()}))
    return 0


def _cmd_ask(settings: Settings, args: argparse.Namespace) -> int:
    engine = _readonly_engine(settings, args.database)
    embedder = build_embedder(settings)
    try:
        answerer = ExtractiveAnswerer(
            Retriever(engine, embedder), embedder, thresholds_from(settings)
        )
        result = answerer.answer(args.question, mode=RetrievalMode(args.mode))
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "status": result.status.value,
                "answer": result.answer,
                "citations": [asdict(c) for c in result.citations],
                "top_similarity": round(result.top_similarity, 4),
                "coverage": round(result.coverage, 4),
                "latency_ms": round(result.latency_ms, 1),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_eval(settings: Settings, args: argparse.Namespace) -> int:
    questions = load_eval_set(EVAL_SET)
    dev = [q for q in questions if q.split == "dev"]
    test = [q for q in questions if q.split == "test"]
    engine = _readonly_engine(settings, args.database)
    started = time.perf_counter()
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "embedding_model": settings.embedding_model,
        "eval_set": {"path": "eval/kb_questions.json", "questions": len(questions),
                     "dev": len(dev), "test": len(test)},
    }  # fmt: skip
    try:
        retrieval: list[dict[str, Any]] = []
        for instruction in (True, False):
            retriever = Retriever(engine, build_embedder(settings, instruction))
            for mode in RetrievalMode:
                for name, subset in (("dev", dev), ("test", test)):
                    metrics = retrieval_metrics(retriever, subset, mode)
                    retrieval.append({"split": name, "query_instruction": instruction, **metrics})
        report["retrieval"] = retrieval

        embedder = build_embedder(settings)
        retriever = Retriever(engine, embedder)
        answerer = ExtractiveAnswerer(retriever, embedder, thresholds_from(settings))
        thresholds, dev_scores = calibrate(answerer, retriever, dev)
        report["calibration"] = {"split": "dev", "thresholds": asdict(thresholds), **dev_scores}

        answerer.thresholds = thresholds
        for name, subset in (("dev", dev), ("test", test)):
            result = end_to_end(answerer, subset)
            if not args.include_rows:
                result.pop("rows")
            report[f"end_to_end_{name}"] = result
    finally:
        engine.dispose()
    report["wall_seconds"] = round(time.perf_counter() - started, 1)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        EVAL_RESULTS.mkdir(parents=True, exist_ok=True)
        (EVAL_RESULTS / args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="hotel-kb", description="Hotel knowledge base tools.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("download-model", help="One-time download of the pinned embedding model.")

    ingest = sub.add_parser("ingest", help="Load, chunk, embed and store the knowledge base.")
    ingest.add_argument("--database", default=None, help="Target database (default: POSTGRES_DB)")
    ingest.add_argument("--no-prune", action="store_true", help="Keep documents not on disk.")

    ask = sub.add_parser("ask", help="Answer a question from the knowledge base (extractive).")
    ask.add_argument("question")
    ask.add_argument("--mode", choices=[m.value for m in RetrievalMode], default="hybrid")
    ask.add_argument("--database", default=None)

    ev = sub.add_parser(
        "eval", help="Run retrieval and answer evaluation on eval/kb_questions.json."
    )
    ev.add_argument("--database", default=None)
    ev.add_argument("--output", default=None, help="File name to save under eval/results/.")
    ev.add_argument("--include-rows", action="store_true", help="Include per-question rows.")

    args = parser.parse_args(argv)
    # Answers contain ₹ and other non-ASCII text; Windows consoles default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    configure_logging(settings.log_level)
    handlers = {
        "download-model": _cmd_download_model,
        "ingest": _cmd_ingest,
        "ask": _cmd_ask,
        "eval": _cmd_eval,
    }
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
