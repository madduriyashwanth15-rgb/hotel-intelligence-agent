# Hotel Intelligence Agent

An AI copilot for hotel teams: grounded answers from SOPs and policies (RAG),
controlled questions over operational data (Text-to-SQL), and safe hotel
operations through tools with human approval.

**Status: Phase 3 complete (grounded knowledge retrieval with cited extractive answers;
controlled, read-only Text-to-SQL over curated views with a deterministic fake generator).**
No LLM is used. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Project documentation

| Document | Purpose |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Persistent project instructions (rules, constraints, current state) |
| [docs/PRD.md](docs/PRD.md) | Product requirements and capability status |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture (current and planned) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phases, Definition of Done, backlog |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decision log (ADRs) |
| [docs/DEVELOPMENT_RULES.md](docs/DEVELOPMENT_RULES.md) | Engineering rules and conventions |

## Ground rules

- **₹0 budget, local only.** No paid services, no cloud, no paid API calls
  ([ADR-0002](docs/DECISIONS.md#adr-0002--0-budget-local-only-development)).
- **Synthetic data only.** All data describes the fictional *Grand Hills
  Resort* ([ADR-0003](docs/DECISIONS.md#adr-0003--deterministic-synthetic-data-for-one-fictional-hotel)).
- **No secrets in git.** Configuration comes from environment variables / `.env`.

## Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Docker with Compose (runs PostgreSQL 16 + pgvector locally)

## Setup

```bash
# 1. Configure (edit the passwords; .env is git-ignored)
cp .env.example .env

# 2. Install dependencies
uv sync

# 3. Start PostgreSQL and wait until healthy
docker compose up -d --wait

# 4. Create the schema
uv run alembic upgrade head

# 5. Load synthetic data (use --reset to reload)
uv run hotel-seed

# 6. Run the API
uv run uvicorn hotel_agent.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

Check it: `curl http://127.0.0.1:8000/health` and `curl http://127.0.0.1:8000/health/ready`.

### Knowledge base (Phase 2)

```bash
uv run hotel-kb download-model   # one-time, free, anonymous (~65 MB into .models/)
uv run hotel-kb ingest           # chunk + embed data/knowledge_base/*.md (idempotent)
uv run hotel-kb ask "Can I cancel for free two days before arrival?"
uv run hotel-kb eval --output kb_eval.json   # metrics -> eval/results/
```

```bash
curl -X POST http://127.0.0.1:8000/knowledge/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Where is the fire assembly point?"}'
```

Answers are sentences quoted verbatim from the documents, each with a citation such as
`[EMG-FIRE-001#3]`; when the evidence is weak the answer is "I don't know".

### Operational data (Phase 3, read-only)

```bash
uv run hotel-sql ask "How many rooms are occupied tonight?"
uv run hotel-sql eval --output sql_eval.json   # safety + accuracy metrics -> eval/results/
curl -X POST http://127.0.0.1:8000/data/ask -H "Content-Type: application/json" \
  -d '{"question": "What revenue was generated last month?"}'
```

Generated SQL is validated by an allowlist guard and executed as `hotel_sql`, a role that
can read only curated `analytics` views (no guest identities) and can never write. The
response contains the answer and, separately, the exact SQL that was executed.

> **Windows note:** use `POSTGRES_HOST=127.0.0.1`, not `localhost`. Windows
> resolves `localhost` to IPv6 `::1` first, while the container is bound to
> IPv4 only, so every connection stalls on a timeout before falling back.

## Quality checks

```bash
uv run pytest          # DB tests need Postgres; `model` tests need the downloaded model
uv run ruff format --check .
uv run ruff check .
uv run mypy
```

Tests create and drop their own databases (`TEST_POSTGRES_DB`, default
`hotel_agent_test`); they never touch the development database.

## Layout

```
src/hotel_agent/
  config.py            typed settings from environment
  logging_config.py    JSON logging + request-id context
  api/                 FastAPI app, health endpoints, middleware
  db/                  SQLAlchemy models, engine/session helpers
  synthetic/           deterministic data generator and loader CLI
  knowledge/           knowledge base: loader, chunker, embeddings, retrieval, answers, eval
  sql/                 controlled Text-to-SQL: guard, executor, generators, pipeline, eval
data/knowledge_base/   synthetic hotel documents (Markdown + TOML front matter)
eval/                  evaluation questions and results
migrations/            Alembic migrations
tests/                 pytest suite
docs/                  PRD, architecture, roadmap, decisions, development rules
CLAUDE.md              persistent project instructions
```

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness; never touches dependencies. |
| `GET /health/ready` | Readiness; 200 only if the DB is reachable and migrated to head, else 503. |
| `POST /knowledge/ask` | Grounded extractive answer with citations, or "insufficient evidence". |
| `POST /data/ask` | Read-only operational answer + executed SQL, or a refusal with a reason code. |

Every response carries an `X-Request-ID` header (a caller-supplied value is
reused if it is safe, otherwise one is generated); the same id appears in logs.
