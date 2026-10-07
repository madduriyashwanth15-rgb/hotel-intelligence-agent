# Roadmap — Hotel Intelligence Agent

Canonical roadmap. **Only items that were implemented and verified are checked.**
Constraint for every phase: ₹0 cost, local only (ADR-0002). Status labels:
IMPLEMENTED · TESTED · PLANNED · EXPERIMENTAL ([CLAUDE.md §13](../CLAUDE.md#13-truthfulness-rule)).

| Phase | Name | Status |
|---|---|---|
| 1 | Foundations and synthetic data | ✅ Complete — TESTED (2026-10-07) |
| 2 | Grounded RAG | ✅ Complete — TESTED (2026-10-07) |
| 3 | Controlled Text-to-SQL | ✅ Complete — TESTED (2026-10-07) |
| 4 | MCP tool server | ✅ Complete — TESTED (2026-10-07; simulated, no auth) |
| 5 | LangGraph orchestration | IN PROGRESS (started 2026-10-07) |
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

## Phase 2 — Grounded RAG ✅ (2026-10-07)

Approved decisions: ADR-0009 (fastembed + bge-small-en-v1.5), ADR-0010 (extractive answers
only), ADR-0011 (hybrid retrieval), ADR-0012 (Markdown + TOML front matter); ADR-0013
(evidence gate and evaluation methodology). Out of scope and not implemented: LLM answer
generation, Ollama, LangGraph, MCP, Text-to-SQL, frontend, cloud deployment.

Definition of Done — all items verified (final run 2026-10-07):

- [x] Decision records for embeddings, extractive answers, hybrid retrieval, document format
- [x] 15 synthetic documents covering SOPs, cancellation, room info, F&B, emergency,
      service recovery — consistency with Phase 1 room types/rates/times is test-enforced
- [x] Front-matter loader with validation, heading-aware chunker, `Embedder` protocol — tested
- [x] fastembed + pgvector installed (no PyTorch); bge-small-en-v1.5 downloaded once
      (65 MB), pinned revision + SHA-256, `local_files_only` at runtime; 384 dims verified
- [x] Migration 0003 (`kb_documents`, `kb_chunks`); `alembic check` clean; round-trip tested
- [x] `hotel-kb ingest`: idempotent (re-run: 15 unchanged, 0 embedded); changed documents
      replaced transactionally; removed documents pruned
- [x] Vector, full-text and hybrid (RRF) retrieval as the read-only role — tested
- [x] Extractive answerer with citations and `insufficient_evidence` refusal — tested
- [x] `POST /knowledge/ask` + `hotel-kb ask` — tested and verified live
- [x] Evaluation with the real model: Recall@k, MRR, evidence recall, refusal
      precision/recall, latency; vector vs FTS vs hybrid and query-instruction comparison;
      thresholds calibrated on dev, reported on held-out test; regression floors in
      `tests/test_kb_quality.py`
- [x] Measured RAM / disk / latency recorded (ARCHITECTURE §2.9)
- [x] Prompt-injection fixture and input-limit tests
- [x] All checks pass (181 tests, ruff, mypy); docs and roadmap updated; committed

Known Phase 2 limitations are listed in ARCHITECTURE §3 (extractive answers; heuristic
refusal gate with 0.78 held-out refusal recall; small evaluation set).

## Phase 3 — Controlled Text-to-SQL ✅ (2026-10-07)

Approved decisions: ADR-0014 (analytics schema, `hotel_sql`, narrowed `hotel_readonly`),
ADR-0015 (SQL safety pipeline), ADR-0016 (generator interface, deterministic fakes),
ADR-0017 (business date, metric glossary). Not implemented (out of scope): any LLM,
Ollama, LangGraph, MCP, frontend, cloud, persistent audit tables.

Definition of Done — all items verified (final run 2026-10-07):

- [x] Decision records (ADR-0014..0017; ADR-0005 amended)
- [x] Migration 0004: `analytics` schema + 7 views, `hotel_sql`, narrowed `hotel_readonly`;
      `alembic check` clean; round-trip tested; Phase 2 retrieval and evaluation unchanged
- [x] Views verified against reference SQL on raw tables; no PII columns (tested)
- [x] `SqlGenerator` protocol, `RuleBasedFakeGenerator`, `ScriptedFakeGenerator` — tested
- [x] SqlGuard (all ADR-0015 checks); bypass corpus 108/108 blocked, 17 safe queries allowed
- [x] Executor (2 s timeout, row cap 100, rollback, SQLSTATE error mapping); database-layer
      tests bypassing the guard prove `hotel_sql` cannot read `public.guests`, write,
      create objects, access forbidden schemas, change settings or run long queries
- [x] EXPLAIN cost ceiling 250,000 (~10x the most expensive valid query, 24,683)
- [x] Deterministic formatter and refusals (incl. "no posted revenue yet" explanation)
- [x] JSON audit logging with hashed questions, masked SQL, parameter names only — tested
- [x] `POST /data/ask`, `hotel-sql ask` / `hotel-sql eval` — tested and verified live
- [x] Evaluation (90 cases, 11 categories) vs independent reference SQL: safety block rate
      100% (34/34), execution accuracy 100% (34/34), refusal accuracy 100% (51/51),
      false-refusal rate 0, wrong-but-safe detection 4/4; regression floors in tests
- [x] RAM / disk / latency measured (ARCHITECTURE §2.10)
- [x] All checks pass (458 tests, ruff, mypy); docs and roadmap updated; committed

## Phase 4 — MCP tool server ✅ (2026-10-07)

Decisions: ADR-0018 (MCP boundary over a tool gateway, stdio only), ADR-0019 (classification
and human approval), ADR-0020 (principals, authorization, hotel scoping, `hotel_ops` role),
ADR-0021 (refund policy engine). Not implemented (out of scope): LLM agent, LangGraph,
real PMS, external APIs, cloud, authentication.

Definition of Done — all items verified (final run 2026-10-07):

- [x] Decision records (ADR-0018..0021)
- [x] Migration 0005: `staff_members`, `approval_requests`, `refunds`, `hotel_ops` role with
      least privilege; synthetic staff seeded (generator 1.1.0); round-trip tested;
      database-layer tests bypass the gateway
- [x] Tool gateway: registry, strict schemas, READ_ONLY / MUTATING / SENSITIVE classes,
      authorization matrix, hotel scoping, PII minimisation, masked audit log
- [x] Tools: get_reservation, get_room_status, get_occupancy, get_housekeeping_tasks,
      calculate_refund, get_approval_status, create_housekeeping_task,
      request_cancellation_refund
- [x] Human approval workflow (pending → executed/failed/rejected/expired), four-eyes,
      authority limits, argument integrity, exactly-once execution; `hotel-ops` CLI
      verified live
- [x] MCP server (stdio) with in-memory and real stdio-process tests
- [x] Adversarial tests: unauthorized, malformed, cross-hotel, mutation via read tools,
      prompt-injection-style requests, excessive quantities, refund abuse, housekeeping
      abuse, unknown tools, tool chaining — all blocked as designed
- [x] Measured: tool latency, RAM, disk, coverage (91% overall, 94% `ops`), security results
- [x] Full regression: 603 tests, ruff, mypy; Phase 2 and 3 evaluations unchanged; docs
      updated; committed

## Phase 5 — LangGraph orchestration (IN PROGRESS)

Decisions: ADR-0022 (LangGraph layer above existing services; LangSmith tracing disabled),
ADR-0023 (LLM interface, deterministic fake, no local LLM yet), ADR-0024 (security
invariants, routing policy). Out of scope: frontend, cloud, real PMS/payments, Phase 6.

Definition of Done:

- [x] Decision records (ADR-0022..0024); local-model feasibility measured (no download)
- [ ] LangGraph installed; LangSmith tracing forced off and test-guarded
- [ ] `LLMClient` interface, router prompt, `RouteDecision` schema, deterministic fake and
      scripted (hostile) models
- [ ] Graph: ingest → route → validate → knowledge | data | tool | clarify | refuse → respond
- [ ] Capability adapters reuse ExtractiveAnswerer, SqlPipeline and MCP (ToolGateway)
- [ ] `hotel-agent` CLI entry point; audit/trace events
- [ ] Adversarial tests: prompt injection, tool-selection manipulation, approval bypass,
      fake system messages, cross-hotel, ambiguous, unsupported, mixed, malicious SQL-like
      content, malicious tool arguments; hostile-model tests for the invariant
- [ ] Evaluation set + runner: routing accuracy, refusal, tool selection, RAG/SQL/MCP
      routing, clarification rate, unauthorized-action prevention (must be 100%)
- [ ] Measured RAM / disk / latency
- [ ] Full regression (pytest, ruff, mypy); docs updated; committed

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

- Starlette emits a deprecation warning recommending `httpx2` for `TestClient`. httpx2 was
  verified in Phase 4 as a legitimate BSD-3 package (httpx author, maintained by Pydantic)
  but is not installed; switching the test client is optional future work.
- No pre-commit hooks or CI yet (CI requires owner approval per ADR-0002).
- Git repository initialised but **no commits yet**; awaiting owner instruction to commit.
- Docker Desktop licence applicability not yet confirmed by the owner (ADR-0002).

## Ideas / backlog (not scheduled, not implemented)

Record future ideas here instead of implementing them early.

- Room-status history table (status changes over time) if tools/analytics need it.
- Composite FK ensuring a room's type belongs to the same hotel (relevant only for multi-property).
- Lightweight local trace viewer for Phase 7.
- RAG: local LLM to phrase extractive answers (needs owner approval and RAM headroom).
- RAG: cross-encoder re-ranking or synonym expansion to reduce paraphrase refusals.
- Text-to-SQL: LLM SQL generator behind `SqlGenerator` (needs owner approval); evaluate it
  with the existing 90-case set, keeping the 100% safety gate.
- Text-to-SQL: persistent audit table (deferred from Phase 3 by decision).
- Text-to-SQL: non-superuser owner for analytics views (production hardening).
- Tools: authentication for MCP/CLI principals (Phase 8); approval UI or HTTP endpoints;
  persistent per-principal rate limits; tamper-evident audit store.
- Tools: unit tests for the `hotel-kb` / `hotel-sql` CLIs (currently verified live only).
- Synthetic data: spread cancellation timestamps more realistically (they cluster before
  the business date today).
- RAG: grow the evaluation set (more paraphrases, more near-topic unanswerables), ideally
  with questions written by someone other than the corpus author.
