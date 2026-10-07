"""`hotel-kb` command line: download-model, ingest, ask, eval."""

from __future__ import annotations

import argparse
import json
import sys

from hotel_agent.config import Settings, get_settings
from hotel_agent.db.session import make_engine
from hotel_agent.knowledge.documents import load_corpus
from hotel_agent.knowledge.embeddings import FastEmbedEmbedder, download_model
from hotel_agent.knowledge.ingest import ingest_documents
from hotel_agent.logging_config import configure_logging


def build_embedder(settings: Settings) -> FastEmbedEmbedder:
    return FastEmbedEmbedder(
        settings.embedding_model,
        settings.resolve_path(settings.embedding_cache_dir),
        threads=settings.embedding_threads,
    )


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


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="hotel-kb", description="Hotel knowledge base tools.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("download-model", help="One-time download of the pinned embedding model.")

    ingest = sub.add_parser("ingest", help="Load, chunk, embed and store the knowledge base.")
    ingest.add_argument("--database", default=None, help="Target database (default: POSTGRES_DB)")
    ingest.add_argument("--no-prune", action="store_true", help="Keep documents not on disk.")

    args = parser.parse_args(argv)
    configure_logging(settings.log_level)
    handlers = {"download-model": _cmd_download_model, "ingest": _cmd_ingest}
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
