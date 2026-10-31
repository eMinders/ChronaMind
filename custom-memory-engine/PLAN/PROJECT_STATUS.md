> **Direction notice (2026-10-31).** This document is the **v1** execution record and is **superseded for post-v1.0.0 work** by [`V2/ROADMAP_V2.md`](V2/ROADMAP_V2.md) (waves `W0`–`W8`), which the Project Owner approved as the canonical implementation plan on 2026-10-31 ([`V2/APPROVAL.md`](V2/APPROVAL.md)). §1 below ("Current Repository & Execution State") predates the engine implementation and is stale: the repository, [`../REFACTORING.md`](../REFACTORING.md) and the 2026-10-28 review update win wherever they disagree. The machine-checkable replacement is the evidence-status audit `E-001`. Nothing below is rewritten; it is retained as history.

# Project Status & First-Wave Dispatch (`custom-memory-engine/`)

**Single Source of Truth for Execution State:** This document tracks what is complete, what is currently open on GitHub, and how the team executes the first wave of tasks.

---

## Review update — 2026-10-28 (repository state, verified)

**This document was stale relative to the code.** The sections below describe the
2026-10-25 wave and are retained as history; where they conflict with the
repository, the repository and [`../REFACTORING.md`](../REFACTORING.md) win.

Reconciled against `main` @ `aa2e125` and subsequent work in this session:

- **Phase P4–P7 engine work is implemented, not "Not Yet Executed"** as §1 below
  claims. `src/custom_memory_engine/{engine,schema,clock,queue}/` exists with
  predicates, transactions, retrieval, render, gate, deletion, ingest, clock and
  queue modules, all covered by `tests/` and `tests/integration/`.
- **`RL-040` (full schema) is complete**, not "in review":
  `src/custom_memory_engine/schema/001_full_schema.sql` is applied by
  `schema/migrate.py` and replayed by `tests/integration/test_full_schema.py`.
- **`RL-032` is wired.** `EngineAdapter` routes extraction through
  `ExtractionReplayCache` keyed by `(turn_id, extractor_version, model_id)`;
  `P`, `A1`, `A2` and `A2b` are constructible through `baselines/factory.py`.
  `A8` (`P-nolineage`) and the 7-channel scanner remain open (`RL-070`).
- **`RL-061` consolidation is implemented** (`src/custom_memory_engine/consolidation/`,
  40 new tests), so branches `(S)` and `(C)` are both exercised end-to-end and
  `RL-070` is no longer gated by it.
- **Still genuinely open:** `RL-070` remainder (`A8` toggle, cache invalidation,
  7-channel scanner), `RL-071`, `RL-072` (no `security/` package), `RL-090`–`RL-094`.
- **Open GitHub issues are not all incomplete.** `#42` (`RL-050`) and `#44`
  (`RL-060`) are implemented and verified; `#43` (`RL-051`) is implemented with a
  deliberate scope change (mode classification lives in the caller/predicate, not a
  `query_analyzer.py`). They await peer review, not code.
- **Gated on `RL-010`/`D-01` supervisor signoff:** the real provider-backed
  extractor. The cache, the engine, and all ablations are provider-free and were
  not blocked by it.
- **Verified test state:** `1374 passed, 21 skipped` with the disposable
  database; `1240 passed, 155 skipped` without it. The 21 skips are environment
  gates (FalkorDB, tokenizer assets, dedicated baseline PostgreSQL), not failures.
- **Issue ledger (live, 2026-10-28):** 35 issues total — **29 closed, 6 open**.
  Open: `#42` `RL-050`, `#43` `RL-051`, `#44` `RL-060`, `#45` `RL-061`,
  `#46` `RL-070`, `#47` `RL-071`. Of those, three are implemented and awaiting
  recorded review (`#42`, `#43`, `#44`), one is implemented on an open PR
  (`#45`), one is partial (`#46`), and one has not started (`#47`).
- **Branch workflow:** changes land on a topic branch `rl/<id>-<slug>` and are
  merged through a PR with peer review (`PLAN/CONTRIBUTING.md` §1). `main` has no
  branch protection configured; the workflow is enforced by convention.

## Review update — 2026-10-25

1. **Completed & Closed Wave 1 (`#1`–`#6`: `RL-010`, `RL-012`–`RL-016`) and Phase P0 (`#9`–`#13`: `RL-002`–`RL-006`):**
   - Canonical implementation workspace is root `custom-memory-engine/`, with decision memos in `custom-memory-engine/decisions/` (the duplicated `PLAN/decisions/` copy was removed in `aa2e125`).
   - Reviewed and hardened P0/P1 implementation: added turn-before-session timestamp guard in `schedule_replay`, extended `judge_gate` to enforce optional inter-human `κ ≥ 0.75`, mode coverage, and perturbation gates, added synthetic AST import-violation regressions (`RL-006`), reconciled `TECHNICAL_SPECIFICATION.md` §9.3 with `Graduation_Project_Proposal.md` §§4.1 and 10.6, and synchronized `validation/document_hashes.txt`.
