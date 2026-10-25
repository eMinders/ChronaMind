# RL-013 — Engine runtime and inspector stack (#3)

Prepared 2026-10-24. D-17/D-20 selected and technically verified; owner/peer review pending.

## D-17 selection

**CPython 3.12.12**, supported minor line 3.12 (`>=3.12,<3.13`); standalone uv project and lock under `custom-memory-engine/`. Upgrade patch versions deliberately and rerun verification; no dependency on root Python 3.13 or root lock.

| Package | Pin | Purpose |
|---|---|---|
| psycopg[binary,pool] | 3.3.2 (pool resolves 3.3.3 in lock) | Async connections/pool, explicit SQL transactions, native tstzrange |
| pgvector | 0.4.2 | Async register_vector integration, 384-dimensional vector adaptation |
| pydantic | 2.12.5 | Validated immutable contracts, timezone-aware dates |
| numpy | 2.4.2 | Vector adaptation/probes; not a heavyweight model runtime |
| tenacity | 9.1.2 | Bounded whole-transaction retry policy when implemented |
| structlog | 25.5.0 | Content-free lowercase_underscore events |
| rich | 14.3.3 | Human-readable verification/console output |
| pytest / pytest-asyncio | 9.0.2 / 1.3.0 | Deterministic unit and asynchronous DB compatibility tests |
| ruff / pyright | 0.15.6 / 1.1.408 | Lint/format and standard-mode typing |

All transitive packages locked by uv. Tests verify native Range round trips, pgvector register/adaptation, transaction-local `app.clock` via parameterized `set_config(..., true)` (equivalent to SET LOCAL), cleanup on pooled reuse after commit and rollback, SKIP LOCKED and transaction advisory locks with independent connections. No ORM in this package. Do not parameterize `SET LOCAL app.clock = %s`; use set_config for safely bound values.

Independent **library** first: no FastAPI, LangGraph, mem0 or root configuration imports; no web layer in research execution. Heavy local model dependencies, LLM tracing integration, benchmark statistics and HTTP dependencies belong to their later adapters/use cases, not this compatibility foundation. All future LLM calls require Langfuse tracing with the deletion-boundary redaction policy; no LLM calls are added here.

## D-20 selection

P12 primary: minimal **React + TypeScript via Vite**. Pre-approved technical fallback: server-rendered HTML + HTMX with equivalent timeline/evidence/delete controls if schedule threatens evaluation. No UI or frontend dependencies are installed in P1. Exact frontend versions must be pinned when RL-121 starts, after then-current compatibility/security review. UI/API never sits on H1–H4 execution path. Product authentication does not replace engine tenant/RLS checks.

## Location revision and evidence

User instruction dated 2026-10-24 places implementation at repository root `custom-memory-engine/`, **not inside Research Lab**. This changes physical placement only: separate pyproject/lock/database/environment, zero imports from app/evals/scripts, and no changes to product runtime. Research Lab remains normative specification and decision index, with links to these memos. Historical issue-draft paths are superseded by the dated boundary revision.

Sources: Research Lab Architecture §3, Independence Boundary, Proposal §§9/11; Psycopg/pgvector documentation in `../../evidence/SOURCES.md`; executable tests and report. Unblocks runtime choices for RL-003/030/040/120/121; the small independent manifest supplied here is a compatibility vehicle, not a claim all later packaging/application tasks are complete.

## Sign-off

Owner Member 2 (R-ENGINE): pending. Peer Member 5 (R-APP): pending. No member signature inferred from successful tests.
