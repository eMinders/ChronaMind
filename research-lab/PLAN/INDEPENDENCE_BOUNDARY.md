# INDEPENDENCE_BOUNDARY.md — Research Lab vs. Main ChronaMind Application

**Status:** normative for this phase of the research lab.
**Scope:** declares the rules that keep the Research Lab independent of the main ChronaMind FastAPI/LangGraph application, until integration is explicitly scheduled.

---

## 1. Why this boundary exists

The Research Lab exists to run the open, ablation-controlled memory-engine evaluation described in [`../Graduation_Project_Proposal.md`](../Graduation_Project_Proposal.md) §4, §4.1, §10, and in [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §A, §F.

The research contribution is a comparison between the proposed engine (`P`) and ablated variants (`A1`–`A10`, `A2b`) and public baselines (`B0`–`B4`) under a predeclared protocol with hedged "Supported / Not supported / Inconclusive" decisions per hypothesis.

The main ChronaMind repository is a **FastAPI + LangGraph agent template** (`app/`, `alembic/`, `evals/`, `docker-compose.yml`, `pyproject.toml` with FastAPI, LangGraph, Langfuse, mem0ai) that currently uses an off-the-shelf memory stack. It is not the research artifact and does not yet implement the proposed bi-temporal engine. Making the Research Lab depend on it would:

- Pollute reproducibility: experiments would require booting the whole FastAPI stack and its dependencies (Supabase, Prometheus, Grafana, Langfuse, mem0ai, Atlas Cloud) before any research question can be answered.
- Confuse the research claim: the research must measure the engine, not the surrounding application.
- Pin the lab to the main app's release cadence, Python version, and runtime decisions.
- Prevent the research code from being released as a standalone reproducibility bundle (roadmap §J, deliverable J3 and J9).

Integration is a **downstream** activity, scheduled only after the engine specification is implemented and H1–H4 have decisions.

---

## 2. Working location

- The Research Lab lives at `research-lab/` inside the ChronaMind Git working tree so that reviewers and the supervisor can inspect the research artefacts alongside the main-project context, but it is **not** a nested Git repository and must not become one. There is one Git history (`origin/main`).
- All research code, benchmarks, evaluation harnesses, implementation of the proposed engine, PDT demonstration, reports, and reproducibility bundles are produced **under** `research-lab/`.
- No file outside `research-lab/` is modified by any research task in the current roadmap.
- If, after the research is complete and defended, the team decides to migrate the lab to its own GitHub repository, that migration is scheduled as a dedicated Phase P13 sub-task; it is not implicit in the current initialization.

---

## 3. Mandatory rules

The following rules apply to every task in `research-lab/PLAN/TASK_GRAPH.md` and every pull request touching `research-lab/`.

1. **No runtime import of the main app.** Research Lab code **MUST NOT** import from the top-level Python packages of the main application (`app`, `evals`, `scripts` outside `research-lab/`, `alembic`, `typings`). It **MAY** reference `../AGENTS.md` and the standing research documents at `research-lab/*.md` as documentation.
2. **Own dependency set.** The Lab has its own `research-lab/pyproject.toml`. Dependencies are chosen for the engine, benchmarks, and evaluation harness — not inherited from the main app's FastAPI/LangGraph stack. In particular, the Lab does not pull in `fastapi`, `uvicorn`, `langchain`, `langgraph`, `langfuse`, `mem0ai`, `slowapi`, `prometheus-client`, `starlette-prometheus`, `supabase`, `python-jose`, `passlib`, `bcrypt`, `sqlmodel`, `email-validator` as its own dependencies. (`mem0ai` and `langgraph` may be referenced indirectly only as pinned **baselines** `B3`/`B4` in `research-lab/evaluation/baselines/`, isolated behind adapter interfaces, per roadmap §D.3.)
3. **Own database and schema.** The Lab's PostgreSQL schema (roadmap P4) lives under `research-lab/implementation/schema/` with its own migrations under `research-lab/implementation/migrations/`. It **MUST NOT** reuse or import `alembic/` from the main app, must not share tables with the main app's database, and must not connect to the main app's running database at experiment time.
4. **Own runtime.** Experiments are executed by Python entry-points under `research-lab/evaluation/` and `research-lab/implementation/`. They do not boot the FastAPI application, do not require the `docker-compose.yml` at the repository root, and do not depend on environment variables defined in `.env.example` at the repository root. Any secrets or configuration they need are declared in `research-lab/.env.example` once that file exists.
5. **No modifications to the main project.** No research task modifies files under `app/`, `alembic/`, `evals/`, `scripts/` (outside `research-lab/scripts/` which does not exist yet and would be local to the lab), `docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `uv.lock`, `Makefile`, `run.py`, `prometheus/`, `grafana/`, `.github/workflows/`, `AGENTS.md`, `CLAUDE.md`, `SECURITY.md`, `LICENSE`, `README.md`, `.pre-commit-config.yaml`, `.secrets.baseline`, `.gitignore`, `.gitattributes`, `.mcp.json`, `.python-version`, `skills-lock.json`, `.dockerignore`, `setup.sh`, `setup.ps1`, `typings/`, `docs/` (the main app's `docs/`, as distinct from `research-lab/`'s own documents at the lab root).
6. **No copy of main-app code.** The research engine (`research-lab/implementation/engine/`) is implemented from `../Graduation_Project_Proposal.md` §7, §8 and `../TECHNICAL_SPECIFICATION.md` §3, not by copying from `app/core/langgraph/` or similar.
7. **Independence disclosed in every PR.** Pull requests that touch `research-lab/` must include a checkbox in the PR template confirming "I did not modify any file outside `research-lab/` as part of this change" and "I did not introduce a runtime dependency on the main application."
8. **No research claims from the main app.** The research proposal's hypotheses are evaluated using the Lab's harness only. Running the FastAPI chatbot or the LangGraph agent does **not** count as evidence for H1–H4.

---

## 4. Allowed, bounded interactions

Only these interactions between the Lab and the main app are allowed in this phase:

- The Lab may **read** documentation at the repository root (`AGENTS.md`, `README.md`, `docs/`) to understand project context.
- The Lab may **reuse** the main repo's CI (`.github/workflows/ci.yaml`) by adding a Lab-scoped job that only runs when files under `research-lab/` change. If this is done, the job runs the Lab's own commands and uses the Lab's own dependency file. The CI change is a dedicated Research Lab task (`RL-005`), not an ad-hoc edit.
- The Lab's planning documents may link to main-app files for context only (no behavioural coupling).
- The Lab may propose, in Phase P13 only, an integration track that documents how the engine could later be mounted inside the FastAPI app. Any such integration is explicitly out of scope for the current roadmap.

---

## 5. Enforcement

- Every Research Lab PR must self-report compliance with Section 3 via the PR template at `research-lab/PLAN/templates/pull_request_template.md`.
- A lightweight check, `research-lab/scripts/check_independence.py` (to be authored under task `RL-004`), will scan Research Lab Python files for imports of banned modules and the diff of a PR for modifications outside `research-lab/`. The script is advisory until wired into CI by `RL-005`.
- Any proposed exception to this boundary must be documented as a new Open decision in [`../Methodology_and_Consistency_Checklist.md`](../Methodology_and_Consistency_Checklist.md) §6 and approved by the supervisor; it is not resolved informally in review.

---

## 6. When the boundary ends

This boundary is lifted (and this document is updated) when:

1. H1 – H4 have decisions per the proposal §4.1 decision rules, and
2. The release bundle in `research-lab/release/vX.Y.Z/` has shipped per roadmap §J row J3 and J9, and
3. The supervisor approves an integration track for Phase P13 or beyond.

Until all three conditions hold, the boundary stays in force.
