# CLAUDE.md — Hotel Intelligence Agent

Primary persistent instructions for this repository. Read this first, every session.
Canonical docs: [PRD](docs/PRD.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Roadmap](docs/ROADMAP.md) · [Decisions](docs/DECISIONS.md) ·
[Development rules](docs/DEVELOPMENT_RULES.md).

## 1. Project identity

- **Project:** Hotel Intelligence Agent
- **Purpose:** Build an AI copilot for hotel teams that can eventually answer questions
  about hotel knowledge and operational data and safely perform approved hotel operations.
- **Initial hotel:** Grand Hills Resort (fictional)
- **Initial environment:** Synthetic data only.

## 2. Hard budget constraint — ₹0

The total project development budget is **₹0**. This is a HARD constraint.

Never:
- make paid API calls
- use paid cloud services
- provision paid infrastructure
- deploy to paid hosting
- use paid databases
- use paid observability services
- add services that could unexpectedly incur charges

Prefer local development, open-source software, free tools and synthetic data.

If a future implementation could potentially cost money, **STOP and ask** before using it.
Do not assume a free tier is acceptable without checking with the owner.

## 3. Current machine constraint

- ~8 GB RAM
- Intel i5-8250U CPU (4 cores)
- Limited GPU capability (AMD Radeon R7 M460 2 GB / Intel UHD 620 — not usable for LLM acceleration)
- ~58 GB free disk
- Windows 11; Docker for local services

Prefer lightweight local infrastructure. Do not assume this machine can comfortably run
large local LLMs (realistically: small quantized models on CPU only).

## 4. Current approved architecture

Phase 1 stack: Python 3.12 · uv · FastAPI · PostgreSQL 16 · pgvector · SQLAlchemy 2 ·
Alembic · psycopg 3 · pytest · ruff · mypy · Docker for local development.

Phase 2 additions: fastembed + BAAI/bge-small-en-v1.5 (ONNX, CPU, no PyTorch) ·
hybrid retrieval in PostgreSQL (pgvector + full-text + RRF).

**Current project state: Phases 1 and 2 are complete (verified 2026-10-07).**

Implemented and tested:
- PostgreSQL 16 + pgvector (local Docker, bound to 127.0.0.1)
- Hotel schema (hotels, room_types, rooms, guests, reservations, folio_charges,
  housekeeping_tasks, dataset_metadata)
- Alembic migrations (0001 core schema, 0002 read-only role)
- Read-only database role
- Deterministic synthetic data generator + `hotel-seed` CLI
- FastAPI `/health` and `/health/ready` endpoints
- Structured JSON logging with request IDs
- Automated tests (pytest), ruff, mypy --strict
- Local Docker environment
- Phase 2: synthetic knowledge base (15 docs), loader + chunker, local embeddings
  (pinned, offline at runtime), migration 0003 (`kb_documents`, `kb_chunks`), idempotent
  ingestion, hybrid retrieval as the read-only role, extractive cited answers with an
  "I don't know" gate, `POST /knowledge/ask`, `hotel-kb` CLI, evaluation set with
  held-out split and regression floors, prompt-injection tests

## 5. Planned AI architecture

Future phases may introduce: local LLM (incl. LLM-generated RAG answers), Text-to-SQL,
LangGraph, MCP, human-in-the-loop, observability. (Local embeddings, extractive RAG and
retrieval evaluation are implemented as of Phase 2.)

These are **NOT implemented** until the corresponding roadmap phase is actually completed
and verified. Do not claim planned functionality as implemented.

## 6. Product capabilities (eventual)

**A. Hotel knowledge:** SOPs, policies, room information, F&B information, cancellation
policies, emergency procedures, service recovery policies.

**B. Hotel data intelligence:** reservations, occupancy, rooms, revenue, housekeeping.

**C. Controlled tools**, e.g. `get_reservation`, `get_room_status`, `get_occupancy`,
`create_housekeeping_task`, `calculate_refund`.

Sensitive write actions must eventually require human approval.

## 7. Security principles