2. **Completed Wave 2 Issues (`#14`–`#19`: `RL-011`, `RL-020`–`RL-024`):**
   - **`#14` (`RL-011`):** Authored [`../evaluation/pilot_plan.md`](../evaluation/pilot_plan.md) and executable deterministic freeze functions in [`../../custom-memory-engine/src/custom_memory_engine/pilot_rules.py`](../../custom-memory-engine/src/custom_memory_engine/pilot_rules.py) covering `D-09`–`D-14`, `D-21`, `D-22`.
   - **`#15` (`RL-020`):** Implemented [`../research/benchmarks/longmemeval/fetch.py`](../research/benchmarks/longmemeval/fetch.py), `MANIFEST.txt`, `LICENSE`, and `README.md`; verified SHA-256 checksums and exact question/anomaly counts on `longmemeval_oracle.json` (`500 / 30 / 56 / 34 / 43`) and `longmemeval_s_cleaned.json` (`500 / 30 / 56`, `211` full-haystack / `39` evidence out-of-order, `76` full-haystack / `44` evidence `question_date < max(session_date)`, `95` tied-session histories).
   - **`#16` (`RL-021`):** Implemented [`../research/benchmarks/locomo/fetch.py`](../research/benchmarks/locomo/fetch.py), `MANIFEST.txt`, `LICENSE` (`CC BY-NC 4.0`), and `README.md`; verified SHA-256 and exact counts (`10` conversations, `1,986` questions, `4` empty-evidence items, categories `282, 321, 96, 841, 446`).
   - **`#17` (`RL-022`):** Implemented deterministic script-first generators for Own Suite `G` ([`../research/benchmarks/own_G/generator.py`](../research/benchmarks/own_G/generator.py), disjoint held-out test injection templates) and Own Suite `I` ([`../research/benchmarks/own_I/generator.py`](../research/benchmarks/own_I/generator.py), zero cross-user exposure contract).
   - **`#18` (`RL-023`):** Implemented deterministic script-first generator for Own Suite `D` ([`../research/benchmarks/own_D/generator.py`](../research/benchmarks/own_D/generator.py), `dev = 20`, `test = 50` scenarios per `D-16`, all 5 lineage structures, 7 in-boundary channels, `A8` positive-control leaks, and tagged re-learning / never-stated controls).
   - **`#19` (`RL-024`):** Implemented deterministic script-first generator for Own Suite `T` ([`../research/benchmarks/own_T/generator.py`](../research/benchmarks/own_T/generator.py), `dev = 20`, `test = 50` scenarios, `E1`–`E3` / `R1`–`R8` patterns, `3:3` `history-dependent` vs. `present-state` questions per scenario, heuristic guards, and deterministic `audit_as_of` grid).
   - See [`../../custom-memory-engine/evidence/VERIFICATION.md`](../../custom-memory-engine/evidence/VERIFICATION.md).

## 1. Current Repository & Execution State

- **Git & Workspace Status:** `research-lab/` is committed and tracked in Git with a clean separation between normative specifications (root), execution plans (`PLAN/`), research references (`reference/`), executed design checks (`validation/`), and src/custom_memory_engine/evaluation package directories (`src/custom_memory_engine/`, `evaluation/`, `research/benchmarks/`, `application/`, `reports/`).
- **Architecture & Team Plan (`RL-001`):** **Done (Owner-approved).** Option C (**Hybrid Architecture**, `D-ARCH`) is locked (`PLAN/ARCHITECTURE.md`, `PLAN/INDEPENDENCE_BOUNDARY.md`).
- **Executed Verification Artifacts:**
  - `validation/mechanical_checks.py` (`ALL HARD CHECKS PASSED` on `Graduation_Project_Proposal.md`, `Methodology_and_Consistency_Checklist.md`, `CHANGELOG.md`).
  - `validation/bitemporal_checks.sql` (`43/43` assertions passed on PostgreSQL `16.15`, logged in `validation/bitemporal_checks.output.txt`).
  - `validation/cp_bounds.py` (exact Clopper–Pearson upper bounds verified against `validation/cp_bounds.output.txt`).
- **Authored, in review (`#25`):** Full twelve-table PostgreSQL schema (`RL-040`) in [`../src/custom_memory_engine/schema/001_full_schema.sql`](../src/custom_memory_engine/schema/001_full_schema.sql), with the invariant map in [`../src/custom_memory_engine/schema/README.md`](../src/custom_memory_engine/schema/README.md).
- **Not Yet Executed:** `T1`–`T5` archival harness (`RL-090`), engine fast/slow paths (`P4`–`P7`), benchmark adapters and synthetic suite generators (`P2`–`P3`), dev-split pilot (`RL-100`), and confirmatory experiments `H1`–`H4` (`RL-101`–`RL-105`).

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
