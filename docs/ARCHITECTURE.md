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

Planned principles (not yet implemented):

- LLM and embeddings run **locally** (no paid APIs) — ADR-0002. Model choice is open and
  constrained by an 8 GB, CPU-only machine.
- **RAG:** answers must cite chunk/document IDs; low retrieval confidence → refuse rather
  than guess; retrieved text is treated as data, never as instructions.
- **Text-to-SQL:** LLM sees curated views only; generated SQL is parsed and validated
  (single `SELECT`, allowlisted relations, row limit); executed as the read-only role with
  a statement timeout; every query is logged.
- **Tools behind MCP:** typed schemas; read vs write classification; write tools go
  through human approval; tool logic lives in a plain service layer so MCP is a thin
  wrapper.
- **Human approval:** LangGraph `interrupt()` with a Postgres-backed checkpointer so
  pending approvals survive restarts.
- **Tests never call an LLM**; real-model evaluation runs as a separate suite.

## 2. Current architecture (Phase 1) — TESTED

```
uvicorn ─► FastAPI app (create_app factory)
             ├─ RequestContextMiddleware  (X-Request-ID, JSON request log)
             └─ /health, /health/ready ─► SQLAlchemy engine ─► PostgreSQL 16 + pgvector
                                                               (Docker, 127.0.0.1 only)
hotel-seed CLI ─► generator.generate(seed, anchor_date) ─► loader (single transaction)
alembic ─► migrations/ (0001 core schema, 0002 read-only role)
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
| `migrations/` | Alembic environment and revisions | TESTED |
| `tests/` | pytest suite (92 tests at Phase 1 completion) | — |

### 2.2 Runtime infrastructure

- **PostgreSQL 16.12 + pgvector 0.8.1**, image `pgvector/pgvector:0.8.1-pg16`, run by
  `docker-compose.yml`; port bound to `127.0.0.1` only; named volume `pgdata`;
  `pg_isready` healthcheck.
- Extensions created by migration 0001: `btree_gist` (exclusion constraint), `vector`
  (installed for Phase 2; no vector columns exist yet).

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

### 2.5 Read-only access (groundwork for Text-to-SQL)

Migration 0002 (ADR-0005) creates a `hotel_readonly` NOLOGIN group with `SELECT` on the
`public` schema (including future tables via default privileges) and a login role
(`READONLY_DB_USER`) that is a member of it, with `default_transaction_read_only = on` and
`statement_timeout = 5s`. Writes fail on privileges even if a session switches to
read-write (tested). Phase 3 will narrow access to curated views (e.g. excluding guest
contact details).

### 2.6 API

| Endpoint | Behaviour |
|---|---|
| `GET /health` | Liveness; never touches dependencies. |
| `GET /health/ready` | 200 only if the DB is reachable **and** migrated to Alembic head; otherwise 503 with details. |

Engine connect timeout is 3 s so readiness fails fast instead of hanging.

### 2.7 Logging and request correlation

JSON lines on stdout: timestamp, level, logger, message, `request_id` (context variable
set by the middleware) and any `extra=` fields. A caller-supplied `X-Request-ID` is reused
only if it matches `^[A-Za-z0-9._-]{1,64}$`; otherwise a new UUID hex is generated (prevents
log injection). Uvicorn's access log is disabled in favour of our request log.

### 2.8 Synthetic data

See ADR-0003. `generate(seed, anchor_date)` is pure and deterministic; the loader inserts
in one transaction, refuses a non-empty database without `--reset`, and advances ID
sequences past the explicit IDs.

## 3. Known limitations (by design for Phase 1)

- Single property; tables carry `hotel_id`, so multi-property is possible later (untested).
- Sync SQLAlchemy engine; FastAPI runs sync endpoints in its threadpool (ADR-0006).
  Revisit if the agent needs high concurrency.
- No authentication yet (local-only, bound to 127.0.0.1). Planned for Phase 8.
- Read-only role can currently read all tables, including guest contact columns
  (synthetic). Narrowing is a Phase 3 deliverable.
