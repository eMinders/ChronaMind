# Traceability Matrix

This matrix links the audit findings, literature evidence, database invariants (`1`–`10`), hypotheses (`H1`–`H4`), deliverables (`D1`–`D5`), and quality gates (`J1`–`J10`) to their normative specifications, implementation tasks, and verification artifacts.

---

## 1. Audit Findings & Source Evidence Traceability

| Source / Audit Artifact | Scope | Status | Authoritative Location |
|---|---|---|---|
| **V1–V4 Flaw & Patch Audit** | 16 original proposal flaws + V2/V3/V4 targeted fixes (including the V4 historical-memory archival fix and `P − A2` primary `H1` contrast) | `COMPLETE` | [`reference/Research_and_Fact_Check_Audit.md`](reference/Research_and_Fact_Check_Audit.md) §§1, 8, 9 |
| **41-Reference Evidence Ledger** | Verification of references `[1]`–`[41]` with explicit per-row *Reading depth* labels and claim mappings | `COMPLETE` | [`reference/Source_Evidence_Ledger.md`](reference/Source_Evidence_Ledger.md) §§A, B, C, D |
| **Stage-2 Full-Text Corpus (`[20]`–`[29]`)** | Extracted Markdown full texts and passage audits for 10 2026 preprints | `COMPLETE` | [`reference/sources/fulltext/`](reference/sources/fulltext/) & [`reference/Source_Evidence_Ledger.md`](reference/Source_Evidence_Ledger.md) §E |
| **Offline Source Snapshots** | Fetched arXiv/ACL/GitHub/PostgreSQL/NIST pages and READMEs | `COMPLETE` | [`reference/sources/pages/`](reference/sources/pages/) |
| **Mechanical Integrity Checks** | Citation bijection, reference SHA-256 guard, table shapes, section cross-references, and stale-term guards | `COMPLETE` | [`validation/mechanical_checks.py`](validation/mechanical_checks.py) |

---

## 2. Database Invariants (`1`–`10`) Traceability

