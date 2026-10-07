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
  wrapper.
- **Human approval:** LangGraph `interrupt()` with a Postgres-backed checkpointer so
  pending approvals survive restarts.
- **Tests never call an LLM**; real-model evaluation runs as a separate suite.

## 2. Current architecture (Phases 1–3) — TESTED

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
alembic ─► migrations/ (0001 core, 0002 read-only role, 0003 knowledge base, 0004 analytics)
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
| `src/hotel_agent/sql/cli.py` | `hotel-sql` command line | TESTED |
| `data/knowledge_base/` | 15 synthetic hotel documents | — |
| `eval/` | Evaluation set and saved results | — |
| `migrations/` | Alembic environment and revisions | TESTED |
| `tests/` | pytest suite (92 tests after Phase 1; 181 after Phase 2; 458 after Phase 3) | — |

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

## 3. Known limitations (by design for Phase 1)

- Single property; tables carry `hotel_id`, so multi-property is possible later (untested).
- Sync SQLAlchemy engine; FastAPI runs sync endpoints in its threadpool (ADR-0006).
  Revisit if the agent needs high concurrency.
- No authentication yet (local-only, bound to 127.0.0.1). Planned for Phase 8.
- Read-only role can currently read all tables, including guest contact columns
  (synthetic). Narrowing is a Phase 3 deliverable.

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
