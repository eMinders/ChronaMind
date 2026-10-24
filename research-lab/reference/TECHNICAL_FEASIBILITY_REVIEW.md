# Technical Feasibility Review

**Subject:** Empirical and engineering feasibility of `Graduation_Project_Proposal.md` (V4) and `TECHNICAL_SPECIFICATION.md`  
**Role:** Reference analysis for Phase P1 decision resolution and Phase P4–P10 execution. Authoritative implementation decisions are tracked in [`../PLAN/DECISIONS_REGISTER.md`](../PLAN/DECISIONS_REGISTER.md).

---

## 1. Executive Summary

- **Design-level readiness:** **High.** The V4 proposal, methodology checklist, and technical specification form a tight, falsifiable specification. All four primary hypotheses (`H1`–`H4`) have predeclared decision rules, cluster units, and multiplicity controls.
- **Implementation readiness:** **Greenfield.** Only two artifacts have been executed to date:
  1. `validation/bitemporal_checks.sql` (43 assertions passed on PostgreSQL 16.15 in a single-table reduced schema with an emulated clock).
  2. `validation/cp_bounds.py` (exact Clopper–Pearson upper bounds for zero-leakage sample sizes).
- **Primary feasibility bottlenecks:**
  1. **Evaluation cost & throughput:** Ingesting `LongMemEval_S` (~500 histories × ~115k tokens ≈ 57M input tokens before prompt overhead) requires strict record-and-replay caching (`RL-032`) and budget validation during the pilot (`D-01`, `D-21`).
  2. **Database concurrency under `btree_gist`:** Invariant 4 (`EXCLUDE USING gist` over `(user_id, subject_key, predicate_key, valid_range, recorded_range)`) is proven sequentially in the reduced schema (`R1` close-then-insert), but requires multi-worker contention testing (`RL-093`, `D-18`) with per-user advisory locks (`pg_advisory_xact_lock`).
  3. **Baseline B4 (Graphiti) operational overhead:** Graphiti requires a separate graph database (Neo4j or FalkorDB) and multi-step LLM extraction/resolution calls during ingestion. Its inclusion is conditional on passing the `D-21` pilot feasibility and cost gate (`RL-031`, `RL-100`).

---

## 2. Subsystem Feasibility Assessment

### 2.1 Bi-Temporal Relational Core & Storage Layer