| Inv. # | Summary (Proposal §8) | Enforcement Mechanism (`TECHNICAL_SPECIFICATION.md`) | Implementation Task | Verification Artifact / Test Task | Current Status |
|---|---|---|---|---|---|
| **1** | Every `Active`/`Archived` version has `≥ 1` `MEMORY_EVIDENCE` row (extracted) or `≥ 1` outgoing `MEMORY_DERIVATION` link (derived). | `DEFERRABLE INITIALLY DEFERRED` constraint trigger on `MEMORY_VERSION` (§4). | `RL-040`, `RL-042` | Full schema: `implementation/tests/test_full_schema.py` (`RL-040`); suite expansion: `RL-091` | `PARTIAL (DDL trigger executed; RL-091 pending)` |
| **2** | `valid_range` non-empty and lower-bounded; `recorded_range IS NULL` iff staging (`candidate`, `conflict_pending`, `rejected`), else non-empty and lower-bounded. | `CHECK` constraints `valid_wellformed`, `recorded_iff_believed`, `recorded_wellformed` (§3.3). | `RL-040` | Reduced schema: `validation/bitemporal_checks.sql` (`COMPLETE`); Full schema: `implementation/schema/bitemporal_on_full.sql` (`RL-040`); `RL-091` remaining for engine-role tests. | `PARTIAL (Reduced and full SQL executed)` |
| **3** | Separate valid and recorded clocks; `recorded_from` assigned at commit from monotonic per-user clock (`R3`); only `status` and `upper(recorded_range)` may change post-insert. | `clock_now()` + `BEFORE UPDATE` trigger `guard_update()` (§3.4, §8). | `RL-040`, `RL-044` | Reduced schema: `validation/bitemporal_checks.sql` (`COMPLETE`); Full schema: `implementation/schema/bitemporal_on_full.sql` (`RL-040`); Replay clock test: `RL-092` (`PLANNED`). | `PARTIAL (Reduced and full SQL executed; replay pending)` |
| **4** | Single-valued predicates: no two `Active`/`Archived` versions for the same `(user_id, subject_key, predicate_key)` overlap in both `valid_range` and `recorded_range`. | `EXCLUDE USING gist (... valid_range WITH &&, recorded_range WITH &&)` + `R1` close-then-insert + per-user advisory lock (§3.3, §8). | `RL-040`, `RL-042` | Reduced schema: `validation/bitemporal_checks.sql` (`COMPLETE`); Full schema: `implementation/schema/bitemporal_on_full.sql` (`RL-040`); 8-worker concurrency load test: `RL-093` (`PLANNED`). | `PARTIAL (Reduced and full SQL executed; load pending)` |
| **5** | Superseded beliefs (`NOT upper_inf(recorded_range)`) are retained for audit until deletion/purge; never overwritten in place (`R1`, `R2`). | Transactional `supersede_single` / `commit_candidate` procedures (§3.4). | `RL-042` | Reduced schema worked example E1–E3: `validation/bitemporal_checks.sql` (`COMPLETE`); Full engine: `RL-091` (`PLANNED`). | `PARTIAL (Reduced SQL executed)` |
| **6** | Staging rows (`candidate`, `conflict_pending`, `rejected`) have `recorded_range IS NULL` and are never returned by any query predicate. | Invariant 2 `CHECK` + query functions filtering on `status` and `recorded_range` (§3.5). | `RL-040`, `RL-043` | Reduced schema: `validation/bitemporal_checks.sql`; Full schema: `implementation/schema/bitemporal_on_full.sql`; all four predicates + unprivileged RLS: `implementation/tests/test_predicates_db.py` (`RL-043`). | `PARTIAL (DDL and predicate coverage; full RL-091/runtime role gates pending)` |
| **7** | Every user-data table carries `user_id`, enforces `FORCE ROW LEVEL SECURITY` for the runtime role, and rejects unscoped queries. | PostgreSQL RLS policies + service-boundary guard (§3.1, Proposal §7.8). | `RL-040`, `RL-071` | DDL `ENABLE`/`FORCE` + isolation policies: `implementation/schema/001_full_schema.sql` (`RL-040`); runtime grants and Suite `I`: `RL-071` | `PARTIAL (RLS scaffolding executed)` |
| **8** | Deletion removes all versions of `memory_key` (`R6`), transitive `MEMORY_DERIVATION` closure (or re-derives surviving multi-source artifacts), embeddings, FTS entries, queue payloads, and caches; `AUDIT_EVENT` holds no content. | Transactional `delete_memory` procedure with `deletion_epoch` check (§7.2). | `RL-070` | Reduced schema key deletion: `validation/bitemporal_checks.sql`; Full 7-channel closure: `RL-090` (`T5`), `RL-105` (`H4`). | `PARTIAL (Key delete only; closure PLANNED)` |
| **9** | Retried slow-path jobs cannot create duplicate memory versions. | Unique constraints on `JOB.idempotency_key` and `(user_id, turn_id, extractor_version, candidate_fingerprint)` (§3.1, Proposal §7.3). | `RL-040`, `RL-044` | Unique constraints: `implementation/tests/test_full_schema.py` (`RL-040`); worker replay: `RL-092` (`test_worker_idempotency`) | `PARTIAL (DDL unique constraints executed)` |
| **10** | V4 Archival precondition: `Active → Archived` permitted iff `(S)` `recorded_range` is closed OR `(C)` an `Active` derived replacement `r'` exists with `MEMORY_DERIVATION(r' → r)` and `V(r') @> V(r)`. | Transactional `archive_version` procedure + trigger check (§6, `D-27`). | `RL-042`, `RL-061` | `RL-090` (`T1`–`T5` archival-precondition harness) | `PLANNED` |

---

### 2.1 Schema provisioning (`RL-041`)

`implementation/schema/migrate.py` provisions the unchanged `RL-040` full schema
through explicit `RL_DATABASE_URL`, with version/source identity, catalog drift
inspection, idempotent apply and atomic confirmed clean reset. The private
migration ledger is operational metadata, outside the twelve domain tables.
`implementation/tests/test_migrations.py` verifies rollback, repeated/concurrent
apply, ownership, history/source drift and preservation of outside dependencies.
Its post-reset replay executes all 43 adapted assertions against the existing full
`memory_version` table, checking table identity, full-only columns and restored
provenance trigger. See [`evidence/RL-041-verification.md`](evidence/RL-041-verification.md).

This establishes schema provisioning and design-check compatibility. The runtime
procedure, role, concurrency-load, archival and deletion gates above remain owned
by their respective tasks; this contribution does not mark them complete.

### 2.2 Durable clock and queue (`RL-044`)

[`implementation/clock/`](implementation/clock/README.md) allocates one recorded instant per mutation transaction under the canonical user advisory lock. Live raw time uses the database transaction clock; replay input is explicit. The allocation takes the maximum prior content-free allocation event and memory recorded bounds, so ties/out-of-order instants advance by 1µs across connections, processes and empty jobs. Audit mode metadata attributes rows by user/recorded instant without logging memory content. Rollback restores the local clock and discards the allocation. No DDL/function/catalog change is introduced.

