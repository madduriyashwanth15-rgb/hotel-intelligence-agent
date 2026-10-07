# Decision Log — Hotel Intelligence Agent

Canonical record of architectural and project decisions (ADR format). Newest decisions
are appended at the end; superseded decisions are marked, never deleted.

| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](#adr-0001--core-stack-and-single-postgresql-datastore) | Core stack and single PostgreSQL datastore | Accepted | 2026-10-07 |
| [0002](#adr-0002--0-budget-local-only-development) | ₹0 budget, local-only development | Accepted | 2026-10-07 |
| [0003](#adr-0003--deterministic-synthetic-data-for-one-fictional-hotel) | Deterministic synthetic data for one fictional hotel | Accepted | 2026-10-07 |
| [0004](#adr-0004--schema-conventions-and-database-enforced-integrity) | Schema conventions and database-enforced integrity | Accepted | 2026-10-07 |
| [0005](#adr-0005--least-privilege-read-only-role) | Least-privilege read-only role | Accepted | 2026-10-07 |
| [0006](#adr-0006--synchronous-sqlalchemy-engine-for-now) | Synchronous SQLAlchemy engine for now | Accepted | 2026-10-07 |
| [0007](#adr-0007--use-127001-for-local-database-connections) | Use 127.0.0.1 for local database connections | Accepted | 2026-10-07 |
| [0008](#adr-0008--canonical-project-documentation-set) | Canonical project documentation set | Accepted | 2026-10-07 |
| [0009](#adr-0009--local-embeddings-with-fastembed--bge-small-en-v15) | Local embeddings with fastembed + bge-small-en-v1.5 | Accepted | 2026-10-07 |
| [0010](#adr-0010--extractive-answers-only-in-phase-2) | Extractive answers only in Phase 2 | Accepted | 2026-10-07 |
| [0011](#adr-0011--hybrid-retrieval-pgvector--postgresql-full-text--rrf) | Hybrid retrieval: pgvector + PostgreSQL full-text + RRF | Accepted | 2026-10-07 |
| [0012](#adr-0012--knowledge-base-documents-as-markdown-with-toml-front-matter) | Knowledge-base documents as Markdown with TOML front matter | Accepted | 2026-10-07 |
| [0013](#adr-0013--evidence-gate-calibration-and-evaluation-methodology) | Evidence gate, calibration and evaluation methodology | Accepted | 2026-10-07 |

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

- Status: Accepted
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