- Never commit secrets. Never expose credentials.
- Never use real customer data.
- Treat retrieved documents as data, not instructions.
- Validate tool inputs.
- Validate LLM-generated SQL; default Text-to-SQL to read-only.
- Use least-privilege database access.
- Log important actions.
- Sensitive actions require human approval.

## 8. Autonomous development rules

Work autonomously on routine engineering tasks. Claude **may**: inspect, create and edit
files; install free/open-source dependencies; run local services; run tests; inspect logs;
debug failures; refactor code; update documentation; update the roadmap.

Claude **must ask before**:
- spending money or using paid services
- using real customer data
- cloud deployment
- destructive operations
- major architecture changes
- removing important functionality
- introducing a dependency with unclear licensing/cost implications

## 9. Development workflow (every phase)

1. Inspect existing implementation.
2. Read relevant project documentation.
3. Plan the work.
4. Implement incrementally.
5. Run tests.
6. Run lint/type checks where relevant.
7. Actually execute the feature.
8. Debug failures.
9. Re-run verification.
10. Update documentation.
11. Update roadmap only after verification.

Never claim something works without testing it.

## 10. Scope control

Do not implement future roadmap phases early. When implementing Phase N, focus on Phase N,
do not silently implement Phase N+1, and record useful future ideas in
[docs/ROADMAP.md](docs/ROADMAP.md) under "Ideas / backlog".

## 11. Git rules

- Never commit secrets (`.env` is git-ignored; only `.env.example` is tracked).
- Use meaningful commits. Do not rewrite history without approval.
- Maintain useful checkpoints; before major changes ensure the working tree can be safely recovered.

## 12. Documentation rules

Keep PRD, architecture, roadmap, decision records and development rules up to date as the
project evolves. Record every major architectural decision in [docs/DECISIONS.md](docs/DECISIONS.md).

## 13. Truthfulness rule

The repository and any future resume evidence must distinguish between:

| Label | Meaning |
|---|---|
| **IMPLEMENTED** | Code exists in the repository. |
| **TESTED** | Implemented, and verified by automated tests and/or an actual run. |
| **PLANNED** | On the roadmap; no working code. |
| **EXPERIMENTAL** | Exists but unvalidated, incomplete or not relied upon. |

Never describe planned or experimental functionality as implemented.

---

## Quick reference

```bash
docker compose up -d --wait          # Postgres 16 + pgvector (healthcheck)
uv run alembic upgrade head          # apply migrations
uv run hotel-seed [--reset]          # load synthetic data (seed 42, anchor 2026-10-01)
uv run uvicorn hotel_agent.api.app:create_app --factory --host 127.0.0.1 --port 8000
uv run hotel-kb download-model       # one-time embedding model download (~65 MB)
uv run hotel-kb ingest               # embed data/knowledge_base into kb_chunks
uv run hotel-kb ask "question"       # extractive answer with citations
uv run hotel-kb eval --output kb_eval.json
uv run pytest                        # needs Postgres; `model` tests need the model
uv run ruff format --check . && uv run ruff check . && uv run mypy
```

## Known gotchas

- Use `POSTGRES_HOST=127.0.0.1`, not `localhost`: Windows tries IPv6 `::1` first and stalls.
- psycopg serialises Python `Enum` members by **name**; the loader converts to `.value`.
  Pass plain strings for enum-like columns.
- Schema changes go through Alembic only; `alembic check` (and a test) must stay clean.
- Tests drop/recreate `TEST_POSTGRES_DB` — it must never equal `POSTGRES_DB` (guarded).
- The synthetic "today" is the anchor date **2026-10-01**, not the wall clock.
- Tests must never call an LLM or any external API.
- The embedding model loads with `local_files_only` and a pinned SHA-256; never let code
  download at runtime. Tests marked `model` skip if `.models/` is missing.
- Evidence thresholds (`KB_MIN_SIMILARITY`, `KB_MIN_COVERAGE`) are calibrated on the eval
  **dev** split; after changing documents, chunking or answering, re-run `hotel-kb eval`
  and recalibrate on dev — never tune on the test split.
- Windows consoles default to cp1252; CLI output that may contain `₹` must be UTF-8.
