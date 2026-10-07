# Decision Log — Hotel Intelligence Agent

Canonical record of architectural and project decisions (ADR format). Newest decisions
are appended at the end; superseded decisions are marked, never deleted.

| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](#adr-0001--core-stack-and-single-postgresql-datastore) | Core stack and single PostgreSQL datastore | Accepted | 2026-10-07 |
| [0002](#adr-0002--0-budget-local-only-development) | ₹0 budget, local-only development | Accepted | 2026-10-07 |
| [0003](#adr-0003--deterministic-synthetic-data-for-one-fictional-hotel) | Deterministic synthetic data for one fictional hotel | Accepted | 2026-10-07 |
| [0004](#adr-0004--schema-conventions-and-database-enforced-integrity) | Schema conventions and database-enforced integrity | Accepted | 2026-10-07 |
| [0005](#adr-0005--least-privilege-read-only-role) | Least-privilege read-only role | Amended by ADR-0014 | 2026-10-07 |
| [0006](#adr-0006--synchronous-sqlalchemy-engine-for-now) | Synchronous SQLAlchemy engine for now | Accepted | 2026-10-07 |
| [0007](#adr-0007--use-127001-for-local-database-connections) | Use 127.0.0.1 for local database connections | Accepted | 2026-10-07 |
| [0008](#adr-0008--canonical-project-documentation-set) | Canonical project documentation set | Accepted | 2026-10-07 |
| [0009](#adr-0009--local-embeddings-with-fastembed--bge-small-en-v15) | Local embeddings with fastembed + bge-small-en-v1.5 | Accepted | 2026-10-07 |
| [0010](#adr-0010--extractive-answers-only-in-phase-2) | Extractive answers only in Phase 2 | Accepted | 2026-10-07 |
| [0011](#adr-0011--hybrid-retrieval-pgvector--postgresql-full-text--rrf) | Hybrid retrieval: pgvector + PostgreSQL full-text + RRF | Accepted | 2026-10-07 |
| [0012](#adr-0012--knowledge-base-documents-as-markdown-with-toml-front-matter) | Knowledge-base documents as Markdown with TOML front matter | Accepted | 2026-10-07 |
| [0013](#adr-0013--evidence-gate-calibration-and-evaluation-methodology) | Evidence gate, calibration and evaluation methodology | Accepted | 2026-10-07 |
| [0014](#adr-0014--analytics-schema-hotel_sql-role-and-narrowed-hotel_readonly) | Analytics schema, hotel_sql role and narrowed hotel_readonly | Accepted | 2026-10-07 |
| [0015](#adr-0015--sql-safety-pipeline) | SQL safety pipeline | Accepted | 2026-10-07 |
| [0016](#adr-0016--sql-generator-interface-and-deterministic-fakes) | SQL generator interface and deterministic fakes | Accepted | 2026-10-07 |
| [0017](#adr-0017--business-date-and-metric-glossary) | Business date and metric glossary | Accepted | 2026-10-07 |
| [0018](#adr-0018--mcp-tool-server-as-a-thin-boundary-over-a-tool-gateway) | MCP tool server as a thin boundary over a tool gateway | Accepted | 2026-10-07 |
| [0019](#adr-0019--tool-classification-and-human-approval-workflow) | Tool classification and human approval workflow | Accepted | 2026-10-07 |
| [0020](#adr-0020--staff-principals-authorization-and-hotel-scoping) | Staff principals, authorization and hotel scoping | Accepted | 2026-10-07 |
| [0021](#adr-0021--simulated-refund-policy-engine-and-cancellation-refunds) | Simulated refund policy engine and cancellation refunds | Accepted | 2026-10-07 |
| [0022](#adr-0022--langgraph-orchestration-layer-above-existing-services) | LangGraph orchestration layer above existing services | Accepted | 2026-10-07 |
| [0023](#adr-0023--provider-neutral-llm-interface-deterministic-fake-no-local-llm-yet) | Provider-neutral LLM interface, deterministic fake, no local LLM yet | Accepted | 2026-10-07 |
| [0024](#adr-0024--agent-security-invariants-and-routing-policy) | Agent security invariants and routing policy | Accepted | 2026-10-07 |
| [0025](#adr-0025--local-llm-router-via-ollama-experimental-not-selected) | Local LLM router via Ollama: EXPERIMENTAL, not selected | Accepted | 2026-10-07 |
| [0026](#adr-0026--agent-evaluation-methodology-for-model-routers) | Agent evaluation methodology for model routers | Accepted | 2026-10-07 |

Template for new entries: **Context → Decision → Consequences**, with Status and Date.

---

## ADR-0001 — Core stack and single PostgreSQL datastore

- Status: Accepted
- Date: 2026-10-07

### Context
The agent needs operational data (reservations, rooms, revenue), document embeddings for
RAG (Phase 2) and durable agent state for human approval (Phase 6). The project must be
simple to run locally and cost nothing.

### Decision
- **Python 3.12**, dependency management with **uv** (lockfile committed).
- **FastAPI** for the HTTP API, **pydantic-settings** for configuration.
- **PostgreSQL 16 + pgvector** (image `pgvector/pgvector:0.8.1-pg16`) in local Docker as
  the *only* datastore: operational tables, vector embeddings and (later) LangGraph
  checkpoints.
- **SQLAlchemy 2** (typed ORM) with **psycopg 3**; schema changes only via **Alembic**.
- **pytest**, **ruff** (lint + format), **mypy --strict**.

### Consequences
- One container, one backup story, one connection config; tests exercise the real database.
- pgvector is adequate for a single hotel's documents; a dedicated vector DB can be
  introduced later behind a retrieval interface if scale demands it.
- Integrity rules (e.g. no double booking) live in the database itself (ADR-0004).

---

## ADR-0002 — ₹0 budget, local-only development

- Status: Accepted
- Date: 2026-10-07

### Context
The project owner set a hard constraint: building and developing this project must cost
₹0. The development machine is a laptop with ~8 GB RAM, a 4-core i5-8250U CPU, ~58 GB free
disk and no GPU usable for LLM inference (AMD Radeon R7 M460 2 GB / Intel UHD 620).

### Decision
- Everything runs locally. No cloud deployment, hosting or managed databases.
- No paid services and no paid API calls. **No OpenAI API calls.**
- Any dependency or service that *could* incur a charge — including free tiers that can
  bill — requires explicit owner approval before use. A free tier is not assumed acceptable.
- LLM work (Phase 2+) prefers local open-source models. Ollama is **not** installed and no
  model is downloaded until the owner approves it.
- Embeddings will use a small local model (candidate: fastembed + bge-small).
- Observability uses structured logs (and later traces stored locally), not a hosted
  service. GitHub Actions is not set up without approval.
- Unit/integration tests never call an LLM; they use fakes.

### Consequences
- A small CPU-only local model is likely weak at Text-to-SQL and tool calling; evaluation
  sets (Phases 2–3) will measure this, and any change of approach will be brought to the
  owner with evidence.
- Docker Desktop is free for personal use, education and small businesses (<250 employees
  and <US$10M revenue); otherwise Docker Engine in WSL2 or Podman is the free alternative.
  Applicability to the owner is not yet confirmed.

---

## ADR-0003 — Deterministic synthetic data for one fictional hotel

- Status: Accepted
- Date: 2026-10-07

### Context
No real customer data may be used. Development, tests and future evaluation sets (RAG,
Text-to-SQL) need realistic, reproducible data with known answers.

### Decision
- One fictional property: **Grand Hills Resort** (Hillcrest, IN; INR; Asia/Kolkata),
  80 rooms across 6 room types.
- `hotel_agent.synthetic.generator.generate(seed, anchor_date)` is a **pure, deterministic
  function**: same inputs → identical dataset. Defaults: `SYNTHETIC_SEED=42`,
  `SYNTHETIC_ANCHOR_DATE=2026-10-01`. The anchor date is the business "today"; data spans
  90 days before to 60 days after it.
- Privacy by construction (enforced by tests):
  - emails use RFC 2606 reserved domains (`example.com/.org/.net`);
  - phone numbers use the NANP `555-0100…0199` range reserved for fiction;
  - names are random pairings from generic first/last name lists.
- Realism included: weekend and seasonal pricing, channel discounts, cancellations (~12%),
  no-shows (~4% of past stays), returning guests, two out-of-order rooms with maintenance
  tasks, dirty rooms with open cleaning tasks.
- The database records provenance in `dataset_metadata` (`synthetic=true`, seed, anchor
  date, generator version, load time).
- The loader runs in one transaction and refuses to load into a non-empty database unless
  `--reset` is given.

### Consequences
- Evaluation questions can have exact expected answers tied to (seed, anchor date,
  generator version). Changing the generator requires bumping `GENERATOR_VERSION` and
  regenerating expected answers.
- Because the anchor date is fixed, "today" in the data is 2026-10-01 regardless of the
  wall clock; queries about "today" must use the business date.

---

## ADR-0004 — Schema conventions and database-enforced integrity

- Status: Accepted
- Date: 2026-10-07 (made during Phase 1; recorded retroactively)

### Context
Future components (Text-to-SQL, tools, agents) will write and read data in ways that are
hard to fully anticipate. Bugs in application code must not be able to corrupt core
invariants.

### Decision
- Enum-like columns are `VARCHAR` + named `CHECK` constraints instead of native PostgreSQL
  enums (adding a value is a constraint change, not an `ALTER TYPE`). Python `StrEnum`
  classes in `db/models.py` define the allowed values.
- A **GiST exclusion constraint** prevents overlapping active reservations for the same
  room (`btree_gist` extension).
- Cross-column rules as `CHECK` constraints (stay dates ordered; `cancelled_at` iff
  cancelled; `completed_at` iff done; non-negative money/counts).
- Constraint names follow a fixed naming convention (`ck_`, `fk_`, `uq_`, `ix_`, `pk_`).
- Occupancy is derived from reservations, not stored; rooms store only housekeeping status.

### Consequences
- Invalid writes fail at the database with specific errors (tested for exclusion and check
  violations).
- psycopg serialises Python `Enum` members by **name**, so values must be passed as plain
  strings (the loader converts to `.value`).

---

## ADR-0005 — Least-privilege read-only role

- Status: Accepted; **amended by ADR-0014** (2026-10-07): `hotel_readonly` is narrowed to
  the knowledge-base tables; generated SQL runs as the separate `hotel_sql` role.
- Date: 2026-10-07 (made during Phase 1; recorded retroactively)

### Context
Text-to-SQL (Phase 3) will execute LLM-generated SQL. Validation alone is not sufficient;
the database account itself must be incapable of writes.

### Decision
- Migration 0002 creates a NOLOGIN group role `hotel_readonly` with `USAGE` + `SELECT` on
  schema `public`, plus default privileges for future tables.
- A login role (`READONLY_DB_USER`, password from `READONLY_DB_PASSWORD`) is a member, with
  `default_transaction_read_only = on` and `statement_timeout = 5s`.
- DDL is built server-side with `format(%I, %L)` so identifiers and the password are
  safely quoted; passwords never appear in migration files.
- Roles are cluster-wide, so creation is idempotent; downgrade revokes privileges but keeps
  the roles.

### Consequences
- Writes are blocked by privileges, not just by the session default (tested).
- Currently the role can read every table, including (synthetic) guest contact columns.
  Phase 3 will restrict it to curated views.

---

## ADR-0006 — Synchronous SQLAlchemy engine for now

- Status: Accepted
- Date: 2026-10-07 (made during Phase 1; recorded retroactively)

### Context
FastAPI supports async, but Phase 1 has only health endpoints and a CLI loader.

### Decision
Use a synchronous SQLAlchemy engine (psycopg 3). FastAPI runs sync endpoints in its
threadpool. Engine connect timeout is 3 s for the API.

### Consequences
Simpler code and tests. Revisit (async engine) if the agent needs high concurrency or
long-running streaming responses; that would be a recorded decision change.

---

## ADR-0007 — Use 127.0.0.1 for local database connections

- Status: Accepted
- Date: 2026-10-07 (made during Phase 1; recorded retroactively)

### Context
The Postgres container port is bound to `127.0.0.1` only (not exposed to the network). On
Windows, `localhost` resolves to IPv6 `::1` first, so each connection stalled on a timeout
(~5 s measured, longer without a connect timeout) before falling back to IPv4.

### Decision
`POSTGRES_HOST` defaults to `127.0.0.1` in settings and `.env.example`.

### Consequences
Fast, reliable local connections; documented in README and CLAUDE.md gotchas.

---

## ADR-0008 — Canonical project documentation set

- Status: Accepted
- Date: 2026-10-07

### Context
Phase 1 produced `docs/architecture.md`, `docs/roadmap.md` and three ADR files in
`docs/adr/`. The owner requested a permanent "project brain" with fixed canonical files.

### Decision
- `CLAUDE.md` (repo root) — primary persistent instructions for Claude.
- `docs/PRD.md` — product requirements and capability status.
- `docs/ARCHITECTURE.md` — canonical architecture (replaces `docs/architecture.md`).
- `docs/ROADMAP.md` — canonical roadmap (replaces `docs/roadmap.md`).
- `docs/DECISIONS.md` — canonical decision log (absorbs `docs/adr/0001–0003`, verbatim
  content preserved).
- `docs/DEVELOPMENT_RULES.md` — engineering rules and conventions.
- `README.md` — human quick start; links to the above.

### Consequences
One place per topic; no duplicate ADR files that could drift. New decisions are appended
here rather than added as separate files.

---

## ADR-0009 — Local embeddings with fastembed + bge-small-en-v1.5

- Status: Accepted (owner-approved 2026-10-07)
- Date: 2026-10-07

### Context
Phase 2 needs text embeddings for retrieval. Constraints: ₹0, local only, CPU-only laptop
with ~8 GB RAM (only ~0.3 GB free was observed during planning), and no large ML
frameworks. Options considered: sentence-transformers (pulls PyTorch — rejected), Ollama
embedding models (Ollama not approved — rejected), model2vec static embeddings (lightest,
lower quality — kept as fallback), raw onnxruntime + tokenizers (fewer deps, more
hand-written pre/post-processing), fastembed (ONNX Runtime, no PyTorch).

### Decision
- Library: **fastembed** (Apache-2.0), which runs models with **ONNX Runtime** (MIT).
  **No PyTorch.**
- Model: **BAAI/bge-small-en-v1.5** (MIT licence): 384 dimensions, 512-token limit,
  ~67 MB quantized ONNX as distributed for fastembed.
- One-time **anonymous** download from Hugging Face (no account, no API key, no payment —
  owner-approved). The model is cached in a git-ignored project directory; after the
  download, runtime runs offline and Hugging Face telemetry is disabled.
- Code depends on an `Embedder` protocol; tests use a deterministic fake embedder and never
  need the model.
- The embedding model name is stored with every chunk so a model change is detectable.

### Consequences
- Disk ~0.3–0.5 GB total (venv + model); RAM a few hundred MB while embedding (to be
  measured and recorded).
- Changing the model requires re-ingestion (dimension and vector space change) and a new
  decision record.

---

## ADR-0010 — Extractive answers only in Phase 2

- Status: Accepted (owner-approved 2026-10-07)
- Date: 2026-10-07

### Context
"Grounded answer generation" normally uses an LLM. No LLM is approved (Ollama not
installed), and the machine's free RAM is too low for reliable local generation.

### Decision
- Phase 2 answers are **extractive**: the answer consists of the most relevant sentences
  copied verbatim from retrieved chunks, each with a citation (`doc_key#chunk_index`).
- When evidence is insufficient the system returns an explicit
  `insufficient_evidence` response ("I don't know") instead of an answer.
- The answerer sits behind an interface so an LLM-backed generator can be added later as a
  **separate, separately approved** step. No LLM generation in Phase 2.

### Consequences
- Answers cannot contain unsupported (hallucinated) content; they may read less fluently.
- Citations can only reference chunks that retrieval actually returned (validated).

---

## ADR-0011 — Hybrid retrieval: pgvector + PostgreSQL full-text + RRF

- Status: Accepted (owner-approved 2026-10-07)
- Date: 2026-10-07

### Context
Hotel questions mix paraphrase ("can I get my money back if I cancel late?") with exact
terms (room codes, "late checkout", emergency code names). Vector search handles the
former, keyword search the latter.

### Decision
- Keep **PostgreSQL + pgvector** as the vector store (ADR-0001); no external vector DB.
- Run vector similarity (cosine) and PostgreSQL full-text search (`websearch_to_tsquery`)
  and fuse the rankings with **Reciprocal Rank Fusion** (k = 60).
- Exact (sequential) vector scan, no ANN index, while the corpus is small (hundreds of
  chunks): 100% recall and negligible latency. Revisit (HNSW) above ~10k chunks.
- Retrieval queries run as the **read-only role** (ADR-0005); ingestion uses the admin role.
- The "insufficient evidence" threshold is **calibrated on the evaluation set**, not guessed.
- The evaluation compares vector-only, keyword-only and hybrid so the choice stays
  evidence-based.

### Consequences
- One database, one query path; no new infrastructure.
- If evaluation shows hybrid does not beat vector-only, this decision is revisited.

---

## ADR-0012 — Knowledge-base documents as Markdown with TOML front matter

- Status: Accepted
- Date: 2026-10-07

### Context
Phase 2 needs synthetic hotel documents with reliable metadata (stable ID, title,
category, version) for citations, without adding parsing dependencies.

### Decision
- Documents live in `data/knowledge_base/*.md`: a TOML front-matter block delimited by
  `+++` lines (parsed with the standard-library `tomllib`), followed by Markdown.
- Required metadata: `doc_key` (stable, unique, e.g. `POL-CANCEL-001`), `title`,
  `category`, `version`, `effective_date`, `synthetic = true`.
- Only Markdown is supported in Phase 2 (no PDF/DOCX parsing dependencies). Ingestion reads
  only from the configured local directory and enforces size limits.
- Chunking is heading-aware; each chunk records its `section_path` for citations.

### Consequences
- Zero extra dependencies for loading; documents are diff-able and reviewable in git.
- Other formats need a future decision and loader.

---

## ADR-0013 — Evidence gate, calibration and evaluation methodology

- Status: Accepted
- Date: 2026-10-07

### Context
Extractive answering (ADR-0010) needs a rule for when to say "I don't know". With
bge-small, cosine similarities of answerable and unanswerable questions overlap
(answerable ≈ 0.59–0.88, unanswerable ≈ 0.51–0.72 on the dev split), so similarity alone
separates them poorly (balanced accuracy 0.884 on dev).

### Decision
- **Two-signal gate:** answer only if the top vector similarity ≥ `KB_MIN_SIMILARITY`
  **and** the IDF-weighted coverage of the query's content stems by the top-3 evidence
  chunks ≥ `KB_MIN_COVERAGE`. IDF comes from PostgreSQL `ts_stat` over `kb_chunks`; stems
  absent from the corpus get the maximum weight (an off-topic word like "casino" sinks
  coverage); question-framing stems ("much", "long", "get", …) are ignored.
- The vector signal is always measured, also in full-text-only mode.
- **Methodology:** evaluation set `eval/kb_questions.json` (83 questions: 64 answerable
  with verbatim evidence quotes, 19 unanswerable incl. operational-data and adversarial),
  split alternately into `dev` (42) and `test` (41). All tuning — thresholds (grid search
  maximising balanced accuracy), sentence-selection parameters, the query-instruction
  choice — uses **dev only**; `test` is reported, never tuned on.
- Calibrated values: `KB_MIN_SIMILARITY = 0.58`, `KB_MIN_COVERAGE = 0.45`; extraction
  `max_sentences = 3`, `relative_margin = 0.08`; bge query instruction **on** (better
  end-to-end on dev: balanced accuracy 0.9375 vs 0.9219, evidence-in-answer 0.81 vs 0.78,
  although retrieval-only metrics were marginally better without it).
- Regression floors (`tests/test_kb_quality.py`) sit ~1–2 questions below the measured
  baseline.

### Measured results (eval/results/kb_eval.json)
- Hybrid retrieval — dev: doc Recall@5 0.97, evidence Recall@5 0.97, MRR@10 0.94;
  test: 1.00 / 1.00 / 0.94. Vector-only had equal or slightly better top-1 on test
  (Recall@1 0.97 vs 0.91) while hybrid improved Recall@5 / evidence recall on dev;
  full-text alone was clearly weaker (Recall@1 0.78–0.81). ADR-0011 stands, with this
  nuance recorded.
- End-to-end — dev: answer rate 0.875, evidence-in-answer 0.81, refusal recall 1.00;
  test: 0.81, 0.75, 0.78 (answered "spa opening hours" and "parking cost").

### Consequences
- The gate is explainable (two numbers returned with every answer) and cheap.
- It is a heuristic: paraphrases using words absent from the documents can be refused;
  near-topic questions can be answered. Any corpus or eval-set change requires re-running
  `hotel-kb eval` and recalibrating on dev.

---

## ADR-0014 — Analytics schema, hotel_sql role and narrowed hotel_readonly

- Status: Accepted (owner-approved 2026-10-07)
- Date: 2026-10-07

### Context
Phase 3 executes SQL produced by a generator (a fake now, possibly an LLM later). That SQL
is untrusted. Inspection showed `hotel_readonly` (ADR-0005) could read every public table,
including `guests` (names, emails, phones).

### Decision
- New schema **`analytics`** containing curated views only, owned by the admin role. Views
  run with their owner's privileges, so readers need no rights on base tables. No view
  exposes guest names, emails, phones or free-text notes.
- New NOLOGIN group **`hotel_analytics`** with `USAGE` on `analytics` and `SELECT` on the
  approved views only (explicit grants, no default privileges), and a login role
  **`hotel_sql`** (`SQL_DB_USER` / `SQL_DB_PASSWORD`) that is its only member. Role defaults:
  `default_transaction_read_only = on`, `statement_timeout = 3s`,
  `idle_in_transaction_session_timeout = 10s`, `search_path = analytics`, connection
  limit 5. `PUBLIC` loses `CREATE` on schema `public` (already the PG15+ default; made
  explicit) and `hotel_sql` gets no rights on `public`.
- **`hotel_readonly` is narrowed** to `SELECT` on `kb_documents` and `kb_chunks` only; the
  broad `SELECT ON ALL TABLES` grant and the default privileges from migration 0002 are
  revoked. Phase 2 retrieval keeps working.

### Consequences
- Even if the SQL guard is bypassed, generated SQL can only read the curated views and can
  never write (tested by running raw SQL as `hotel_sql` without the guard).
- New views must be explicitly granted to `hotel_analytics` in a migration.

---

## ADR-0015 — SQL safety pipeline

- Status: Accepted (owner-approved 2026-10-07)
- Date: 2026-10-07

### Context
Generated SQL must be treated as hostile input. Defence in depth: the guard must be strict
on its own, and the database must be safe on its own.

### Decision
Pipeline: generator → **SqlGuard** (sqlglot, pure Python) → **EXPLAIN cost ceiling** →
**executor** (`hotel_sql`, read-only transaction, `SET LOCAL statement_timeout`, row cap,
always rollback) → deterministic formatter → JSON audit log.

SqlGuard rejects unless *all* hold:
1. SQL text ≤ 4,000 chars, no NUL/control characters.
2. Parses (sqlglot, postgres dialect) into exactly one statement.
3. Root is a SELECT or a set operation of SELECTs; no `exp.Command` fallback anywhere.
4. No DML/DDL node anywhere in the tree (catches data-modifying CTEs), no `SELECT INTO`,
   no locking clause, no recursive CTE.
5. Every relation is an allowlisted `analytics` view (bare or `analytics.`-qualified) or a
   CTE defined in the query; no table functions in FROM.
6. Every function is allowlisted; unknown/anonymous functions, system functions and
   wall-clock functions (`now()`, `CURRENT_DATE`, …) are rejected.
7. Parameters: named placeholders only, all supplied, all used, typed and validated.
8. Outer LIMIT forced to ≤ max_rows + 1, OFFSET ≤ 1,000, literal only.

Failures map to stable reason codes (`unsafe_sql`, `disallowed_relation`,
`disallowed_function`, `invalid_sql`, `invalid_parameters`, `too_expensive`, `timeout`,
`execution_error`). The API returns the executed SQL (clearly labelled) separately from
the natural-language answer. Logs contain a question fingerprint and SQL with literals
masked — never the question text. JSON logs only; audit tables deferred.

### Consequences
- Semantically wrong but safe SQL cannot be blocked by safety checks; it is mitigated by
  the glossary, by showing the SQL, and by evaluation against independent reference SQL.
- sqlglot is pinned (`>=30.21,<31`); the bypass corpus guards against parser changes.
- Cost ceiling set to 250,000 planner units (~10× the most expensive valid evaluation
  query, 24,683); per-query timeout 2 s, role timeout 3 s, 100 rows.
- Measured (2026-10-07): bypass corpus 108/108 blocked; evaluation safety block rate
  34/34, execution accuracy 34/34, refusal accuracy 51/51, wrong-but-safe detection 4/4.

---

## ADR-0016 — SQL generator interface and deterministic fakes

- Status: Accepted (owner-approved 2026-10-07)
- Date: 2026-10-07

### Context
No LLM is approved. The safety pipeline must be built and evaluated before any model.

### Decision
- `SqlGenerator` protocol: `generate(question, context) -> Generated | Refusal`, where
  `SchemaContext` carries the business date, view specs, glossary and allowed values —
  exactly what a future LLM prompt would need.
- `RuleBasedFakeGenerator`: deterministic intent rules for a realistic subset of questions;
  refuses mutations, ambiguous and unsupported questions.
- `ScriptedFakeGenerator`: evaluation-only; emits given SQL to simulate a compromised or
  hallucinating model, so the guard is tested independently of question understanding.
- Any real LLM generator is a future, separately approved step.

### Consequences
Phase 3 answers only the question types the rules cover; everything else is refused with a
clear code. Swapping in a model later changes only the generator.

---

## ADR-0017 — Business date and metric glossary

- Status: Accepted (owner-approved 2026-10-07)
- Date: 2026-10-07

### Decision
- "Today" is the synthetic business date **2026-10-01**, exposed as
  `analytics.business_date` (from `dataset_metadata.anchor_date`). Generated SQL must not
  use `CURRENT_DATE`, `now()` or other wall-clock functions (the guard rejects them).
- Glossary (also given to generators in `SchemaContext`):
  - **occupied room-night**: an active reservation (confirmed, checked_in, checked_out)
    covering the night; **occupancy** = occupied rooms ÷ total rooms (80);
  - **revenue** = posted folio charges (`daily_revenue`); **room revenue** = category
    `room`; there is no on-the-books (future) revenue metric;
  - **ADR** = room revenue ÷ occupied room-nights with posted room revenue;
    **RevPAR** = room revenue ÷ total rooms;
  - **average booking value** = mean `total_amount` of reservations that are not cancelled
    and not no-shows;
  - **this week** = Monday of the business week through the business date;
    **this month** = 1st of the business month through the business date;
    **last week / last month** = the previous full calendar week / month.
- October-to-date revenue is currently ₹0 because no October nights have been posted
  yet; answers state this explicitly instead of returning a bare zero.

---

## ADR-0018 — MCP tool server as a thin boundary over a tool gateway

- Status: Accepted (Phase 4 approved by owner 2026-10-07)
- Date: 2026-10-07

### Context
Phase 4 exposes simulated hotel operations to a future agent through MCP. The tool layer
must be verifiable without an LLM and must not open network services.

### Decision
- Use the official **MCP Python SDK** (`mcp`, MIT) with **stdio transport only**; no HTTP
  listener is started. The SDK's other dependencies (starlette, uvicorn, httpx2, pyjwt,
  …) are installed but unused for transport.
- All logic lives in a plain-Python **ToolGateway** (`hotel_agent/ops/`): tool registry,
  argument schemas (pydantic, `extra="forbid"`), authorization, approval workflow, audit.
  The MCP server only translates MCP calls to `ToolGateway.call(principal, name, args)`.
- Testing: gateway tests without MCP; MCP tests through the SDK's in-memory client session;
  one stdio smoke test that starts the real `hotel-mcp` process. No LLM anywhere.
- No agent is built in Phase 4; MCP itself is the integration point for Phase 5.

### Consequences
- The same gateway can back other front ends (CLI, HTTP) without duplicating rules.
- Coverage is measured with `pytest-cov` (dev dependency, MIT).
- Measured (2026-10-07): MCP in-memory round trip p50 23.5 ms; `hotel-mcp` ~96 MB RSS;
  `mcp` 1.30.0 + `pytest-cov` added 32 MB. The SDK resolved without `httpx2`, so the
  Starlette `httpx2` deprecation warning remains.

---

## ADR-0019 — Tool classification and human approval workflow

- Status: Accepted
- Date: 2026-10-07

### Decision
- Every tool is classified **READ_ONLY** or **MUTATING**; tools touching money or guest
  stays are additionally flagged **SENSITIVE**.
- READ_ONLY tools run inside `SET TRANSACTION READ ONLY` transactions.
- MUTATING tools **never execute in the model's call**. They validate, authorize and create
  an `approval_requests` row (status `pending`, immutable arguments + SHA-256, 30-minute
  expiry) and return its id. Nothing else happens.
- Approval is a **human-only channel** (`hotel-ops approvals approve|reject`), not exposed
  through MCP. The approver must be an active staff member of the same hotel with authority
  for that tool (and amount), and must not be the requester (four-eyes, also a database
  CHECK). Approval executes the action **exactly once** inside one transaction (row lock +
  status transition), re-validating against current data; failures are recorded.
- The model can only read the outcome with `get_approval_status`.

### Consequences
A compromised or confused model can at most create pending requests (capped per session)
that a human must explicitly approve.

---

## ADR-0020 — Staff principals, authorization and hotel scoping

- Status: Accepted
- Date: 2026-10-07

### Decision
- **Principals** are synthetic staff members (`staff_members`: staff_code, role, hotel_id,
  is_active), generated deterministically with the dataset (generator 1.1.0, role-based
  display names, no real people).
- The MCP session principal is fixed when the server is launched (`hotel-mcp --staff-code`)
  and cannot be changed by tool arguments. **There is no authentication** in Phase 4: the
  local launcher asserts identity. This is a simulation boundary, not production auth.
- Roles: `front_desk`, `housekeeping`, `housekeeping_supervisor`, `front_office_manager`,
  `duty_manager`, `general_manager`, with an explicit per-tool permission matrix (deny by
  default).
- **Hotel scoping**: every query is filtered by the principal's `hotel_id`; arguments never
  carry a hotel id (unknown fields are rejected). Other hotels' data is reported as not
  found (no existence oracle).
- **Database least privilege**: tools run as login role `hotel_ops` (OPS_DB_USER /
  OPS_DB_PASSWORD) with SELECT on the needed tables — on `guests` only the columns `id`,
  `first_name`, `last_name`, `loyalty_tier` (no email/phone; names are used only to verify
  a lookup and to derive initials) — INSERT
  on `housekeeping_tasks`, `approval_requests`, `refunds`, and column-limited UPDATE on
  `approval_requests` and `reservations(status, cancelled_at)`. No DELETE, no DDL.
- **PII minimisation**: reservation lookups require confirmation code **and** guest last
  name (SOP-PRIVACY-001) and return initials + loyalty tier only. Repeated failed lookups
  lock the lookup tools for the session (anti-enumeration).
- **Audit**: JSON log events for request, authorization decision, approval
  creation/decision, execution result and latency; arguments are masked per field
  (confirmation codes partially, names and notes redacted).

---

## ADR-0021 — Simulated refund policy engine and cancellation refunds

- Status: Accepted
- Date: 2026-10-07

### Decision
- `calculate_refund` (READ_ONLY, SENSITIVE) implements POL-CANCEL-001 for confirmed future
  stays: OTA bookings → not refundable by the hotel; Private Pool Villa → free ≥ 14 days,
  else 2 nights (or full stay if shorter); corporate → free ≥ 24 h, else 1 night; peak
  season (1 Apr–30 Jun, 20 Dec–5 Jan) → free ≥ 7 days, else 50 % of the stay; otherwise
  flexible → free ≥ 48 h, else 1 night. Hours are measured to 14:00 on the arrival date.
- Simulation assumptions: the guest prepaid the quoted `total_amount`; taxes are ignored;
  rate plans (e.g. non-refundable) are not modelled; "now" is the business date at 12:00
  Asia/Kolkata.
- `request_cancellation_refund` (MUTATING, SENSITIVE): the **amount is always computed
  server-side** (never supplied by the caller). On approval it cancels the reservation and
  records a refund in one transaction, after recomputing the amount (must be unchanged).
  `refunds.reservation_id` is UNIQUE (one refund per reservation).
- Approval authority (synthetic matrix): refunds up to ₹50,000 → `front_office_manager` or
  `duty_manager`; above → `general_manager`. Housekeeping tasks → `housekeeping_supervisor`,
  `duty_manager` or `front_office_manager`.

---

## ADR-0022 — LangGraph orchestration layer above existing services

- Status: Accepted (Phase 5 approved by owner 2026-10-07)
- Date: 2026-10-07

### Context
Phase 5 adds a single entry point that routes a staff request to knowledge (RAG), data
(Text-to-SQL) or operational tools (MCP), without weakening Phase 1-4 controls.

### Decision
- New package `hotel_agent/agent/` **above** the existing services. A LangGraph
  `StateGraph` with explicit nodes: ingest → route → validate → {knowledge | data | tool |
  clarify | refuse} → respond.
- The graph talks to capabilities only through narrow interfaces: `KnowledgeService`
  (the existing `ExtractiveAnswerer`), `DataService` (the existing `SqlPipeline`) and
  `ToolClient` (an **MCP client session** to the existing `hotel-mcp` server, which wraps
  the `ToolGateway`). The graph modules import no database, SQL or approval code.
- Capability nodes pass on only the user's question (knowledge, data) or a tool name and
  arguments (tools). SQL is never produced by the router; Text-to-SQL keeps its own
  generator and SqlGuard.
- Entry points: `HotelAgent.ask()` and the `hotel-agent` CLI (staff code fixed at launch,
  like `hotel-mcp`). No HTTP endpoint: without authentication an HTTP caller could pick
  any identity.
- **LangSmith**: `langchain-core` (required by LangGraph) depends on the `langsmith`
  client library. It is installed but **tracing is forced off in code** (no account, no
  API key); a test fails if tracing is enabled or a LangSmith key is present. Owner
  approved this on 2026-10-07.

### Consequences
Security logic is not duplicated: every authorization, scoping, validation and approval
decision still happens in the Phase 1-4 components.

---

## ADR-0023 — Provider-neutral LLM interface, deterministic fake, no local LLM yet

- Status: Accepted
- Date: 2026-10-07

### Context
₹0 budget and a laptop with ~7.9 GB RAM of which **0.45–0.60 GB was free** (sampled over
30 s on 2026-10-07; Docker, IDE, browser and Claude Code resident). Smallest candidate
instruct models (Ollama q4 downloads): qwen2.5:0.5b 398 MB, qwen2.5:1.5b 986 MB,
llama3.2:1b 1.3 GB, qwen2.5:3b 1.9 GB — plus runtime and KV-cache memory, plus installing
Ollama. Sub-1B models are also weak at reliable structured tool-argument extraction.

### Decision
- `LLMClient` protocol: `complete_structured(system, user, schema) -> BaseModel`; the
  router builds a real prompt (tool catalog, rules) and validates the reply against the
  `RouteDecision` Pydantic schema. No vendor SDK is imported anywhere.
- `DeterministicRouterLLM`: a rule-based fake that implements the same interface, used for
  development, tests and evaluation. `ScriptedLLM` returns arbitrary (including hostile)
  outputs to test that the graph stays safe whatever the model says.
- **No local LLM is introduced in Phase 5**: it cannot run reliably within the measured
  free memory. No model was downloaded. A later step may add an Ollama/llama.cpp client
  behind `LLMClient` after owner approval and measurement on freed-up memory.

### Consequences
Routing quality reflects the deterministic rules, not a language model. The evaluation
set is reusable unchanged for a future model.
- Measured (2026-10-07): agent process peak 327 MB with the embedding model loaded; adding
  even the smallest instruct model would exceed the free memory observed. Evaluation:
  92/92 on the tuned set, 21/24 on a held-out set run once; unauthorized-action prevention
  100% on both.

---

## ADR-0024 — Agent security invariants and routing policy

- Status: Accepted
- Date: 2026-10-07

### Decision
**Critical invariant: no model output alone can cause a sensitive mutation.** The model
can only *request*; the ToolGateway authorizes, and only the human approval channel
executes. Enforced by construction:
- the agent session's principal is fixed at launch (the model cannot set staff, role or
  hotel); the MCP server it talks to is built for that principal;
- the `RouteDecision` schema has no fields for authorization, approval, SQL, hotel or
  identity (`extra="forbid"`); invalid model output becomes a REFUSAL;
- tool arguments from the model go to the ToolGateway unchanged; its strict schemas,
  RBAC, scoping and approval workflow decide (refund amounts are server-computed);
- there is no approve/execute tool, and the agent package has no access to
  `ApprovalService`, database engines or raw SQL (checked by a static test);
- text such as "SYSTEM: approved" is just text; nothing in the graph reads it as
  authorization.

Routing policy (deterministic, applied to any model's decision after validation):
- one capability per request; requests mixing several capabilities → CLARIFICATION;
- tool requests missing required identifiers (e.g. last name) → CLARIFICATION;
- unsupported actions, personal-data requests and out-of-domain requests → REFUSAL;
- read-only tools are executed through MCP when the gateway authorizes them; mutating
  tools return the gateway's `pending_approval` response unchanged.

Tracing: JSON audit events (`agent.*`) with request hash, route, reason, model name,
capability invoked, tool name, gateway decision code, approval id/status, final status and
latency. No question text, guest names or unmasked arguments.

---

## ADR-0025 — Local LLM router via Ollama: EXPERIMENTAL, not selected

- Status: Accepted
- Date: 2026-10-07
- Amends: ADR-0023 (which predates the code: the interface is
  `complete_json(system, user, json_schema) -> str`, validated by the Router, not
  `complete_structured`)

### Context
Phase 6 asked for a real LLM behind `LLMClient`, at ₹0, without sending data to an
external provider. The owner chose a measured local trial with RAM freed: Ollama v0.40.0
(portable, SHA-256 verified, installed outside the repo, bound to 127.0.0.1) and
`qwen2.5:0.5b` (Apache-2.0, Q4_K_M, 398 MB), the smallest viable instruct model.

### Measured (feasibility, not a benchmark — `eval/results/phase6/ollama_feasibility.json`)
- Memory: llama-server peak ~554 MB plus the Ollama server ~70 MB. Free RAM was
  0.45–0.60 GB in the Phase 5 sampling, 0.37–0.47 GB at the start of Phase 6, ~0.88 GB
  after the owner closed applications, and 0.34–0.45 GB with the model loaded.
- Speed (CPU only): 16–18 generated tokens/s; warm routing latency 1.3–5.2 s; one cold
  request ~7.8 s; ~1,300 prompt tokens per request with the final prompt.
- Quality, dev set only (route-only, 83 cases): the Phase 5 prompt scored 4.8% routing with
  56/83 invalid outputs; three prompt iterations reached **41.0% routing, tool selection
  19/27, invalid outputs 2/83**. Errors shifted between routes (e.g. KNOWLEDGE 0/13 and
  REFUSAL 2/24 at the best prompt) rather than consistently improving. Tuning stopped to
  avoid overfitting the dev set (`eval/results/llm_prompt_tuning.json`).
- The full agent evaluation could not complete: the machine ran critically low on memory
  and the Ollama server and evaluation runner were stopped. **No valid full Ollama
  held-out or adversarial evaluation exists**; the one partial run (97.6% model errors
  after the server stopped) was discarded.

### Decision
- **qwen2.5:0.5b is not selected as the router.** Reasons: (1) hardware — the model plus
  agent, Postgres and normal desktop use exceed the free memory of this 8 GB machine; (2)
  quality — ~41% tuned dev routing is far below the deterministic router and below what
  staff could rely on.
- **The deterministic router stays the default** (`LLM_PROVIDER=deterministic`).
- `OllamaLLMClient` is kept as **EXPERIMENTAL** (`LLM_PROVIDER=ollama`, opt-in): standard
  library only, loopback-only URLs (anything else raises), system proxies bypassed,
  1 MB response cap, Ollama `format` = the `RouteDecision` JSON schema, temperature 0,
  truncated/incomplete/HTTP/timeout failures → `LLMError`, token usage recorded.
- Optional fallback (`LLM_FALLBACK=true`): when the model errors or its output fails
  validation, the deterministic router routes the request; the trace records
  `primary_failure` and `fallback_model`. A valid-but-wrong model decision is **not**
  overridden.
- Hardening that applies to any model: raw replies > 4,000 chars rejected; tool arguments
  must be scalars (strings ≤ 500 chars) with snake_case names; model-written clarification
  text is replaced by a fixed question if it contains markup/links/newlines or repeats any
  6-word run of the prompt or tool catalog; unknown refusal labels are reported as
  `not_supported`, never echoed.
- Prompt minimisation: the model sees route definitions, tool names, descriptions (internal
  `[READ_ONLY | SENSITIVE]` tags removed) and argument names/types — no validation
  patterns, staff identity, role, hotel, database, approval or guard details (tested).
  The tuned prompt lives in `router_prompt_v3.txt` (versioned, byte-for-byte).
- No other model is introduced, no larger model downloaded, no paid API, no cloud.
  **No production LLM has been selected.**

### Consequences
The agent architecture supports a real LLM through `LLMClient` without redesign; a better
model can be evaluated later with `hotel-agent eval --llm ...` on the frozen held-out and
adversarial sets. Security never depended on the model: in the live trial the 0.5B model
picked the mutating refund tool for "Is room 101 clean?" and the ToolGateway rejected the
invented arguments. The Ollama runtime and qwen2.5:0.5b artefacts (3.55 GB incl. the
downloaded zip, kept outside the repository) were used only for this feasibility
experiment and were deleted afterwards at the owner's request; no Ollama runtime or model
files are currently required or stored. Re-running the experimental client would need a
fresh, owner-approved install.

---

## ADR-0026 — Agent evaluation methodology for model routers

- Status: Accepted
- Date: 2026-10-07

### Decision
- Three kinds of agent sets, reported separately: **dev/tuned** (`agent_cases.json`),
  **held-out** (`agent_cases_holdout.json` v1, `agent_cases_holdout_v2.json` v2) and
  **adversarial** (`agent_cases_adversarial.json`). v2 and adversarial were committed
  before any LLM prompt tuning; held-out and adversarial sets are never used for tuning
  and each router configuration runs them once. Tuned accuracy is never reported as
  generalization.
- Metrics per set: routing accuracy (+ per-route precision/recall), tool-selection
  accuracy, clarification precision, refusal correctness, outcome accuracy,
  unauthorized-action prevention (database snapshot unchanged), prompt-leak rate,
  prompt-injection success rate (adversarial: DB change, forbidden text or prompt leak),
  malformed-output, model-error and fallback rates, token usage, latency.
- An evaluation run is **invalid** if the model runtime was not available for the whole
  run (e.g. model-error rate from a stopped server). Invalid runs are discarded, never
  reported or committed.

### Consequences
Phase 6 deterministic results (reproduced exactly on re-run): dev 92/92; held-out v1
21/24; held-out v2 22/36 (routing 0.72); adversarial 18/30 (routing 0.67);
unauthorized-action prevention 1.00, prompt leaks 0, injection success 0/30. The
deterministic router generalizes poorly to unseen phrasings (mostly misrouting to
KNOWLEDGE); its misses are wrong but safe.
