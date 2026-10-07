# Product Requirements — Hotel Intelligence Agent

Status labels follow [CLAUDE.md §13](../CLAUDE.md#13-truthfulness-rule):
IMPLEMENTED · TESTED · PLANNED · EXPERIMENTAL.

## 1. Problem

Hotel teams (front office, housekeeping, reservations, revenue, management) spend time
searching SOPs and policies, pulling numbers from operational systems, and performing
routine operational actions. Answers are often inconsistent and hard to trace to a source.

## 2. Product goal

An AI copilot for hotel teams that can:

1. Answer questions from hotel SOPs, policies, menus and operational documents using
   **grounded RAG** (answers cite their sources).
2. Answer questions about operational data (reservations, occupancy, rooms, revenue)
   using **controlled Text-to-SQL**.
3. Perform hotel operations through **controlled tools** (e.g. check reservations, check
   room status, create housekeeping tasks).
4. Orchestrate these capabilities with **LangGraph**.
5. Expose tools through **MCP** for standardized access.
6. Require **human approval** for sensitive actions.
7. Include **evaluation, testing, logging and observability**.
8. Use **completely synthetic** hotel data initially.

## 3. Users (initial assumptions)

| User | Typical need |
|---|---|
| Front office | Reservation lookups, arrivals/departures, policy answers |
| Housekeeping | Room status, task creation and tracking |
| Reservations / revenue | Occupancy, ADR, revenue by channel or room type |
| Management | Operational summaries, SOP compliance questions |

No user research has been done; these are working assumptions.

## 4. Capabilities and status

### A. Hotel knowledge — TESTED (Phase 2, extractive answers)
SOPs, policies, room information, F&B information, cancellation policies, emergency
procedures, service recovery policies — 15 synthetic Grand Hills Resort documents.

| Item | Status |
|---|---|
| Synthetic knowledge base (15 documents, all six topic areas) | TESTED |
| Local embeddings (fastembed + bge-small-en-v1.5, CPU) | TESTED |
| Hybrid retrieval (pgvector + PostgreSQL full-text + RRF) with citations | TESTED |
| Extractive grounded answers + "I don't know" when evidence is weak | TESTED |
| `POST /knowledge/ask` and `hotel-kb ask` | TESTED |
| Retrieval / answer evaluation with held-out split and regression floors | TESTED |
| Fluent LLM-generated answers | PLANNED (separate, owner-approved step) |

Held-out results: retrieval Recall@5 1.00; answers contain the expected evidence 75% of
the time; 78% of unanswerable questions refused (see ARCHITECTURE §2.9).

### B. Hotel data intelligence — data foundation TESTED; question answering PLANNED (Phase 3)
Reservations, occupancy, rooms, revenue, housekeeping.

| Item | Status |
|---|---|
| Operational schema (hotels, room types, rooms, guests, reservations, folio charges, housekeeping tasks) | TESTED |
| Deterministic synthetic dataset for Grand Hills Resort | TESTED |
| Read-only database role for analytical queries | TESTED |
| Natural-language questions over the data (Text-to-SQL) | PLANNED |

### C. Controlled tools — PLANNED (Phase 4)
Examples: `get_reservation`, `get_room_status`, `get_occupancy`,
`create_housekeeping_task`, `calculate_refund`.
Sensitive write actions must require human approval (Phase 6).
`calculate_refund` depends on a cancellation policy, which will come from the Phase 2
synthetic policy documents.

### Platform

| Item | Status |
|---|---|
| FastAPI service with `/health` and `/health/ready` | TESTED |
| `POST /knowledge/ask` (knowledge Q&A) | TESTED |
| Structured JSON logging with request IDs | TESTED |
| Agent orchestration (LangGraph) | PLANNED (Phase 5) |
| MCP tool server | PLANNED (Phase 4) |
| Human-in-the-loop approvals | PLANNED (Phase 6) |
| Evaluation harness and tracing | PLANNED (Phase 7) |
| Authentication / authorization | PLANNED (Phase 8) |

## 5. Constraints

- **Budget: ₹0, hard constraint.** Local, open-source, free tools only; no paid APIs,
  cloud, hosting, databases or observability. Anything that could cost money requires
  owner approval first. ([DECISIONS.md ADR-0002](DECISIONS.md#adr-0002--0-budget-local-only-development))
- **Hardware:** ~8 GB RAM, CPU-only laptop. Any local LLM must be small.
- **Data:** synthetic only; no real customer data.
- **Initial scope:** one fictional property, Grand Hills Resort.

## 6. Non-goals (for now)

- Real hotel/PMS integrations or real customer data.
- Cloud deployment or public hosting.
- A frontend UI (API only until decided otherwise).
- Multi-property support (schema allows it; not built or tested).

## 7. Success criteria

Each phase has a Definition of Done in [ROADMAP.md](ROADMAP.md). Quantitative quality
targets (retrieval recall, SQL accuracy, routing accuracy) will be set when the
corresponding evaluation sets exist (Phases 2–3), based on measured baselines rather
than guessed numbers.

## 8. Open questions

- Which local LLM (if any) is viable on this hardware for SQL and tool calling? To be
  measured in Phase 3+; Ollama is not installed until the owner approves.
- Is extractive answering good enough for staff, or is LLM phrasing worth its RAM/latency
  cost? Needs user feedback.
- Is Docker Desktop's free licence applicable to the owner? (Fallbacks: Docker Engine in
  WSL2 or Podman.)
- Will a UI be needed, and when?