| Mechanism | Verification Status | Engineering Risk | Mitigation & Planned Verification |
|---|---|---|---|
| **Invariants 2 & 4 (`CHECK` + `btree_gist` exclusion)** | Executed in reduced schema (`validation/bitemporal_checks.sql`, PG 16.15). | **Low–Medium.** GiST index maintenance overhead and lock contention if two workers update the same user concurrently. | Enforce per-user serialization on the slow path via `pg_advisory_xact_lock(hashtext(user_id))` (`TECHNICAL_SPECIFICATION.md` §3.4, §8) and verify under 8-worker load in `RL-093` (`D-18`). |
| **Invariant 3 (`clock_now()` & update-guard trigger)** | Executed in reduced schema with session GUC `bt.clock`. | **Low.** `SET LOCAL app.clock` is transaction-scoped in PostgreSQL and clean for evaluation replay. | Restrict clock setting to the injectable clock module (`RL-044`); verify monotonicity (`R3`) and replay determinism in `RL-092`. |
| **Invariant 1 (Traceability deferred trigger)** | Specified; not yet executed. | **Low.** Standard PostgreSQL `CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED`. | Implement in `RL-040` and test commit-time rejection of ungrounded `Active` versions in `RL-091`. |
| **Invariant 7 (Row-Level Security)** | Specified; not yet executed. | **Low–Medium.** Table owners and superusers bypass RLS unless `FORCE ROW LEVEL SECURITY` and a non-superuser runtime role are used. | Configure non-superuser `app_role` + `FORCE ROW LEVEL SECURITY` in `RL-040`/`RL-071`; test in `RL-091`. |
| **Invariant 8 (Deletion closure across 7 channels)** | Reduced-schema key deletion tested; full closure not yet executed. | **Medium–High.** Recursive CTE over `MEMORY_DERIVATION` must atomically purge `MEMORY_VERSION`, `MEMORY_EMBEDDING`, FTS vectors, queue payloads, and caches, and re-derive surviving multi-source summaries. | Bound derivation depth (`≤ 3`), lock user scope during deletion (`RL-070`), key any caches by `(user_id, deletion_epoch)`, and verify via `T5` (`RL-090`) and suite `D` (`RL-105`). |
| **Invariant 10 (V4 Archival Precondition `(S) ∨ (C)`)** | Specified in V4; not yet executed (`D-27`). | **Medium.** Branch `(C)` requires verifying that an `Active` derived replacement `r'` covers `r`'s valid range (`V(r') @> V(r)`). | Enforce inside the transactional `archive_version` procedure (`RL-042`, `RL-061`) and verify with the `T1`–`T5` regression harness (`RL-090`). |

### 2.2 Fast Path & Slow Path Pipeline

- **Fast path (`retrieve` + context assembly):** Hybrid retrieval combining `pgvector` exact cosine distance (per-user filtered) and PostgreSQL full-text search (`tsvector` + GIN, `ts_rank_cd`) fused via Reciprocal Rank Fusion (`k = 60`) is well within standard PostgreSQL capabilities for per-user memory stores (`10²`–`10⁴` rows per user). HNSW approximate indexing is needed only for the scalability benchmark (`RL-050`, `RL-093`).
- **Slow path (`SKIP LOCKED` job queue + extraction + gate):** Using a PostgreSQL `JOB` table with `SELECT ... FOR UPDATE SKIP LOCKED` avoids adding Redis/Celery to the deletion boundary (`Graduation_Project_Proposal.md` §7.1, §9). The main risk is LLM extraction latency (1–3 LLM calls per turn), which is mitigated in evaluation by record-and-replay extraction caching (`RL-032`).

### 2.3 Evaluation Protocol, Benchmarks, and Baselines

| Component | Feasibility & Constraints | Action / Gate |
|---|---|---|
| **LongMemEval (`H2`)** | 500 questions; `S` variant is ~115k tokens/history. Data contains 34/500 questions with out-of-order `haystack_dates` and 43/500 questions where `question_date < max(session_date)`. | Stable-sort sessions by timestamp before ingestion; set `ρ = max(question_date, last_turn_ts)` (`Graduation_Project_Proposal.md` §7.4). If full `S` ingestion exceeds budget (`D-01`), freeze a seeded stratified subset in `RL-100` (`D-22`). |
| **LoCoMo (Descriptive)** | 10 conversations, 1,986 questions, CC BY-NC 4.0 license. Only 10 independent clusters — insufficient for cluster-bootstrap hypothesis testing. | Use strictly for secondary descriptive reporting per conversation (`RL-021`, `RL-025`). |
| **Own Suites (`T`, `G`, `D`, `I`)** | Script-first generation ensures ground-truth labels and validity intervals are known mechanically before LLM dialogue rendering. | Implement deterministic seeded generators in `RL-022`–`RL-024`; audit a sample with two human annotators (Cohen's κ) in `RL-104`; freeze dev/test splits in `RL-026`. |
| **Baseline B3 (Mem0)** | Open-source (`Apache-2.0`), already used in the parent repository (`mem0ai`), but upstream head (`0724291`) differs from the 2025 paper's prompt/update pipeline. | Pin exact version in `RL-031`, configure with the same frozen LLM and embedder (`D-01`, `D-02`), and report results as specific to that pinned release. |
| **Baseline B4 (Graphiti)** | Requires Neo4j/FalkorDB and performs multi-call LLM graph extraction. | Gate inclusion on the `D-21` pilot rule (`RL-031`, `RL-100`): include only if it runs end-to-end with the frozen model within budget on the dev split. |

---

## 3. Feasibility Decision Mapping (`S-01`–`S-07`)

The feasibility review identified seven structural questions (`S-01`–`S-07`). Their mapping to the canonical decision register ([`../PLAN/DECISIONS_REGISTER.md`](../PLAN/DECISIONS_REGISTER.md)) is summarized below:

| Feasibility ID | Topic | Canonical Decision IDs | Status & Resolution Path |
|---|---|---|---|
| **S-01** | Core scope vs. 6-month / 5-member capacity | `D-04`, `D-05`, `D-06`, `D-07`, `D-08` | **Needs supervisor signoff (`RL-010`).** Recommended: keep Persona (`A10`), retention ranking (`A9`), BM25, Redis/Celery, and usability study (`H5`) **OFF** so the team focuses on `H1`–`H4` and invariants 1–10. |
| **S-02** | Graphiti (`B4`) baseline inclusion | `D-21` | **Pilot-derived (`RL-031`, `RL-100`).** Conditional on end-to-end adapter stability and pilot ingestion cost on the dev split. |
| **S-03** | LLM & embedding budget and LongMemEval subset | `D-01`, `D-02`, `D-03`, `D-22` | **Needs supervisor signoff (`RL-010`) + Pilot (`RL-100`).** Record-and-replay cache (`RL-032`) mandatory before running public benchmarks. |
| **S-04** | Own-suite generation & dual-annotator workload | `D-14`, `D-16`, `D-23`, `D-24`, `D-26` | **Open (`RL-014`, `RL-016`) + Pilot (`RL-100`).** Keep pilot suite sizes small for pipeline validation, then freeze test-split sizes via `RL-100`. |
| **S-05** | `btree_gist` exclusion constraint under concurrent workers | `D-18`, `D-25` | **Open (`RL-012`) + P9 load test (`RL-093`).** Combine `EXCLUDE USING gist` with per-user advisory locks (`pg_advisory_xact_lock`); if GiST contention fails under load, fall back to `SERIALIZABLE` + retry per `D-18`. |
| **S-06** | Transactional deletion closure + multi-source re-derivation | `D-13`, `D-16` | **Open (`RL-014`, `RL-070`).** Bound derivation depth to `≤ 3` and keep synchronous re-derivation minimal (episodic → semantic/summary). |
| **S-07** | Repository architecture (App integration vs. Standalone Lab vs. Hybrid) | `D-ARCH` | **Resolved (Owner decision).** Option C (**Hybrid Architecture**) is adopted: `research-lab/` is a self-contained implementation and evaluation workspace (`PLAN/ARCHITECTURE.md`), isolated from `app/` at runtime and in code (`PLAN/INDEPENDENCE_BOUNDARY.md`). |
