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
| 5 | LangGraph orchestration | ✅ Complete — TESTED (2026-10-07; deterministic router) |
| 6 | Real LLM integration and agent evaluation | ✅ Complete — TESTED (2026-10-07; deterministic default, local LLM EXPERIMENTAL, not selected) |
| 7 | Make it verifiable (CI, Docker demo, HTTP agent, honest claims, intervals) | ✅ Complete — TESTED (2026-10-07; CI not yet run on GitHub) |
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

## Phase 5 — LangGraph orchestration ✅ (2026-10-07)

Decisions: ADR-0022 (LangGraph layer above existing services; LangSmith tracing forced off,
owner approved), ADR-0023 (LLM interface, deterministic fake, no local LLM yet), ADR-0024
(security invariants, routing policy). Not implemented (out of scope): real LLM, frontend,
cloud, real PMS/payments, Phase 6.

Definition of Done — all items verified (final run 2026-10-07):

- [x] Decision records (ADR-0022..0024); local-model feasibility measured (no download):
      0.45–0.60 GB free RAM vs ≥ 398 MB smallest instruct model + runtime
- [x] LangGraph 1.2.14 installed (+14 MB); LangSmith tracing forced off and test-guarded
- [x] `LLMClient` interface, router prompt, `RouteDecision` schema, deterministic fake and
      scripted (hostile) models
- [x] Graph: route → validate → knowledge | data | tool | clarify | refuse → respond
- [x] Capability adapters reuse ExtractiveAnswerer, SqlPipeline and MCP (ToolGateway);
      static test: graph modules have no DB/SQL/gateway/approval imports
- [x] `hotel-agent` CLI entry point (in-memory or stdio MCP), verified live; audit/trace
- [x] Adversarial tests incl. prompt injection, tool-selection manipulation, approval
      bypass, fake system messages, cross-hotel, ambiguous, unsupported, mixed, SQL-like
      content, malicious tool arguments, hostile model outputs
- [x] Evaluation: 92/92 tuned set (first run 87/92), 21/24 held-out set (run once);
      unauthorized-action prevention 1.00 on both; regression floors in tests
- [x] Measured RAM / disk / latency (ARCHITECTURE §2.12)
- [x] Full regression: 634 tests, ruff, mypy; Phase 2-3 evaluations unchanged; docs
      updated; committed

## Phase 6 — Real LLM integration and agent evaluation ✅ (2026-10-07)

Decisions: ADR-0025 (local Ollama router EXPERIMENTAL, not selected: memory + quality;
deterministic router stays default), ADR-0026 (evaluation methodology). The human approval
workflow originally listed here was delivered in Phase 4 (ADR-0019); an `interrupt()`-based
variant is in the backlog. Not done (out of scope): frontend, cloud, PMS/payments, Phase 7.

Definition of Done — verified 2026-10-07:

- [x] Feasibility measured before adding a model; owner chose a local trial with RAM freed
- [x] Ollama v0.40.0 (SHA-256 verified, outside the repo, 127.0.0.1) + qwen2.5:0.5b measured:
      llama-server peak ~554 MB + ~70 MB; 16–18 tok/s; warm 1.3–5.2 s, cold ~7.8 s;
      ~1,300 prompt tokens/request
- [x] `OllamaLLMClient` behind `LLMClient.complete_json` (EXPERIMENTAL, loopback only);
      deterministic router kept as default, baseline and optional fallback
- [x] Structured-output hardening: invalid JSON, extra fields, invalid routes, missing
      fields, confidence range, non-scalar/oversized arguments, unknown tools, raw size cap,
      clarification leak filter, unknown refusal labels not echoed
- [x] Prompt minimisation (no identity/role/hotel/DB/guard details; tested)
- [x] Frozen held-out v2 (36) and adversarial (30) sets committed before tuning
- [x] Dev-only prompt tuning recorded: 4.8% → 41.0% routing, tool 19/27, invalid 2/83
- [x] Deterministic evaluation on all sets (dev 92/92, held-out v1 21/24, v2 22/36,
      adversarial 18/30; unauthorized-action prevention 1.00; leaks 0; injection 0/30)
- [x] Full regression: pytest, ruff, mypy --strict; docs and ADRs updated; committed
- [ ] **Not achieved:** a valid full Ollama evaluation — the machine ran out of memory and
      the run was stopped; per owner decision it was not retried (no benchmark claimed)

## Phase 7 — Make it verifiable ✅ (2026-10-07)

Decisions: ADR-0027 (HTTP agent endpoint, Docker demo, CI), ADR-0028 (confidence
intervals and claims policy). Scope was deliberately limited by the owner: no external LLM
evaluation, authentication, UI, cloud, larger model or multi-step planning. Local tracing
moved to the backlog.

Definition of Done — verified 2026-10-07:

- [x] `POST /agent/ask` over the existing `HotelAgent`; principal from `AGENT_STAFF_CODE`
      (server config); request cannot set staff/role/hotel; no approval endpoint; tests
      for knowledge, data, read-only tool, mutating → pending, invalid, injection, unknown
      tool, cross-hotel
- [x] Dockerfile + `docker-compose.demo.yml`: one command from a fresh clone; DB not
      published; API on 127.0.0.1; idempotent migrate → seed → ingest → serve; verified
      live including the human approval step and a restart
- [x] GitHub Actions workflow (tests, lint, types, migrations, eval safety gates, Docker
      smoke); every step executed locally on a fresh database; gate script rejects
      tampered reports
- [x] Honest-claims pass (README, PRD, ARCHITECTURE, CLAUDE.md, pyproject); coverage
      re-measured (90%)
- [x] Wilson 95% intervals in KB, SQL and agent reports; regenerated results identical in
      every non-timing value
- [x] Full regression: 720 tests, ruff, mypy --strict; Phase 2/3/5/6 evaluations unchanged
- [ ] **Not verified:** a CI run on GitHub (no remote configured; pushing is the owner's call)

## Phase 8 — Production hardening (PLANNED)

- [ ] Authentication and role-based access
- [ ] Rate limits
- [ ] Local deployment packaging (no cloud deployment without approval)

## Known issues / follow-ups

- Resolved in Phase 5: the Starlette `httpx2` deprecation warning disappeared once
  `httpx2` (BSD-3, verified in Phase 4) arrived as a LangGraph dependency.
- No pre-commit hooks or CI yet (CI requires owner approval per ADR-0002).
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
- Observability: local tracing/metrics (moved from the original Phase 7 plan).
- Agent: evaluate a stronger model behind `LLMClient` only with owner approval and enough
  free memory (or a verified ₹0 option); run the frozen held-out v2 + adversarial sets once
  per configuration. Improve the deterministic router's generalization (held-out v2 0.72,
  adversarial 0.67 routing) — e.g. a semantic router using the existing embedding model —
  without tuning on held-out data; multi-step plans for mixed requests; LangGraph
  `interrupt()` for approvals (with a Postgres checkpointer); hotel-scoped analytics.
- Synthetic data: spread cancellation timestamps more realistically (they cluster before
  the business date today).
- RAG: grow the evaluation set (more paraphrases, more near-topic unanswerables), ideally
  with questions written by someone other than the corpus author.
