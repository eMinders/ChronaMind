# Project Status & First-Wave Dispatch (`research-lab/`)

**Single Source of Truth for Execution State:** This document tracks what is complete, what is currently open on GitHub, and how the team executes the first wave of tasks.

---

## 1. Current Repository & Execution State

- **Git & Workspace Status:** `research-lab/` is committed and tracked in Git with a clean separation between normative specifications (root), execution plans (`PLAN/`), research references (`reference/`), executed design checks (`validation/`), and implementation/evaluation package directories (`implementation/`, `evaluation/`, `research/benchmarks/`, `application/`, `reports/`).
- **Architecture & Team Plan (`RL-001`):** **Done (Owner-approved).** Option C (**Hybrid Architecture**, `D-ARCH`) is locked (`PLAN/ARCHITECTURE.md`, `PLAN/INDEPENDENCE_BOUNDARY.md`).
- **Executed Verification Artifacts:**
  - `validation/mechanical_checks.py` (`ALL HARD CHECKS PASSED` on `Graduation_Project_Proposal.md`, `Methodology_and_Consistency_Checklist.md`, `CHANGELOG.md`).
  - `validation/bitemporal_checks.sql` (`43/43` assertions passed on PostgreSQL `16.15`, logged in `validation/bitemporal_checks.output.txt`).
  - `validation/cp_bounds.py` (exact Clopper–Pearson upper bounds verified against `validation/cp_bounds.output.txt`).
- **Not Yet Executed:** Full 12-table PostgreSQL schema (`RL-040`), `T1`–`T5` archival harness (`RL-090`), engine fast/slow paths (`P4`–`P7`), benchmark adapters and synthetic suite generators (`P2`–`P3`), dev-split pilot (`RL-100`), and confirmatory experiments `H1`–`H4` (`RL-101`–`RL-105`).

---

## 2. Active First-Wave GitHub Issues (`Phase P1`)

Six Phase P1 decision issues are currently published and open on GitHub under milestone **`P1 Resolve open decisions`**. All six can be worked in parallel right now and require **zero paid LLM API calls**:

| GitHub Issue | Task ID | Title | Decisions Covered | Proposed Owner | Peer Reviewer |
|---|---|---|---|---|---|
| [`#1`](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/1) | `RL-010` | Prepare research-scope and supervisor decision records | `D-01`–`D-08`, `D-27` | Member 1 (`R-LEAD`) | Member 3 (`R-METHOD`) |
| [`#2`](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/2) | `RL-012` | Verify PostgreSQL and pgvector compatibility evidence | `D-18`, `D-25` | Member 4 (`R-DB`) | Member 2 (`R-ENGINE`) |
| [`#3`](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/3) | `RL-013` | Select and verify the engine runtime | `D-17`, `D-20` | Member 2 (`R-ENGINE`) | Member 5 (`R-APP`) |
| [`#4`](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/4) | `RL-014` | Prepare A2b and Suite D cardinality decisions | `D-15`, `D-16` | Member 3 (`R-METHOD`) | Member 1 (`R-LEAD`) |
| [`#5`](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/5) | `RL-015` | Determine ethics requirements for the optional usability study | `D-19` | Member 5 (`R-APP`) | Member 1 (`R-LEAD`) |
| [`#6`](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/6) | `RL-016` | Prepare paired D-23/D-24 and suite-release decisions | `D-23`, `D-24`, `D-26` | Member 3 (`R-METHOD`) & Member 4 (`R-DB`) | Member 1 (`R-LEAD`) |

### Immediate Parallel Phase P0 Tasks (Ready to Publish / Execute)

Alongside `#1`–`#6`, the Phase P0 workspace tasks (`RL-002`–`RL-006`) have no external blockers:
- **`RL-002`**: Verify `research-lab/` directory scaffold and `.gitignore` rules (`Member 1`).
- **`RL-003`**: Author `research-lab/pyproject.toml`, `uv.lock`, and `.env.example` once `RL-013` confirms Python 3.12 + `psycopg` v3 (`Member 2`).
- **`RL-004`**: Author `research-lab/docker-compose.yml` (`postgres:16-alpine` + `pgvector` + `btree_gist`) once `RL-012` pins the image digest (`Member 4`).
- **`RL-005`**: Author `.github/workflows/research-lab.yaml` CI pipeline (`Member 5`).
- **`RL-006`**: Configure `ruff`, `pyright`, and import-boundary checks for `research-lab/` (`Member 5`).

---

## 3. Team Handoff & Dispatch Checklist

1. **Claiming an issue:** Follow [`CONTRIBUTING.md`](CONTRIBUTING.md) — self-assign the GitHub issue, create branch `rl/<id>-<slug>` (e.g., `rl/012-pg-pgvector-verify`), and keep all changes strictly inside `research-lab/`.
2. **Deliverables for `#1`–`#6`:**
   - Write the decision record(s) under `research-lab/PLAN/decisions/`.
   - Update the corresponding status rows in [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md) and [`../TECHNICAL_SPECIFICATION.md`](../TECHNICAL_SPECIFICATION.md) §2.
   - Run the verification commands in [`../README.md`](../README.md) §3 before opening a PR.
3. **Unblocking Wave 2 (`RL-011`, `P2`, `P3`, `P4`):**
   - Closing `#1` (`RL-010`) and `#4` (`RL-014`) unblocks `RL-011` (authoring `evaluation/pilot_plan.md`).
   - Closing `#2` (`RL-012`) and `#3` (`RL-013`) unblocks `RL-003`, `RL-004`, `RL-030` (harness skeleton), and `RL-040` (full 12-table PostgreSQL DDL).
   - Closing `#4` (`RL-014`) and `#6` (`RL-016`) unblocks `RL-020`–`RL-024` (benchmark fetchers and own-suite generators).
