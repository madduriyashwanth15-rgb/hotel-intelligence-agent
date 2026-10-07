# Architecture — Hotel Intelligence Agent

Canonical architecture document. Status labels: IMPLEMENTED · TESTED · PLANNED ·
EXPERIMENTAL (see [CLAUDE.md §13](../CLAUDE.md#13-truthfulness-rule)).
Decisions referenced as ADR-NNNN live in [DECISIONS.md](DECISIONS.md).

## 1. Target architecture — PLANNED

```
User
 → FastAPI (/chat, /approvals, /health)
 → LangGraph agent ── Postgres checkpointer ── human approval (interrupt/resume)
 → RAG / Text-to-SQL / Tools (via MCP)
 → PostgreSQL / pgvector / MCP server
 → Grounded response (with citations / query provenance)
```

```
Client ─► FastAPI (/chat, /approvals, /health)
            │
            ▼
       LangGraph agent  ── Postgres checkpointer ── human approval (interrupt/resume)
        │       │        │
        ▼       ▼        ▼
      RAG    Text-to-SQL   MCP client ─► MCP server (hotel ops tools)
        │       │                          │
        ▼       ▼                          ▼
   pgvector   Postgres (read-only role,   Postgres (writes via service layer)
              curated views)
```

Principles (status in brackets):

- Embeddings run **locally** (no paid APIs) — ADR-0002/0009 [TESTED: fastembed +
  bge-small-en-v1.5]. No LLM yet; any future LLM must also be local unless the owner
  approves otherwise [PLANNED].
- **RAG:** answers cite chunk/document IDs; weak evidence → refuse rather than guess;
  retrieved text is treated as data, never as instructions [TESTED — extractive answers,
  §2.9].
- **Text-to-SQL:** generators see curated views only; generated SQL is parsed and
  validated (single `SELECT`, allowlisted relations/functions, row limit); executed as the
  `hotel_sql` role with a statement timeout; every query is logged [TESTED with a
  deterministic fake generator, §2.10; LLM generator PLANNED].
- **Tools behind MCP:** typed schemas; read vs write classification; write tools go
  through human approval; tool logic lives in a plain service layer so MCP is a thin
  wrapper [TESTED, §2.11 — simulated operations, no authentication yet].
- **Orchestration:** LangGraph routes each request to knowledge, data or tools; the model
  is an orchestrator, never the security boundary [TESTED with a deterministic router
  model, §2.12; no real LLM yet].
- **Human approval:** pending approvals are durable rows in PostgreSQL decided through the
  human channel [TESTED, §2.11]; LangGraph `interrupt()` integration remains PLANNED.
- **Tests never call an LLM**; real-model evaluation runs as a separate suite.

## 2. Current architecture (Phases 1–6) — TESTED

```
uvicorn ─► FastAPI app (create_app factory)
             ├─ RequestContextMiddleware  (X-Request-ID, JSON request log)
             └─ /health, /health/ready ─► SQLAlchemy engine ─► PostgreSQL 16 + pgvector
                                                               (Docker, 127.0.0.1 only)
             └─ POST /knowledge/ask ─► ExtractiveAnswerer ─► Retriever ─► PostgreSQL
                                          (read-only role)       (kb_chunks: pgvector + tsvector)
hotel-seed CLI ─► generator.generate(seed, anchor_date) ─► loader (single transaction)
             └─ POST /data/ask ─► SqlPipeline: fake generator ─► SqlGuard ─► SqlExecutor
                                    (hotel_sql role, analytics.* views only)
hotel-kb CLI   ─► download-model | ingest | ask | eval
hotel-sql CLI  ─► ask | eval
hotel-agent (LangGraph) ─► route ─► validate ─┬─ knowledge ─► ExtractiveAnswerer
                                              ├─ data      ─► SqlPipeline (SqlGuard, hotel_sql)
                                              ├─ tool      ─► MCP session ─► hotel-mcp ─► ToolGateway
                                              └─ clarify / refuse ──────────────► respond
hotel-mcp (MCP stdio) ─► ToolGateway ─► tools (hotel_ops role) ─► PostgreSQL
hotel-ops CLI (human) ─► ApprovalService ─► executes approved mutations exactly once
alembic ─► migrations/ (0001 core, 0002 read-only, 0003 KB, 0004 analytics, 0005 ops)
```

### 2.1 Code map

| Path | Responsibility | Status |
|---|---|---|
| `src/hotel_agent/config.py` | Typed settings from env / `.env` | TESTED |
| `src/hotel_agent/logging_config.py` | JSON log formatter, `request_id` context var | TESTED |
| `src/hotel_agent/api/app.py` | `create_app()` factory, engine lifecycle | TESTED |
| `src/hotel_agent/api/health.py` | `/health`, `/health/ready` | TESTED |
| `src/hotel_agent/api/middleware.py` | Request-ID + request logging | TESTED |
| `src/hotel_agent/db/models.py` | SQLAlchemy 2 models, enum value sets | TESTED |
| `src/hotel_agent/db/session.py` | Engine factory, Alembic config, revision helpers | TESTED |
| `src/hotel_agent/synthetic/generator.py` | Pure deterministic dataset generator | TESTED |
| `src/hotel_agent/synthetic/seed.py` | Loader + `hotel-seed` CLI | TESTED |
| `src/hotel_agent/api/knowledge.py` | `POST /knowledge/ask` | TESTED |
| `src/hotel_agent/knowledge/documents.py` | Markdown + TOML front-matter loader/validation | TESTED |
| `src/hotel_agent/knowledge/chunking.py` | Heading-aware chunker | TESTED |
| `src/hotel_agent/knowledge/embeddings.py` | `Embedder` protocol, fastembed (pinned), hashing test embedder | TESTED |
| `src/hotel_agent/knowledge/ingest.py` | Idempotent ingestion into `kb_documents` / `kb_chunks` | TESTED |
| `src/hotel_agent/knowledge/retrieval.py` | Vector, full-text and hybrid (RRF) retrieval | TESTED |
| `src/hotel_agent/knowledge/answer.py` | Evidence gate + extractive answers with citations | TESTED |
| `src/hotel_agent/knowledge/evaluation.py` | Retrieval metrics, threshold calibration, end-to-end scoring | TESTED |
| `src/hotel_agent/knowledge/cli.py` | `hotel-kb` command line | TESTED |
| `src/hotel_agent/api/data.py` | `POST /data/ask` | TESTED |
| `src/hotel_agent/sql/guard.py` | SqlGuard: allowlist validation of generated SQL | TESTED |
| `src/hotel_agent/sql/executor.py` | Read-only execution as `hotel_sql`, timeout, cost ceiling, row cap | TESTED |
| `src/hotel_agent/sql/context.py` | SchemaContext: views, glossary, allowed values | TESTED |
| `src/hotel_agent/sql/generators.py` | `SqlGenerator` protocol, rule-based and scripted fakes | TESTED |
| `src/hotel_agent/sql/pipeline.py` | generate → guard → execute → summarize → audit log | TESTED |
| `src/hotel_agent/sql/formatting.py` | Deterministic answer sentences | TESTED |
| `src/hotel_agent/sql/evaluation.py` | Text-to-SQL evaluation vs reference SQL | TESTED |
| `src/hotel_agent/sql/cli.py` | `hotel-sql` command line | TESTED (live) |
| `src/hotel_agent/ops/principal.py` | Staff principals, business date/now | TESTED |
| `src/hotel_agent/ops/schemas.py` | Strict tool argument schemas | TESTED |
| `src/hotel_agent/ops/tools.py` | Tool registry, classification, implementations | TESTED |
| `src/hotel_agent/ops/refund.py` | POL-CANCEL-001 refund policy engine | TESTED |
| `src/hotel_agent/ops/gateway.py` | ToolGateway: authorization, validation, approvals requests, audit | TESTED |
| `src/hotel_agent/ops/approvals.py` | Human approval service (four-eyes, authority, exactly once) | TESTED |
| `src/hotel_agent/ops/audit.py` | Masked audit events | TESTED |
| `src/hotel_agent/ops/mcp_server.py` | `hotel-mcp` stdio MCP server | TESTED |
| `src/hotel_agent/ops/cli.py` | `hotel-ops` human approval CLI | TESTED |
| `src/hotel_agent/agent/schemas.py` | `RouteDecision` structured output (no auth/SQL/identity fields) | TESTED |
| `src/hotel_agent/agent/llm.py` | Provider-neutral `LLMClient`, `ScriptedLLM` | TESTED |
| `src/hotel_agent/agent/router.py` | Prompt, model call, schema validation → refusal on failure | TESTED |
| `src/hotel_agent/agent/fake_llm.py` | `DeterministicRouterLLM` (rule-based; default router) | TESTED |
| `src/hotel_agent/agent/ollama_llm.py` | `OllamaLLMClient` (local, loopback only) | EXPERIMENTAL (unit-tested; not selected) |
| `src/hotel_agent/agent/router_prompt_v3.txt` | Router system prompt (dev-tuned) | TESTED |
| `src/hotel_agent/agent/capabilities.py` | Knowledge/Data/Tool interfaces + adapters over existing services | TESTED |
| `src/hotel_agent/agent/graph.py` | LangGraph `StateGraph` | TESTED |
| `src/hotel_agent/agent/service.py` | `HotelAgent` entry point + audit event | TESTED |
| `src/hotel_agent/agent/factory.py` | Composition root (engines, MCP memory/stdio sessions) | TESTED |
| `src/hotel_agent/agent/evaluation.py` | Routing + safety evaluation | TESTED |
| `src/hotel_agent/agent/cli.py` | `hotel-agent` CLI | TESTED (live) |
| `data/knowledge_base/` | 15 synthetic hotel documents | — |
| `eval/` | Evaluation set and saved results | — |
| `migrations/` | Alembic environment and revisions | TESTED |
| `tests/` | pytest suite (92 / 181 / 458 / 603 / 634 after Phases 1–5) | — |

### 2.2 Runtime infrastructure

- **PostgreSQL 16.12 + pgvector 0.8.1**, image `pgvector/pgvector:0.8.1-pg16`, run by
  `docker-compose.yml`; port bound to `127.0.0.1` only; named volume `pgdata`;
  `pg_isready` healthcheck.
- Extensions created by migration 0001: `btree_gist` (exclusion constraint), `vector`
  (used by `kb_chunks.embedding` since Phase 2).
- Embedding model: `BAAI/bge-small-en-v1.5` (quantized ONNX, Hugging Face repo
  `Qdrant/bge-small-en-v1.5-onnx-Q`, revision `aa8f8b06…`, SHA-256 `51f1bd0a…`), cached in
  the git-ignored `.models/` directory (65 MB). Downloaded once with
  `hotel-kb download-model`; loaded with `local_files_only` and hash-verified at runtime.

### 2.3 Configuration

`hotel_agent.config.Settings` (pydantic-settings) reads environment variables and `.env`.
Credentials have no defaults; passwords are `SecretStr`; URLs are built with
`sqlalchemy.engine.URL.create` so special characters are escaped. Every setting is
documented in `.env.example` (enforced by a test). `POSTGRES_HOST` defaults to
`127.0.0.1` (ADR-0007).

### 2.4 Database schema

| Table | Purpose |
|---|---|
| `hotels` | The property (one row: Grand Hills Resort). |
| `room_types` | Sellable categories, max occupancy, base rate (INR). |
| `rooms` | Physical rooms and current `housekeeping_status`. |
| `guests` | Synthetic guest profiles. |
| `reservations` | Bookings: dates, status, channel, rate per night, total. |
| `folio_charges` | Posted charges per night (room, F&B, spa, minibar, laundry) — revenue source. |
| `housekeeping_tasks` | Cleaning / turndown / inspection / maintenance tasks per room. |
| `dataset_metadata` | Provenance key/values (`synthetic=true`, seed, generator version, load time). |
| `kb_documents` | Knowledge-base documents (doc_key, title, category, version, hash); `CHECK (is_synthetic)`. |
| `kb_chunks` | Chunks: section path, content, `vector(384)` embedding, model name, generated weighted `tsvector` (GIN index). |

Integrity is enforced in the database, not only in code (ADR-0004):

- **No double booking:** GiST exclusion constraint on
  `(room_id, daterange(check_in_date, check_out_date))` for active statuses
  (`confirmed`, `checked_in`, `checked_out`). Requires `btree_gist`.
- Enum-like columns are `VARCHAR` + `CHECK` (easier to evolve than native enums).
- Cross-column rules: `check_out_date > check_in_date`; `cancelled_at` set iff
  `status = 'cancelled'`; `completed_at` set iff task `done`.

Domain semantics:

- **Business date:** the dataset's anchor date (default 2026-10-01) is "today". Stays
  before it are `checked_out`/`no_show`; stays spanning it are `checked_in`; arrivals on
  or after it are `confirmed`.
- **Rates:** `room_types.base_rate` is the rack rate; `reservations.rate_per_night` is the
  sold rate (season, weekend and channel adjusted). There is no separate rate-plan table.
- **Revenue:** `reservations.total_amount` is the quoted room total; actual revenue is the
  sum of `folio_charges` for consumed nights.
- **Room status:** stored as `rooms.housekeeping_status` (clean / dirty / inspected /
  out_of_order). Occupancy is derived from reservations, not stored.

### 2.5 Database roles (ADR-0005, amended by ADR-0014)

| Role | Login | Can read | Used by |
|---|---|---|---|
| `hotel_admin` (POSTGRES_USER) | yes | everything (owner, superuser in the dev container) | migrations, seeding, ingestion, eval reference SQL |
| `hotel_readonly` → `hotel_reader` | `hotel_reader` | `kb_documents`, `kb_chunks` only | knowledge retrieval (`/knowledge/ask`) |
| `hotel_analytics` → `hotel_sql` | `hotel_sql` | the 7 `analytics` views only | Text-to-SQL (`/data/ask`) |
| `hotel_ops_group` → `hotel_ops` | `hotel_ops` | tables tools need; `guests` only `id, first_name, last_name, loyalty_tier`; INSERT on tasks/approvals/refunds; column-limited UPDATE; no DELETE/DDL | MCP tool gateway, human approvals |

Login roles default to read-only transactions and short statement timeouts (`hotel_reader`
5 s, `hotel_sql` 3 s plus `search_path = analytics`, connection limit 5). `PUBLIC` has no
`USAGE`/`CREATE` on schema `public`. Writes fail on privileges even if a session switches to
read-write (tested for both roles).

### 2.6 API

| Endpoint | Behaviour |
|---|---|
| `GET /health` | Liveness; never touches dependencies. |
| `GET /health/ready` | 200 only if the DB is reachable **and** migrated to Alembic head; otherwise 503 with details. |
| `POST /knowledge/ask` | `{question, mode?, category?}` → `{status, answer, citations[], evidence, latency_ms}`; 422 on invalid input; 503 if the read-only role or model is unavailable. |

Engine connect timeout is 3 s so readiness fails fast instead of hanging.

### 2.6b Text-to-SQL API

`POST /data/ask` `{question}` → `{status: answered|refused|error, code, answer,
clarification, data: {columns, rows, row_count, truncated}, query: {label, sql,
parameters}, business_date, generator, latency_ms}`. The natural-language `answer` is
separate from `query.sql` (labelled "Generated and executed SQL"). Refusals return 200 with
a stable `code`; malformed requests 422; 503 if `SQL_DB_PASSWORD` is not configured.

### 2.7 Logging and request correlation

JSON lines on stdout: timestamp, level, logger, message, `request_id` (context variable
set by the middleware) and any `extra=` fields. A caller-supplied `X-Request-ID` is reused
only if it matches `^[A-Za-z0-9._-]{1,64}$`; otherwise a new UUID hex is generated (prevents
log injection). Uvicorn's access log is disabled in favour of our request log.

### 2.8 Synthetic data

See ADR-0003. `generate(seed, anchor_date)` is pure and deterministic; the loader inserts
in one transaction, refuses a non-empty database without `--reset`, and advances ID
sequences past the explicit IDs.

### 2.9 Knowledge base and grounded answers (Phase 2)

Pipeline: `data/knowledge_base/*.md` → validated `KnowledgeDocument` (ADR-0012) →
heading-aware chunks (≤300 estimated tokens; the estimate is verified ≥ the real tokenizer
count for every chunk) → embeddings of `section_path + content` → `kb_chunks`.
Ingestion skips unchanged documents (same hash and model), replaces changed ones, prunes
removed ones, and writes in one transaction.

Retrieval (ADR-0011), as the read-only role:
- **vector:** cosine distance with pgvector, exact scan (96 chunks today);
- **fts:** query words OR-ed into `to_tsquery('english', …)`, ranked by `ts_rank_cd`
  (section headings weighted A, body B);
- **hybrid (default):** top-20 of each fused with Reciprocal Rank Fusion (k = 60), top 5.
- Queries: 1–500 characters, `top_k` 1–20, optional category; bound parameters only.

Answering (ADR-0010, ADR-0013):
1. Evidence gate: answer only if top vector similarity ≥ `KB_MIN_SIMILARITY` (0.58) **and**
   IDF-weighted coverage of the query's content stems by the top-3 chunks ≥
   `KB_MIN_COVERAGE` (0.45). Otherwise `insufficient_evidence` ("I don't know").
2. Extraction: chunk text is split into sentences / list items, each scored against the
   query (embedded with its section path); up to 3 units within 0.08 of the best are
   returned verbatim, each followed by its citation `[DOC-KEY#chunk_index]`.
3. Citations are validated to belong to the retrieved chunks.

Measured on this laptop (i5-8250U, 2 embedding threads): model load 0.5 s; first API
answer 1.7 s (lazy model load), then ~0.2–0.35 s per answered question and ~40 ms per
refusal; ingestion of 15 docs / 96 chunks 9.8 s; peak RAM ~280–290 MB (CLI and API).

Evaluation (`hotel-kb eval`, results in `eval/results/kb_eval.json`): 83 questions
(64 answerable with verbatim evidence, 19 unanswerable incl. operational-data and
adversarial), split dev/test. Thresholds and answer parameters were tuned on dev only;
test is held out. Key held-out results: hybrid doc Recall@5 1.00, evidence Recall@5 1.00,
MRR@10 0.94; end-to-end answer rate 0.81, evidence-in-answer 0.75, refusal recall 0.78.
Regression floors are enforced by `tests/test_kb_quality.py`.

### 2.10 Controlled Text-to-SQL (Phase 3)

Analytics views (schema `analytics`, migration 0004, ADR-0014): `business_date`,
`room_types`, `rooms`, `reservations` (no guest identity or confirmation code),
`daily_occupancy`, `daily_revenue`, `housekeeping_tasks` (no notes). Owned by the admin
role; `hotel_sql` has no rights on base tables. Verified against reference SQL on raw tables.

Pipeline (ADR-0015/0016):
1. **Generator** (`RuleBasedFakeGenerator`, deterministic, ~25 question types) → SQL +
   bound parameters, or a refusal (`mutation_requested`, `ambiguous_question`,
   `unsupported_question`). Relative periods come from `analytics.business_date`.
2. **SqlGuard** (sqlglot 30.21, allowlist): single SELECT/set operation; every AST node,
   function and cast type allowlisted; relations limited to the 7 views or in-query CTEs
   (Postgres identifier folding); no DML/DDL anywhere (incl. data-modifying CTEs), no
   SELECT INTO / locks / recursive CTEs / table functions / wall-clock functions; named
   parameters typed and validated; complexity limits (600 nodes, 6 joins, depth 4, JOIN
   needs ON); outer LIMIT forced to 101; SQL re-rendered canonically (comments dropped).
3. **Executor**: `hotel_sql`, `SET TRANSACTION READ ONLY`, `SET LOCAL statement_timeout =
   2000`, `search_path = analytics`, `EXPLAIN` cost ceiling 250,000 (~10× the most
   expensive valid query), fetch ≤ 101 rows (100 returned + truncated flag), always
   rollback; errors mapped by SQLSTATE without leaking database messages.
4. **Formatter**: deterministic sentences per intent; revenue answers explain that no
   nights are posted on/after the business date (October-to-date revenue is ₹0).
5. **Audit log** (`hotel_agent.sql.audit`, JSON): question SHA-256 prefix, generator,
   intent, status/code/rule, SQL with literals masked + SQL hash, parameter names only,
   row count, cost, latency. No question text, no literal values.

Evaluation (`hotel-sql eval`, `eval/sql_cases.json`, results `eval/results/sql_eval.json`):
90 cases in 11 categories; answered cases compared to independent reference SQL on the raw
tables run as the admin role. Results: safety block rate 34/34 (100%), execution accuracy
34/34, false-refusal rate 0, refusal accuracy 51/51, wrong-but-safe detection 4/4. Guard
bypass corpus: 108 unsafe statements blocked (100%), 17 safe ones allowed.

Measured: answered questions ~30–60 ms end to end (live API), refusals < 3 ms; evaluation
process peak RAM 87 MB; API process ~100 MB without the embedding model loaded; sqlglot
adds 3 MB to the venv.

### 2.11 MCP tool server (Phase 4)

Layers (ADR-0018): `hotel-mcp` (official MCP SDK, low-level `Server`, **stdio only**,
SDK input validation off) → `ToolGateway` → tool implementations → PostgreSQL as
`hotel_ops`. The human approval channel (`hotel-ops approvals …` → `ApprovalService`) is a
separate process path and is **not** exposed via MCP.

| Tool | Class | Roles allowed | Notes |
|---|---|---|---|
| `get_reservation` | READ_ONLY | front desk, managers | code **and** last name; returns initials + loyalty tier only |
| `get_room_status` | READ_ONLY | front desk, housekeeping, managers | no guest data |
| `get_occupancy` | READ_ONLY | front desk, managers | date within ±365 days |
| `get_housekeeping_tasks` | READ_ONLY | front desk, housekeeping, managers | max 50; no notes |
| `calculate_refund` | READ_ONLY, SENSITIVE | front desk, managers | quote only (POL-CANCEL-001) |
| `get_approval_status` | READ_ONLY | all (own requests; managers any in hotel) | |
| `create_housekeeping_task` | MUTATING | front desk, housekeeping, supervisor, FOM, DM | approver: supervisor, DM or FOM |
| `request_cancellation_refund` | MUTATING, SENSITIVE | front desk, managers | approver: FOM/DM/GM ≤ ₹50,000; GM above; amount server-computed |

Gateway checks per call: known tool → principal still active → role permitted → lookup
lockout (5 failed lookups/session) / pending cap (10/session) → strict pydantic
validation (`extra="forbid"`, no value echo in errors) → hotel-scoped execution. READ_ONLY
tools run in `SET TRANSACTION READ ONLY`; MUTATING tools only insert a pending
`approval_requests` row (30-minute expiry, arguments + SHA-256, required approver roles).

Approval (ADR-0019): same hotel, pending and unexpired, approver active, approver ≠
requester (also a DB CHECK), approver role in the stored authority set, argument hash
intact → execute inside the same transaction with re-validation (duplicate tasks, refund
recomputation, one refund per reservation) → `executed` or `failed`, exactly once.

Audit (`hotel_agent.ops.audit`, JSON): `tool.requested` (masked arguments),
`tool.authorization` (allowed/denied + reason), `approval.created`, `approval.decided`,
`approval.executed`, `approval.expired`, `approval.decision_refused`, `tool.result`
(code, latency). Guest names, notes, unknown argument values and full confirmation codes
are never logged.

Measured (dev DB, this laptop): read tools p50 17–24 ms (p95 ≤ 33 ms); rejected calls
< 9 ms; creating an approval request ~31 ms; approve + execute 31–36 ms (p95 ≤ 55 ms);
MCP in-memory round trip 23.5 ms; `hotel-mcp` process ~96 MB RSS; benchmark process peak
99 MB. `mcp` + `pytest-cov` added 32 MB to the venv. Coverage: 91% overall, 94% for `ops`.

### 2.12 Agent orchestration (Phase 5)

Placement (ADR-0022): `hotel_agent/agent/` sits above the existing services. The graph
modules import no database, SQL-execution, gateway or approval code (static test).

LangGraph `StateGraph` (state: `question`, `request_sha256`, `decision`, `outcome`,
`response`, `trace` [append-only]):

| Node | Does | Security role |
|---|---|---|
| `route` | builds prompt (tool catalog from MCP `list_tools`), calls `LLMClient`, validates JSON against `RouteDecision` | invalid/failed output → REFUSAL |
| `validate` | tool name must exist in the MCP catalog | unknown tool → REFUSAL (gateway would reject too) |
| `knowledge` | `ExtractiveAnswerer.answer(question)` | Phase 2 evidence gate |
| `data` | `SqlPipeline.ask(question)` — the router never writes SQL | Phase 3 SqlGuard + `hotel_sql` |
| `tool` | MCP `call_tool(name, arguments)` with arguments unchanged | Phase 4 ToolGateway: RBAC, scoping, validation, approvals |
| `clarify` / `refuse` | deterministic messages | — |
| `respond` | status: answered, insufficient_evidence, clarification, pending_approval, refused, error | reports the gateway's decision verbatim |

Routes: KNOWLEDGE, DATA, TOOL, CLARIFICATION, REFUSAL. Policy: one capability per request
(mixed → CLARIFICATION); missing identifiers → CLARIFICATION; PII, out-of-scope, SQL text,
"approve/execute" requests, other-hotel requests and unsupported actions → REFUSAL.

LLM: `LLMClient.complete_json(system, user, json_schema) -> str`. Phase 5 uses
`DeterministicRouterLLM` (rules; reuses the Phase 3 rule generator to recognise data
questions). No local LLM (ADR-0023: 0.45–0.60 GB free RAM measured vs ≥ 398 MB smallest
instruct model plus runtime). LangSmith tracing forced off at import.

Session binding: `agent_session(resources, staff_code)` loads the principal and builds an
MCP session to a `hotel-mcp` server for that principal (in-memory or real stdio
subprocess). The model cannot change staff, role or hotel.

Audit: `hotel_agent.agent.audit` event `agent.request` (request hash, staff, role, model,
route, reason, tool, masked arguments, gateway authorization code, approval id/status,
final status/code, latency) plus a per-node `trace` in the response; gateway and SQL audit
events remain as in Phases 3-4.

Evaluation (`hotel-agent eval`): 93 cases (17 categories incl. 10 hostile-model scripts) —
first run 87/92 passed (routing 0.963), after fixing five routing rules 92/92 (routing,
tool selection, refusal correctness, clarification precision 1.00). Held-out 24 cases
written afterwards and run once: 21/24 (routing 0.875, tool selection 0.857). Unauthorized
-action prevention (database snapshot unchanged per case) **1.00 on all sets**.

Measured (dev DB, real embedding model, in-memory MCP): imports 3.1 s; resources + MCP
session 0.18 s; first knowledge query 1.9 s (model load); warm p50 — knowledge 239 ms,
data 27 ms, tool 24–26 ms, mutating→pending 42 ms, clarification/refusal ~3 ms; peak RSS
327 MB; stdio MCP session + one call ~2.3 s. LangGraph added 14 MB to the venv.

### 2.13 Model routers and evaluation (Phase 6)

The architecture supports a real LLM; **the default router is deterministic**; local LLM
support is **EXPERIMENTAL**; **no production LLM has been selected** (ADR-0025).

```
Router(llm, fallback?) ─► llm.complete_json(SYSTEM_PROMPT, catalog + REQUEST_JSON line, schema)
   │  LLM_PROVIDER=deterministic ─► DeterministicRouterLLM            (default)
   │  LLM_PROVIDER=ollama        ─► OllamaLLMClient ─► 127.0.0.1:11434 (EXPERIMENTAL)
   ├─ reply > 4,000 chars / invalid JSON / schema violation ─► invalid_model_output
   ├─ LLMError (HTTP, timeout, truncated) ─────────────────► model_error
   │     └─ LLM_FALLBACK=true ─► DeterministicRouterLLM routes the request instead
   └─ CLARIFICATION text ─► leak/markup filter ─► generic question if it echoes the prompt
```

Unchanged security boundary: validated decisions go through the same graph, gateway, SQL
guard and human approval as in Phases 4-5; the model never gains authorization, identity,
SQL or approval capabilities. Route trace events now carry `model`, `primary_failure`,
`fallback_model` and token `usage`.

Evaluation (ADR-0026, details in [eval/README.md](../eval/README.md)):

| Router | Dev (tuned) | Held-out v1 | Held-out v2 | Adversarial | Safety |
|---|---|---|---|---|---|
| Deterministic (TESTED) | 92/92 | 21/24 (routing 0.875) | 22/36 (routing 0.72) | 18/30 (routing 0.67) | unauthorized-action prevention 1.00, leaks 0, injection 0/30 |
| Ollama qwen2.5:0.5b (EXPERIMENTAL) | route-only dev tuning: 41.0% routing, tool 19/27, invalid 2/83 | — | — | — | **no valid evaluation** (run stopped for low memory; discarded) |

Ollama feasibility (measured, not a benchmark): llama-server peak ~554 MB + Ollama ~70 MB;
16–18 tokens/s; warm routing 1.3–5.2 s, cold ~7.8 s; ~1,300 prompt tokens/request; free
RAM 0.34–0.88 GB depending on open applications.

## 3. Known limitations (by design for Phase 1)

- Single property; tables carry `hotel_id`, so multi-property is possible later (untested).
- Sync SQLAlchemy engine; FastAPI runs sync endpoints in its threadpool (ADR-0006).
  Revisit if the agent needs high concurrency.
- No authentication yet (local-only, bound to 127.0.0.1). Planned for Phase 8.
- Read-only role can currently read all tables, including guest contact columns
  (synthetic). Narrowing is a Phase 3 deliverable.

### Phase 6 limitations

- No LLM is selected; qwen2.5:0.5b was too large for the free memory and too inaccurate
  (41% tuned dev routing). The Ollama client is EXPERIMENTAL and has no valid full
  evaluation.
- The deterministic router generalizes poorly (held-out v2 routing 0.72, adversarial
  0.67); unseen phrasings mostly fall to KNOWLEDGE → `insufficient_evidence` (safe, but
  unhelpful).
- The fallback only covers model failures; a valid-but-wrong model decision is executed
  as routed (downstream controls still apply).
- The clarification leak filter is heuristic (6-word overlap); it is defence in depth,
  not a guarantee.

### Phase 5 limitations

- The router is a deterministic rule set, not a language model: it handles the phrasings
  its rules cover (held-out routing accuracy 0.875); a real LLM needs owner approval and
  more free memory.
- One capability per request; multi-step plans are not supported (mixed → clarification).
- No authentication: the agent session's staff code is asserted by its launcher; no HTTP
  endpoint for the agent.
- Text-to-SQL analytics views are not hotel-scoped (single-property design from Phase 3);
  the router refuses explicit other-hotel requests, but multi-property analytics needs a
  scoped design.
- LangGraph checkpointing/`interrupt()` is not used; approvals are resolved out of band.

### Phase 4 limitations

- **No authentication**: the MCP session principal is asserted by whoever launches
  `hotel-mcp --staff-code …` (and `hotel-ops --as …`). Fine for a local simulation, not for
  production. Not production-ready.
- stdio transport only; no network transport or multi-user server.
- Operations are simulated: refunds are ledger rows, no payment system; taxes and rate
  plans are not modelled; "now" for reservations is the business date at 12:00.
- Session limits (lookup lockout, pending cap) are per process, not persistent.
- Audit is JSON logs (plus the `approval_requests` table itself); no tamper-evident audit
  store.
- `mcp_server.main()` is exercised by a subprocess test, so line coverage under-reports it.

### Phase 3 limitations

- The SQL generator is a deterministic rule set (no LLM): it answers only the question
  shapes it knows and refuses the rest. The evaluation measures the safety pipeline and the
  rules, not a language model; the rule set was fixed once after the first evaluation run
  (one misrouted question), and there is no held-out split for it.
- Safety checks cannot catch semantically wrong but safe SQL; mitigated by the glossary,
  by returning the executed SQL, and by evaluation against reference SQL.
- `pg_catalog` metadata (table/column names) is readable by any PostgreSQL role; the guard
  blocks catalog relations, and `hotel_sql` cannot read any data outside the views.
- The dev container's admin role is a superuser (views are owned by it); production
  hardening (Phase 8) should use a non-superuser owner.
- Synthetic data artifact: cancellation timestamps cluster just before the business date
  (the Phase 1 generator caps them there), e.g. 83 cancellations "this week".

### Phase 2 limitations

- Answers are extractive (verbatim sentences), not fluent prose; multi-part questions may
  get only part of the answer (evidence-in-answer 0.75 on the held-out split).
- The refusal gate is a heuristic: paraphrases whose words are absent from the documents
  (e.g. "phone" vs "telephone") can be refused, and near-topic questions can be answered
  (held-out misses: spa opening hours, parking cost). Refusal recall 0.78 on test.
- Thresholds are calibrated for this corpus and evaluation set; changing either requires
  re-running `hotel-kb eval` and recalibrating on the dev split.
- The evaluation set is small (83 questions) and written by the same author as the corpus.
- The IDF statistics are cached per process and refreshed when the chunk table changes.
