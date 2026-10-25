# Independence Boundary (`custom-memory-engine/` vs. Root `app/`)

**Authority:** Implements `D-ARCH` (**Option C — Hybrid Architecture**, [`../reference/ARCHITECTURE_DECISION_MATRIX.md`](../reference/ARCHITECTURE_DECISION_MATRIX.md)).

---

## 0. Owner consolidation revision — 2026-10-25

All specifications, benchmarks, evaluation harnesses, and Custom Memory Engine implementation components are consolidated into the single dedicated repository-root directory **`custom-memory-engine/`** (replacing the former split between `research-lab/` and `custom-memory-engine/`).

Isolation from root `app/` is unchanged: independent `custom-memory-engine/pyproject.toml` and `uv.lock`, no imports from `app`, `evals`, or root `scripts`, separate database/credentials (`CME_*`), and no edits to product runtime/configuration. A dedicated `.github/workflows/custom-memory-engine.yaml` verifies this workspace and reference-document consistency, scoped to `custom-memory-engine/**`.

## 1. Core Principle

`research-lab/` is a **self-contained research and engineering workspace** within the ChronaMind repository. It shares Git history with the root FastAPI/LangGraph application (`app/`), but maintains **strict code, dependency, database, and runtime isolation** so that confirmatory experiments (`H1`–`H4`) are never confounded by the root application's middleware, prompts, or `mem0ai` storage layer.

---

## 2. File & Directory Boundary

- **Allowed workspace:** All Research Lab code, SQL schemas, migrations, benchmark adapters, evaluation scripts, tests, and reports **MUST** reside inside `research-lab/`.
- **Prohibited modifications:** Tasks in `research-lab/` **MUST NOT** modify files under `app/`, `evals/`, `scripts/`, `migrations/`, or root configuration files (`pyproject.toml`, `uv.lock`, `Makefile`, `docker-compose.yml`), with the single exception of adding or updating the dedicated Research Lab CI workflow (`.github/workflows/research-lab.yaml`, scoped to `research-lab/**` paths; see §4).
- **Root documentation:** Any legacy or product documentation outside `research-lab/` applies only to `app/` and is non-normative for the Research Lab.

---

## 3. Code, Package, & Database Isolation

### 3.1 Zero Cross-Imports
- Python modules inside `research-lab/` **MUST NOT** import from `app.*`, `evals.*`, or `scripts.*`.
- Root `app/` modules **MUST NOT** import from `research-lab.*`.
- Enforced by an automated import-guard test in `research-lab/` (`RL-006` / CI):
  ```bash
  ! grep -rnE "^\s*(from|import)\s+(app|evals)\b" research-lab/implementation research-lab/evaluation research-lab/application research-lab/research
  ```

### 3.2 Independent Dependency Manifest (`research-lab/pyproject.toml`)
- `research-lab/` manages its own `pyproject.toml` and `uv.lock` (`RL-003`).
- Engine (`implementation/`) and harness (`evaluation/`) dependencies are governed by [`ARCHITECTURE.md`](ARCHITECTURE.md) and `RL-013` (e.g., `psycopg[binary,pool]`, `pgvector`, `pydantic`, `numpy`, `scipy`, `pandas`, `tenacity`, `pytest`).
- `mem0ai` may be installed only as an optional dependency group (`baselines`) and imported **exclusively** inside the `B3` baseline adapter (`research-lab/evaluation/baselines/b3_mem0.py`, `RL-031`), never inside `research-lab/implementation/`.
- Web/API libraries (`fastapi`, `uvicorn`) are scoped to the Phase P12 Personal Digital Twin demonstrator (`research-lab/application/`, `RL-120`) and are never invoked on the `H1`–`H4` evaluation path.

### 3.3 Dedicated PostgreSQL Instance & Credentials
- `research-lab/docker-compose.yml` (`RL-004`) provisions an isolated PostgreSQL instance (default host port `5433` to avoid colliding with the root app's `5432`) with `pgvector` and `btree_gist` enabled.
- `research-lab/.env.example` defines `RL_DATABASE_URL`, `RL_LLM_API_KEY`, and `RL_EMBEDDING_API_KEY` (prefixed with `RL_`), separate from the root `.env.*` files.

---

## 4. Continuous Integration Boundary (`D-LAB-CI`, `RL-005`)

- The Research Lab uses a dedicated GitHub Actions workflow at `.github/workflows/research-lab.yaml` triggered on `push` and `pull_request` events touching `research-lab/**` or `.github/workflows/research-lab.yaml`.
- This workflow runs:
  1. `python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md`
  2. `diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt`
  3. `psql -v ON_ERROR_STOP=1 -f validation/bitemporal_checks.sql` against the pinned PostgreSQL service container
  4. `ruff check`, `pyright`, and `pytest` across `research-lab/` once `RL-003` is merged.
- It does **not** alter or depend on `.github/workflows/ci.yaml` for the root application.