[`implementation/queue/`](implementation/queue/README.md) claims due work with SKIP LOCKED and commits prepared database effects with durable completion. Tenacity repeats at most three fresh whole transactions on 40001/40P01, including commit-time failures, with the fixed D-18 backoff; 23P01 is not blindly retried. Dual enqueue keys, pinned work revalidation, cancellation rollback and concurrent terminal-state checks protect idempotency. The canonical `JOB.status='dead_letter'` state holds exhausted/permanent failures. Actual native tests preserve the full schema, provenance and RLS boundaries. See [`evidence/RL-044-verification.md`](evidence/RL-044-verification.md).

This supplies the clock/queue foundation. It does not complete extraction/gate/writer/deletion integration, runtime grants, the full replay suite or the eight-worker load test, and it establishes no research result or sign-off.

### 2.2 Temporal selection (`RL-043`)

[`implementation/engine/predicates.py`](implementation/engine/predicates.py)
implements all four Proposal §7.4 / Technical Specification §3.5 predicates with
bound user/time values and native PostgreSQL range operations. Ordinary reads
select `active` versions whose recorded range contains the reference time;
audit includes `active`/`archived` at the belief time. The reference-containment
rule follows the frozen specification and both SQL checks, superseding the
published issue's stale `upper_inf` shorthand.

| Contract / risk | Verification artifact |
|---|---|
| E1–E3 current/corrected history/original belief; earlier reference with closed recorded range | `implementation/tests/test_predicates_db.py` worked-example/reference matrices |
| R8 point/window order and future guards, UTC comparisons, copied-model revalidation, deterministic `22023` | `implementation/tests/test_predicates.py` |
| Audit `β ≤ clock_now()` even for empty results; replay override and transaction-clock fallback; unrestricted audit valid target | Both predicate suites |
| Ordinary/audit status matrix and staging exclusion; valid/recorded half-open boundaries | `implementation/tests/test_predicates_db.py` |
| Bound SQL, quoted aliases, exact user filtering, existing tenant context and actual non-superuser RLS | Both predicate suites; DB role/bypass controls |
| Caller transaction/savepoint preservation, schema-qualified reads, native infinity/unbounded result distinction | `implementation/tests/test_predicates_db.py` |
| Audit excluded from answering context through the public result accessor | `implementation/tests/test_predicates.py`; `implementation/engine/README.md` |

See [`evidence/RL-043-verification.md`](evidence/RL-043-verification.md) for the
recorded checks and limits. Predicate coverage does not complete production role
grants (`RL-071`), the wider invariants suite (`RL-091`), monotonic clock/worker
wiring (`RL-044`/`RL-092`), ranking/rendering or research approvals.

---

## 3. Research Questions & Hypotheses (`H1`–`H4`) Traceability

| RQ / Hypothesis | Primary Endpoint(s) (Proposal §10.3) | Confirmatory Contrast(s) (Proposal §§4.1, 10.4) | Dataset / Suite | Generator & Adapter Tasks | Experiment & Analysis Tasks | Current Status |
|---|---|---|---|---|---|---|
| **`RQ1` / `H1`** (Bi-temporal history retention) | Co-primary **TAA** on `history-dependent` (superiority `> 0`) and `present-state` (non-inferiority `≥ −δ_pres`) strata. | `P − A2` (`P` vs `P-nohistory`). (`P − A1` `P-flat` and `A2b` are diagnostic/exploratory.) | Own Suite `T` (test split) | `RL-024`, `RL-026` | Pilot: `RL-100`; Test run: `RL-101`; Report: `RL-110` | `PLANNED` |
| **`RQ2` / `H2`** (Hybrid retrieval over extracted memories) | **Evidence Recall@k** on answerable user-grounded questions. | `P − A5` (`P-dense`) and `P − A6` (`P-lexical`) with Holm–Bonferroni (`α = 0.05`). | `LongMemEval_S` (test split) | `RL-020`, `RL-025`, `RL-026`, `RL-050` | Pilot: `RL-100`; Test run: `RL-102`; Report: `RL-110` | `PLANNED` |
| **`RQ3` / `H3`** (Write-time validation gate) | Co-primary **ASR** (superiority `< A3`) and **LUAR** (non-inferiority `≥ 1 − δ_luar`). | `P` vs `A3` (`P-nogate`). (`A4` sub-check ablation is diagnostic.) | Own Suite `G` (test split) | `RL-022`, `RL-026`, `RL-060` | Pilot: `RL-100`; Test run: `RL-103`; Audit: `RL-104`; Report: `RL-110` | `PLANNED` |
| **`RQ4` / `H4`** (Lineage-based deletion) | Scenario-level **PDLR** across 7 in-boundary channels (`0` observed leaks and exact 97.5% CP upper bound `≤ u*`). | `P` (primary bound) + `A8` (`P-nolineage` positive-control sensitivity check `≥ D-13`). | Own Suite `D` (test split) | `RL-023`, `RL-026`, `RL-070` | CP math: `validation/cp_bounds.py` (`COMPLETE`); Pilot: `RL-100`; Test run: `RL-105`; Report: `RL-110` | `PARTIAL (CP math verified; run PLANNED)` |
| **Design Req.** (Cross-user isolation) | Cross-user exposure count (`0` observed, exact CP upper bound). | `P` under RLS and service-boundary checks. | Own Suite `I` | `RL-022`, `RL-071` | `RL-091`, `RL-110` | `PLANNED` |

