# Team Roles & Task Assignments (`research-lab/`)

**Authority:** Defines the 5-member role matrix and primary ownership across Phases `P0`–`P13`.

> **Direction notice (2026-11-01, `V2-D57`).** The **peer-review pairings** in this document are a `P0`–`P13`
> artefact and are **superseded**: AI review acting on the Project Owner's behalf is the review gate for every change,
> and human peer review is not a prerequisite for merging (`PLAN/CONTRIBUTING.md` §1.1, `V2-D57`). The role matrix
> below (who owns what) still binds. Nothing below is rewritten; it is retained as the v1 record.
**Anonymity Rule:** In compliance with `validation/mechanical_checks.py` §5, documents inside `research-lab/` use role identifiers (`Member 1`–`Member 5`) rather than personal names. Map role IDs to GitHub handles when assigning issues on GitHub.

---

## 1. Role Definitions

| Role ID | Code | Primary Responsibilities | Default Peer Reviewer |
|---|---|---|---|
| **Member 1** | `R-LEAD` | Project coordination, supervisor decision records (`RL-010`), repository scaffold (`RL-001`, `RL-002`), traceability maintenance, and final thesis compilation (`RL-130`). | Member 3 (`R-METHOD`) |
| **Member 2** | `R-ENGINE` | Engine runtime (`RL-013`, `RL-003`), transactional supersession/archival (`RL-042`), clock/queue (`RL-044`), context renderer (`RL-051`), 5-check validation gate (`RL-060`), consolidation (`RL-061`), lineage deletion (`RL-070`), baselines `B0`–`B4` (`RL-031`), replay cache (`RL-032`), `H3`/`H4` runs (`RL-103`, `RL-105`), and reproducibility pack (`RL-131`). | Member 4 (`R-DB`) |
| **Member 3** | `R-METHOD` | Statistical protocol & pilot plan (`RL-011`, `RL-014`, `RL-016`), synthetic suite generators `T`, `G`, `D`, `I` (`RL-022`–`RL-024`), LLM judge validation (`RL-033`), statistical inference module (`RL-034`), dev-split pilot freeze (`RL-100`), `H1` run (`RL-101`), confirmatory analysis (`RL-110`), and suite release (`RL-132`). | Member 1 (`R-LEAD`) |
| **Member 4** | `R-DB` | PostgreSQL/`pgvector`/`btree_gist` verification (`RL-012`, `RL-004`), 12-table DDL & migrations (`RL-040`, `RL-041`), temporal query predicates (`RL-043`), hybrid RRF retrieval (`RL-050`), RLS security (`RL-071`), `LongMemEval` & split freeze (`RL-020`, `RL-025`, `RL-026`), `T1`–`T5` & `P9` test matrix (`RL-090`, `RL-091`, `RL-093`, `RL-094`), `H2` run (`RL-102`), and latency/cost profiling (`RL-111`). | Member 2 (`R-ENGINE`) |
| **Member 5** | `R-APP` | CI & lint setup (`RL-005`, `RL-006`), `LoCoMo` fetch (`RL-021`), evaluation harness skeleton (`RL-030`), audit log redaction guard (`RL-072`), dual-annotator human `κ` audit (`RL-104`), Personal Digital Twin FastAPI + React Inspector (`RL-120`, `RL-121`), and optional `H5` ethics/study (`RL-015`, `RL-122`). | Member 1 (`R-LEAD`) / Member 2 (`R-ENGINE`) |

---

## 2. Phase-by-Phase Assignment Matrix

| Phase | Member 1 (`R-LEAD`) | Member 2 (`R-ENGINE`) | Member 3 (`R-METHOD`) | Member 4 (`R-DB`) | Member 5 (`R-APP`) |
|---|---|---|---|---|---|
| **`P0` (Bootstrap)** | `RL-001` (Done), `RL-002` | `RL-003` | — | `RL-004` | `RL-005`, `RL-006` |
| **`P1` (Decisions)** | `RL-010` (`#1`) | `RL-013` (`#3`) | `RL-011`, `RL-014` (`#4`), `RL-016` (`#6`, co-owner) | `RL-012` (`#2`), `RL-016` (`#6`, co-owner) | `RL-015` (`#5`) |
| **`P2` (Benchmarks)** | Review `RL-026` freeze | — | `RL-022`, `RL-023`, `RL-024` | `RL-020`, `RL-025`, `RL-026` | `RL-021` |
| **`P3` (Harness/Stats)** | Review `RL-033`, `RL-034` | `RL-031`, `RL-032` | `RL-033`, `RL-034` | Review `RL-030` | `RL-030` |
| **`P4` (Schema/Core)** | — | `RL-042`, `RL-044` | — | `RL-040`, `RL-041`, `RL-043` | — |
| **`P5` (Retrieval)** | — | `RL-051` | — | `RL-050` | — |
| **`P6` (Gate/Consol.)** | — | `RL-060`, `RL-061` | — | Review `RL-061` | — |
| **`P7` (Deletion/RLS)** | — | `RL-070` | — | `RL-071` | `RL-072` |
| **`P8` (Optional)** | Gate check (`D-04`–`D-07`) | `RL-081` (if enabled) | `RL-080` (if enabled) | `RL-082` (if enabled) | `RL-083` (if enabled) |
| **`P9` (Test Matrix)** | Audit `TRACEABILITY_MATRIX.md` | `RL-092`, co-sign `RL-094` | — | `RL-090`, `RL-091`, `RL-093`, `RL-094` | — |
| **`P10` (Pilot & `H1`–`H4`)** | Co-sign `RL-100` freeze | `RL-103` (`H3`), `RL-105` (`H4`) | `RL-100` (Pilot), `RL-101` (`H1`) | `RL-102` (`H2`) | `RL-104` (Human `κ` audit) |
| **`P11` (Analysis)** | Review `RL-110` | — | `RL-110` | `RL-111` | — |
| **`P12` (PDT App)** | Review `RL-121` | Support engine API | — | — | `RL-120`, `RL-121`, `RL-122` (if enabled) |
| **`P13` (Thesis/Release)** | `RL-130` | `RL-131` | `RL-132` | Contribute §8/§9/§10.3 tables | Contribute §11 PDT section |

---

## 3. Dual-Annotator Pairing (`RL-033`, `RL-104`)

To prevent bias during LLM judge validation (`RL-033`) and synthetic suite auditing (`RL-104`):
- **Annotator A:** Member 5 (`R-APP`)
- **Annotator B:** Member 1 (`R-LEAD`)
- **Arbitration & `Cohen's κ` calculation:** Member 3 (`R-METHOD`, who authors the generator scripts and does not serve as a primary blind annotator).
