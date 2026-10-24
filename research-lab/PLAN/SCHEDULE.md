# 24-Week Execution Schedule (`research-lab/`)

**Scope:** Maps Phases `P0`–`P13` (`RL-001`–`RL-132`) onto a 24-week (two-semester) academic calendar with explicit review gates (`J4`–`J9`) and schedule slack.

---

## 1. Milestone & Gate Calendar

| Weeks | Target Phases | Core Tasks | Exit Milestone / Gate |
|---|---|---|---|
| **Weeks 1–2** | `P0` (Bootstrap) & `P1` (Open Decisions) | `RL-002`–`RL-006`, `RL-010`–`RL-016` (`#1`–`#6`) | **`D-P0` & `D-P1` (`Gate J4`):** CI green, all pre-implementation decisions signed in `PLAN/decisions/`, `evaluation/pilot_plan.md` merged. |
| **Weeks 3–5** | `P2` (Benchmarks/Suites), `P3` (Harness/Stats), & `P4` (Schema/Core) | `RL-020`–`RL-024`, `RL-030`, `RL-032`, `RL-034`, `RL-040`–`RL-044` | **`D-P4`:** Full 12-table PostgreSQL DDL, supersession/archival procedures, query predicates, and harness skeleton passing unit tests. |
| **Weeks 6–8** | `P2` (Adapters & Split Freeze), `P3` (Baselines & Judge), & `P5` (Retrieval) | `RL-025`, `RL-026`, `RL-031`, `RL-033`, `RL-050`, `RL-051` | **`D-P2` (`Gate J5`), `D-P3`, & `D-P5`:** Dev/test splits frozen (`splits/FREEZE_LIST.md`), baselines `B0`–`B3` (+ `B4` dev check) and judge validated, hybrid RRF retrieval and context renderer complete. |
| **Weeks 9–11** | `P6` (Validation Gate & Consolidation) & `P7` (Deletion & RLS Security) | `RL-060`, `RL-061`, `RL-070`–`RL-072` (plus `P8` only if enabled) | **`D-P6` & `D-P7`:** 5-check gate, episodic → semantic consolidation (`MEMORY_DERIVATION`), single-transaction lineage deletion, 7-channel scanner, and `FORCE ROW LEVEL SECURITY` complete. |
| **Weeks 12–14** | `P9` (Mandatory Testing Matrix & Verification Gate) | `RL-090` (`T1`–`T5`), `RL-091` (Invariants `1`–`9`), `RL-092` (Clock/Queue), `RL-093` (8-worker load & HNSW), `RL-094` | **`D-P9` (`Gates J6 & J7`):** `T1`–`T5` archival suite green and all mandatory rows in `PROJECT_ROADMAP.md` §G verified in `implementation/tests/report.md`. |
| **Weeks 15–16** | `P10` Part 1 (Development-Split Pilot & Parameter Freeze) | `RL-100`, `RL-104` (Dual-annotator `κ` audit) | **`Gate J8`:** `evaluation/runs/pilot/FREEZE_LIST.md` signed (`B_mem`, `k`, `δ_pres`, `δ_luar`, `u*`, `A8` share, `B4` inclusion, `LongMemEval_S` subset frozen). |
| **Weeks 17–19** | `P10` Part 2 (One-Shot Confirmatory Test Runs `H1`–`H4`) | `RL-101` (`H1`), `RL-102` (`H2`), `RL-103` (`H3`), `RL-105` (`H4`) | **`D-P10` (`Gate J9`):** Immutable test-split run artifacts committed under `evaluation/runs/<ts>/H{1,2,3,4}/`. |
| **Weeks 20–22** | `P11` (Statistical Analysis) & `P12` (Personal Digital Twin App) | `RL-110`, `RL-111`, `RL-120`, `RL-121` (`RL-122` if enabled) | **`D-P11` & `D-P12`:** Confirmatory tables/figures generated (`reports/analysis/`) and PDT Memory Inspector demonstrated (`application/`). |
| **Weeks 23–24** | `P13` (Thesis, Reproducibility Pack, & Suite Release) | `RL-130`, `RL-131`, `RL-132` | **`D-P13`:** Final graduation thesis, clean-checkout reproduction log, and public suite release package complete. |

---

## 2. Schedule Protection & De-Scoping Rules

If an upstream phase slips by more than one week, apply these predeclared de-scoping steps in order (never compromising `H1`–`H4` or Invariants `1`–`10`):

1. **Drop Phase `P8` (`RL-080`–`RL-083`) and `RL-122` (`H5` usability study):** Already recommended **OFF** by default in `DECISIONS_REGISTER.md` (`D-04`–`D-08`).
2. **Drop exploratory ablation `A2b` (`D-15`, `RL-014`):** Keep `A2` (`P-nohistory`), which is the sole confirmatory contrast for `H1`.
3. **Exclude conditional Baseline `B4` (Graphiti) under `D-21`:** If Neo4j/FalkorDB integration or LLM extraction cost blocks `RL-031`/`RL-100`, document the measured obstacle per `Graduation_Project_Proposal.md` §10.1 and proceed with `B0`, `B1`, `B2`, `B2h`, and `B3` (`Mem0`).
4. **Use lightweight server-rendered HTML for the Phase `P12` PDT Inspector (`D-20`):** As permitted by `Graduation_Project_Proposal.md` §9 ("A lighter framework is an acceptable fallback if the application threatens the evaluation").
