# Roadmap — Hotel Intelligence Agent

Canonical roadmap. **Only items that were implemented and verified are checked.**
Constraint for every phase: ₹0 cost, local only (ADR-0002). Status labels:
IMPLEMENTED · TESTED · PLANNED · EXPERIMENTAL ([CLAUDE.md §13](../CLAUDE.md#13-truthfulness-rule)).

| Phase | Name | Status |
|---|---|---|
| 1 | Foundations and synthetic data | ✅ Complete — TESTED (2026-10-07) |
| 2 | Grounded RAG | IN PROGRESS (started 2026-10-07) |
| 3 | Controlled Text-to-SQL | PLANNED — not started |
| 4 | MCP tool server | PLANNED — not started |
| 5 | LangGraph orchestration | PLANNED — not started |
| 6 | Human approval for sensitive actions | PLANNED — not started |
| 7 | Evaluation and observability hardening | PLANNED — not started |
| 8 | Production hardening (auth/RBAC, rate limits) | PLANNED — not started |

Evaluation and tests grow inside every phase, not only in Phase 7.

## Phase 1 — Foundations and synthetic data ✅ (2026-10-07)

Definition of Done — all items verified on a clean rebuild:

- [x] Git repository with `.gitignore` / `.gitattributes`; `.env` ignored; no secrets tracked
- [x] `pyproject.toml` + `uv.lock`; `uv sync --locked` succeeds
- [x] Typed settings from environment; `.env.example` documents every setting (test-enforced)
- [x] `docker compose up -d --wait`: PostgreSQL 16.12 + pgvector 0.8.1, healthcheck `healthy`, bound to 127.0.0.1
- [x] Alembic migrations: 0001 core schema (+ `vector`, `btree_gist`), 0002 read-only role
- [x] Models match migrations (`alembic check` clean, test-enforced); downgrade/upgrade round-trip tested
- [x] Deterministic synthetic data generator (Grand Hills Resort) + `hotel-seed` CLI
- [x] FastAPI app: `/health` (liveness), `/health/ready` (DB + schema revision)
- [x] Structured JSON logging with `X-Request-ID` correlation
- [x] pytest (92 tests, incl. real-DB tests), ruff, mypy --strict all passing
- [x] README, architecture doc, decision records

Post-phase housekeeping (documentation only, no code changes):

- [x] Project brain: `CLAUDE.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`,
      `docs/DECISIONS.md`, `docs/DEVELOPMENT_RULES.md` (2026-10-07)

## Phase 2 — Grounded RAG (IN PROGRESS)

Approved decisions: ADR-0009 (fastembed + bge-small-en-v1.5), ADR-0010 (extractive answers
only), ADR-0011 (hybrid retrieval), ADR-0012 (Markdown + TOML front matter).
Out of scope for Phase 2: LLM answer generation, Ollama, LangGraph, MCP, Text-to-SQL,
frontend, cloud deployment.

Definition of Done:

- [x] Decision records for embeddings, extractive answers, hybrid retrieval, document format
- [ ] ~15 synthetic documents covering SOPs, cancellation, room info, F&B, emergency,
      service recovery — consistent with Phase 1 data (room types, rates, hotel name)
- [ ] Front-matter loader with validation, heading-aware chunker, `Embedder` protocol — tested
- [ ] fastembed + pgvector installed; bge-small-en-v1.5 downloaded once, pinned, offline at
      runtime; dimensions / RAM / latency measured
- [ ] Migration 0003 (`kb_documents`, `kb_chunks`); `alembic check` clean; round-trip tested
- [ ] `hotel-kb ingest`: idempotent; changed documents replaced transactionally
- [ ] Vector, full-text and hybrid (RRF) retrieval as the read-only role — tested
- [ ] Extractive answerer with citations and `insufficient_evidence` refusal — tested
- [ ] `POST /knowledge/ask` + `hotel-kb ask` — tested and verified live
- [ ] Evaluation with the real model: Recall@k, MRR, refusal precision/recall, latency;
      vector vs FTS vs hybrid comparison; threshold calibrated; regression floor set
- [ ] Measured RAM / disk / latency recorded
- [ ] Prompt-injection fixture and input-limit tests
- [ ] All checks pass (pytest, ruff, mypy); docs and roadmap updated; committed

## Phase 3 — Controlled Text-to-SQL (PLANNED)

- [ ] Curated views; narrow read-only role to views (exclude guest contact details)
- [ ] SQL guard (sqlglot AST: single `SELECT`, allowlisted relations, row limit)
- [ ] Execution as read-only role; query audit log
- [ ] Eval set of question → expected result (tied to seed 42 / anchor 2026-10-01 /
      generator version)

## Phase 4 — MCP tool server (PLANNED)

- [ ] Service layer for hotel operations (plain Python, independently tested)
- [ ] Read tools: `get_reservation`, `get_room_status`, `get_occupancy`
- [ ] Write tool: `create_housekeeping_task` (validated inputs, audit log)
- [ ] `calculate_refund` (depends on the Phase 2 cancellation policy)
- [ ] MCP server exposing the tools with typed schemas

## Phase 5 — LangGraph orchestration (PLANNED)

- [ ] Router: knowledge (RAG) vs data (SQL) vs action (tools)
- [ ] `/chat` endpoint
- [ ] Routing eval set

## Phase 6 — Human approval for sensitive actions (PLANNED)

- [ ] `interrupt()`-based approval with Postgres checkpointer
- [ ] Approve / reject endpoints; approvals survive restart (tested)
- [ ] Audit trail of requested / approved / rejected actions

## Phase 7 — Evaluation and observability hardening (PLANNED)

- [ ] End-to-end eval harness with regression thresholds
- [ ] Local tracing (no hosted service without approval)
- [ ] CI (requires owner approval — GitHub Actions minutes may be limited/billable)

## Phase 8 — Production hardening (PLANNED)

- [ ] Authentication and role-based access
- [ ] Rate limits
- [ ] Local deployment packaging (no cloud deployment without approval)

## Known issues / follow-ups

- Starlette emits a deprecation warning recommending `httpx2` for `TestClient`. Not acted
  on: the package is unverified; revisit when FastAPI/Starlette guidance settles.
- No pre-commit hooks or CI yet (CI requires owner approval per ADR-0002).
- Git repository initialised but **no commits yet**; awaiting owner instruction to commit.
- Docker Desktop licence applicability not yet confirmed by the owner (ADR-0002).

## Ideas / backlog (not scheduled, not implemented)

Record future ideas here instead of implementing them early.

- Room-status history table (status changes over time) if tools/analytics need it.
- Composite FK ensuring a room's type belongs to the same hotel (relevant only for multi-property).
- Lightweight local trace viewer for Phase 7.