---

## 4. Proposal Deliverables (`D1`–`D5`, Proposal §12) Traceability

| Deliverable ID | Description | Target Location | Owning Tasks | Status |
|---|---|---|---|---|
| **`D1`** | Open-source bi-temporal memory engine (PostgreSQL schema, slow/fast path, validation gate, hybrid retrieval, lineage deletion). | `custom-memory-engine/implementation/` | `RL-040`–`RL-094` | `PARTIAL (RL-040 DDL; RL-041 provisioning merged)` |
| **`D2`** | Evaluation harness, baseline adapters (`B0`–`B4`), LLM judge validation, and statistical analysis module. | `custom-memory-engine/evaluation/` | `RL-030`–`RL-034` | `PARTIAL (RL-030 harness, RL-031 baselines, RL-032 replay cache; judge/statistics pending)` |
| **`D3`** | Seeded synthetic evaluation suites (`T`, `G`, `D`, `I`) and frozen dataset splits. | `custom-memory-engine/research/benchmarks/` | `RL-020`–`RL-026` | `COMPLETE (RL-020–RL-026)` |
| **`D4`** | Personal Digital Twin (PDT) demonstrator with Memory Inspector, timeline view, conflict resolution, and deletion controls. | `custom-memory-engine/application/` | `RL-120`–`RL-122` | `PLANNED` |
| **`D5`** | Final thesis report, confirmatory analysis tables/figures (`H1`–`H4`), and one-command reproducibility package. | `custom-memory-engine/reports/` | `RL-110`, `RL-111`, `RL-130`–`RL-132` | `PLANNED` |

---

## 5. Quality Gates (`J1`–`J10`, `PROJECT_ROADMAP.md` §J)

| Gate | Requirement | Verification Method | Current Status |
|---|---|---|---|
| **`J1`** | Mechanical checks pass on proposal, checklist, and changelog. | `python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md` | `PASS` |
| **`J2`** | Reduced-schema bitemporal SQL assertions (`43/43`) pass. | `psql -v ON_ERROR_STOP=1 -f validation/bitemporal_checks.sql` (`validation/bitemporal_checks.output.txt`) | `PASS` |
| **`J3`** | Clopper–Pearson upper-bound script matches saved output. | `diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt` | `PASS` |
| **`J4`** | All Phase P1blocking decisions (`D-01`–`D-08`, `D-15`–`D-19`, `D-23`–`D-27`) resolved and signed off. | Decision memos in `PLAN/decisions/` (`RL-010`, `RL-012`–`RL-016`) | `IN PROGRESS (6 GitHub issues open)` |
| **`J5`** | Benchmark and own-suite dev/test splits frozen prior to any test run. | `research/benchmarks/splits/FREEZE_LIST.md` (`RL-026`) | `PASS` |
| **`J6`** | `T1`–`T5` archival-precondition harness green on full schema. | `pytest implementation/tests/archival/` (`RL-090`) | `PENDING (P9)` |
| **`J7`** | All mandatory rows in `PROJECT_ROADMAP.md` §G testing matrix `Tested` or waived. | `implementation/tests/report.md` (`RL-094`) | `PENDING (P9)` |
| **`J8`** | Pilot run completed on dev split only; all pilot-derived parameters (`D-09`–`D-14`, `D-21`, `D-22`) frozen. | `evaluation/runs/pilot/FREEZE_LIST.md` (`RL-100`) | `PENDING (P10)` |
| **`J9`** | Confirmatory test splits (`H1`–`H4`) executed exactly once under frozen protocol. | `evaluation/runs/<ts>/H{1,2,3,4}/manifest.json` (`RL-101`–`RL-105`) | `PENDING (P10)` |
| **`J10`** | Documentation, links, and reference corpus organized with a single source of truth per topic. | Link checker + `md5sum -c validation/document_hashes.txt` | `PASS` |
