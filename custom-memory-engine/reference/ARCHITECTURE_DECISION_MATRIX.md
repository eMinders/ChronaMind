# Architecture Decision Matrix

**Subject:** Structural relationship between the root ChronaMind application (`app/`) and the Research Lab (`research-lab/`)  
**Canonical Decision:** `D-ARCH` in [`../PLAN/DECISIONS_REGISTER.md`](../PLAN/DECISIONS_REGISTER.md) — **Option C (Hybrid Architecture)** is selected.

---

## 1. Context

The ChronaMind repository contains two distinct systems:

1. **Root Application (`app/`, `evals/`, `docker-compose.yml`):** A FastAPI + LangGraph conversational agent using `mem0ai` (`AsyncMemory` backed by PostgreSQL + `pgvector`), JWT session auth, Langfuse tracing, and Prometheus/Grafana monitoring.
2. **Research Lab (`research-lab/`):** The V4 Graduation Project research specification and implementation workspace for a **bi-temporal, write-governed, lineage-deletable conversational memory engine** evaluated on `H1`–`H4` across LongMemEval, LoCoMo, and four synthetic suites (`T`, `G`, `D`, `I`).

Because the root application relies on `mem0ai`'s unversioned memory store (which corresponds to baseline `B3` in the research proposal, not the proposed system `P`), the team evaluated three architectural options for executing the research project.

---

## 2. Comparison of Architectural Options

| Criterion | Option A: Full Monorepo Integration (Refactor `app/` in place) | Option B: Pure Standalone Lab (Engine + Harness only, no UI/API) | Option C: Hybrid Architecture (Selected — Self-contained `research-lab/` with optional thin PDT adapter) |
|---|---|---|---|
| **Description** | Replace `mem0ai` inside `app/services/` with the V4 bitemporal engine and run benchmarks through the FastAPI/LangGraph stack. | Build the V4 engine and evaluation harness exclusively inside `research-lab/` as a Python library + CLI; omit the Personal Digital Twin (PDT) UI/API. | Build the V4 engine, schema, and evaluation harness inside `research-lab/` (`implementation/`, `evaluation/`) with zero imports from `app/`; expose a thin FastAPI/React PDT inspector in Phase P12 (`research-lab/application/`). |
| **Fidelity to V4 Proposal (§§7–10)** | **Poor.** LangGraph agent nodes, prompt wrappers, and `slowapi` middleware confound the controlled comparison between `B0`–`B4`, `P`, and `A1`–`A10`. | **High for §§7–10**, but omits the §11 Personal Digital Twin inspector deliverable (`D4`). | **High across §§7–12.** Isolates the memory engine and evaluation harness for clean `H1`–`H4` experiments while delivering the §11 PDT inspector in Phase P12. |
| **Confounding & Determinism Risk** | **High.** Full agent orchestration breaks record-and-replay extraction caching and inflates token cost. | **Low.** Direct library calls via `MemorySystemAdapter`. | **Low.** Experiments run directly against `research-lab/implementation/` and `research-lab/evaluation/` without going through the demo UI/API. |
| **PostgreSQL Schema Isolation** | **Poor.** Collides with root `app/models/` (SQLModel tables) and `mem0` vector tables. | **Clean.** Dedicated database/schema (`research_lab`). | **Clean.** Dedicated PostgreSQL instance/database for `research-lab/`, completely separate from the root app database. |
| **Engineering Effort** | **Very High.** Requires rewriting existing `app/` routes, auth, and LangGraph state while building the research engine. | **Lowest.** Engine + harness only. | **Moderate & Phased.** Core engine and `H1`–`H4` experiments complete first (P0–P11); thin PDT demo (`P12`) is built only after experiments are frozen. |

---

## 3. Adopted Hybrid Architecture Rules (`D-ARCH`)

1. **Strict Code and Runtime Isolation ([`../PLAN/INDEPENDENCE_BOUNDARY.md`](../PLAN/INDEPENDENCE_BOUNDARY.md)):**
   - Code inside `research-lab/` **must never** import from the root `app/` or `evals/` packages, and root `app/` code must never import from `research-lab/`.
   - `research-lab/` manages its own `pyproject.toml`, `uv.lock`, database migrations, and environment configuration (`.env.example`).
2. **Evaluation Directness ([`../PLAN/ARCHITECTURE.md`](../PLAN/ARCHITECTURE.md)):**
   - All benchmark and ablation runs (`B0`–`B4`, `P`, `A1`–`A10`) execute through the `MemorySystemAdapter` protocol in `research-lab/evaluation/` directly against the engine library and PostgreSQL, never through HTTP/UI layers.
   - `mem0ai` is used inside `research-lab/` **solely** inside the `B3` baseline adapter (`RL-031`), never inside the proposed engine `P`.
3. **Phased Application Delivery (`P12`):**
   - The Personal Digital Twin (PDT) application (`research-lab/application/`) is a thin FastAPI + minimal React/TypeScript inspector over the `research-lab/implementation/` engine (`RL-120`, `RL-121`), scheduled in Phase P12 after `H1`–`H4` experiments (`P10`) are complete.
