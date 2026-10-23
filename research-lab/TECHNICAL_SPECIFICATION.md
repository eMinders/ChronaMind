# TECHNICAL_SPECIFICATION.md

**Scope.** Freezes implementation-level details that `Graduation_Project_Proposal.md` leaves open. Does **not** duplicate the proposal; it points into §7–§10 of the proposal and adds (a) the implementation decisions that must be fixed before P4 of [`PROJECT_ROADMAP.md`](PROJECT_ROADMAP.md), (b) an entity–relationship summary that is testable, and (c) a decision log keyed to §E of the roadmap.

Every normative statement here is marked **MUST**, **SHOULD**, or **MAY** (RFC 2119). A **MUST** that is not implemented yet is a scheduled requirement, not a current property of the engine.

---

## 1. Normative references

- [`Graduation_Project_Proposal.md`](Graduation_Project_Proposal.md) §§1.1, 7, 8, 9, 10.
- [`Methodology_and_Consistency_Checklist.md`](Methodology_and_Consistency_Checklist.md) §§0–6.
- [`Source_Evidence_Ledger.md`](Source_Evidence_Ledger.md) §§A, B, C.
- [`validation/bitemporal_checks.sql`](validation/bitemporal_checks.sql) (reduced-schema reference implementation; 43 PASS lines).
- [`PROJECT_ROADMAP.md`](PROJECT_ROADMAP.md) §C–§K.

---

## 2. Decision log (keyed to roadmap §E)

Only items that **must be fixed before code** are listed here. For the full list see roadmap §E; this log is where each decision will be recorded as it is frozen in P1. Rows currently show the **State** at the end of the finalization session.

| Dec# | Decision | State | Current value | Approver | Evidence | Supersedes |
|---|---|---|---|---|---|---|
| 1 | H1 primary contrast is **P − A2** | **Frozen** | P − A2 | V4 proposal §4 | checklist §0 "H1 scope" | V1 "four layers" claim |
| 2 | Persona layer is optional, off in P | **Frozen** | Optional, off in P | V4 proposal §O1, §5, §7.1 | checklist §0 "Persona layer" | V1 "four layers" claim |
| 3 | Minimal consolidation is essential | **Frozen** | Essential | V4 proposal §7.1 | checklist §0 "Persona layer" (second clause) | — |
| 4 | H4 inference level is scenario-level C–P | **Frozen** | Scenario | V4 proposal §4.1, §10.6 | checklist §0 "H4 inference" | V1 event-level primary |
| 5 | H4 "Supported" rule is three-conjunct | **Frozen** | zero leaks ∧ bound ≤ u\* ∧ positive control ≥ share | V4 proposal §4.1, §10.6 | checklist §0 "H4 conclusion rule" | V3 overlap in §4.1 |
| 6 | Archival precondition is (S) ∨ (C) | **Frozen** | (S) Superseded ∨ (C) Active derived replacement; branch (A) "valid-ended" **removed** | V4 proposal §7.4 R5, §7.5, §7.6 step 1, §8 invariant 10 | checklist §0 "Historical-memory archival bug fixed" | V3 three-branch precondition |
| 7 | Reference list byte-identical to V3 | **Frozen** | 41 entries | enforced by `validation/mechanical_checks.py` | mechanical_checks.output.txt | — |
| 8 | H1 is a two-stratum endpoint | **Frozen** | history-dependent (sup.) + present-state (NI at δ_pres) | V4 proposal §4, §10.3 | checklist §0 "H1 endpoint form" | V1 single-stratum |
| 9 | PostgreSQL version | **Open** | 14 / 16 / 17 / 18 (V2 SQL ran on 16.15) | TBD | checklist §6 item 13 | — |
| 10 | LLM / embedding model pinning | **Open** | TBD | TBD | checklist §6 item 8 | — |
| 11 | Vector index strategy per user | **Open** | Exact (default); HNSW only in scalability tests with recall checks | V4 proposal §9 directs this; the per-user partition is a Frozen architectural MUST (§3.2) | V4 §9 | — |
| 12 | k = 60 for RRF | **Frozen** | 60 | V4 proposal §7.6; [12] "near-optimal" pilot | ledger §C | — |
| 13 | Lexical ranker | **Frozen** | PostgreSQL full-text ranking (NOT BM25) | V4 proposal §7.6, audit §1 row 6 | ledger §A row 38 | — |
| 14 | δ_pres, δ (LUAR), u\*, positive-control share, stratum mix | **Open** (pilot) | TBD | TBD | checklist §4, §6 item 2 | — |
| 15 | Deletion storage boundary | **Frozen** (per §7.7) | In-boundary channels: tables, indexes, queue payloads, caches, derived artifacts, lineage rows. Excluded: backups beyond retention schedule, WAL beyond retention, provider logs, already-shown text, user-copied data. | V4 proposal §7.7 | — | V1 "guaranteed deletion" claim |
| 16 | T1 – T5 harness design (SQL-only vs application-level) | **Open** | TBD | TBD | changelog §Unresolved decisions item 17 | — |

---

## 3. Memory entities and relationships

See V4 proposal §8 for the normative schema. This section summarises in a testable way; implementers **MUST** derive the DDL from the proposal and **MUST NOT** change invariant 1–10 semantics without a V5 patch.

### 3.1 Entities

- `MEMORY` — logical memory item identified by `(user_id, memory_key, predicate_id)`.
- `MEMORY_VERSION` — immutable row with:
  - `status ∈ {Candidate, Conflict-Pending, Active, Archived, Rejected, Deleted}`;
  - **valid_range** `V = [valid_from, valid_until)` possibly open-ended;
  - **recorded_range** `R = [recorded_from, recorded_until)` possibly open-ended;
  - foreign key to `MEMORY`;
  - `confidence`, `assertion_mode`, `extracted_from_event_id`.
- `MEMORY_EVIDENCE` — joins versions to source turns/events with a per-row `evidence_role`.
- `MEMORY_DERIVATION` — directed edge from derived version to source version; mandatory for any consolidated / summary / persona-trait row.
- `DELETION_LOG` — append-only; contains **no content and no hash**.
- `JOB` — PostgreSQL job table with `FOR UPDATE SKIP LOCKED` and an idempotency key.
- `PREDICATE` — predicate schema row with `cardinality ∈ {single, multi, event}`.

### 3.2 Architectural MUSTs

- Every user **MUST** have an isolated partition (per-user vector-store partition and per-user scoped predicates). RLS is a second layer and does not replace the partition.
- Candidate and Conflict-Pending rows **MUST** have `recorded_range = NULL`. `recorded_from` **MUST** be assigned only on commit to Active (invariant 3).
- The exclusion constraint on `(user_id, memory_key, predicate_id, V, R)` with `btree_gist` **MUST** apply only to single-valued predicates (invariant 4).
- `recorded_from` **MUST** be strictly monotonic per user to prevent replay-clock ties (rule R3).
- Derived rows **MUST** carry at least one `MEMORY_DERIVATION` edge back to a source version.

### 3.3 Temporal invariants (summary; §8 is authoritative)

| # | Invariant | Enforcement site | Current test status |
|---|---|---|---|
| 1 | Every non-deleted version is traceable | Scheduled integrity check | Not executed |
| 2 | Status matches range form | CHECK constraint | **Executed in V2** |
| 3 | `recorded_from` set at commit to Active | Trigger / procedure | **Executed in V2** |
| 4 | Single-valued no-overlap in `(V, R)` | Exclusion constraint with `btree_gist` | **Executed in V2** (reduced schema; full schema under concurrency Open) |
| 5 | Superseded is a derived condition (recorded range closed) | Query predicate | Enforced by schema shape; **Executed in V2** |
| 6 | Staged rows never satisfy any predicate | CHECK + predicate design | **Executed in V2** |
| 7 | RLS per user | Row-level security policy | Not executed |
| 8 | Deletion removes every version and derived artifact (R6 + §7.7 closure) | Transactional deletion procedure | Not executed |
| 9 | Job queue is idempotent per `(user_id, key)` | `JOB` schema + idempotency index | Not executed |
| 10 | Archival precondition (S) ∨ (C) | Transactional archival procedure | **Not executed** — tests **T1 – T5** planned |

### 3.4 State-transition rules (R1 – R8 in §7.4)

- **R1** close-then-insert for new belief (never destructive overwrite).
- **R2** same-day correction forbids empty re-issue.
- **R3** clock ties: `recorded_from` strictly monotonic (replay too).
- **R4** staging and commit: Candidate → Active sets `recorded_from = commit_time`; Conflict-Pending → Active sets `recorded_from = resolution_time`.
- **R5** Archive only when **(S)** OR **(C)** holds (invariant 10).
- **R6** Delete removes every version of the memory item **and** the transitive derivation closure of §7.7; audit log carries no content and no hash.
- **R7** cross-user isolation enforced via per-user partition and RLS.
- **R8** read-time check: `t > ρ` disallowed in current-state queries.

### 3.5 Retrieval predicates (§7.6)

- `current_state(user_id, t) = { v | v.status=Active ∧ ρ ∈ R(v) ∧ ρ ∈ V(v) }` where ρ = `now()`.
- `historical_as_of(user_id, t, [β]) = { v | v.status=Active ∧ β ∈ R(v) ∧ t ∈ V(v) ∧ t ≤ β }` (point form; window form uses `V(v) ∩ W ≠ ∅`).
- `audit_as_of(user_id, t, β) = { v | v.status ∈ {Active, Archived} ∧ β ∈ R(v) ∧ t ∈ V(v) }`.

Archived rows **MUST** be excluded from `current_state` and `historical_as_of` and **MUST** be included in `audit_as_of`. The §7.5 four-condition table (currently valid / historical / superseded / Archived) is the normative visibility matrix.

---

## 4. Archival precondition (invariant 10)

Rewriting of V3 branch (A); see V4 proposal §7.5 and changelog §1.

Let `r` be the target `MEMORY_VERSION`. The transactional archival procedure **MUST** accept the archival **only if** at least one of these holds and is recorded in the archival log:

- **(S) Superseded.** `R(r)` is already closed (`recorded_until ≠ ∞`).
- **(C) Active derived replacement.** There exists `r′` such that:
  1. `r′.status = Active` (not Archived, not Deleted);
  2. there exists an edge `(r′, r) ∈ MEMORY_DERIVATION` representing the same user-level fact;
  3. `r′` is itself a current belief (recorded range open);
  4. `V(r′) ⊇ V(r) ∩ (−∞, archival_time]` i.e. `r′` covers at least the valid range of `r` that any historical-as-of query could ask about;
  5. ordinary retrieval through `r′` returns the fact that `r` carried.

If neither (S) nor (C) holds, the procedure **MUST** return `VALIDATION_ERROR archival_precondition_unmet` and `r` **MUST** remain Active. The V3 "valid-ended" sub-branch of (A) is **removed** and must not reappear (the mechanical-checks stale-wording guard enforces this).

### 4.1 Required regression tests (T1 – T5)

| ID | Setup | Expected outcome | Blocks |
|---|---|---|---|
| **T1** | Currently-valid current belief; no (C) replacement | Archival rejected; `current_state` still returns the row | invariant 10 |
| **T2** | Historical current belief (recorded range open, valid range ended); no (C) replacement | Archival rejected; `historical_as_of(t ∈ V)` still returns the row (**V3-bug regression test**) | invariant 10 |
| **T3** | Superseded version | Archival accepted via (S); `current_state` and `historical_as_of` never return it; `audit_as_of` still returns it | invariant 10 |
| **T4** | Active derived replacement `r′` with `(r′, r) ∈ MEMORY_DERIVATION` and matching valid range | Archival accepted via (C); fact still returned via `r′`; provenance chain `r′ → r` traceable; archival log records branch (C) and `r′.id` | invariant 10 |
| **T5** | After T4, delete the original memory | R6 + §7.7 closure removes `r′` (and any further transitively derived artifact); `current_state`, `historical_as_of`, and `audit_as_of` for the deleted key return nothing; deletion-log entry has no content and no hash | invariant 10 + §7.7 |

Implementation **MUST** be transactional. A single-row CHECK constraint is **insufficient** because (C) requires looking at a different row.

---

## 5. Validation and contradiction interfaces (§7.5)

- Gate inputs: candidate `MEMORY_VERSION` with `assertion_mode`, `confidence`, `evidence`; conflict relations with any current beliefs.
- Gate outputs: `(accept | reject | hold)` + reason code + optional supersession proposal; **MUST** be logged.
- Conflict relations **MUST** be computed in the same transaction as the version write.

---

## 6. Derivation-lineage semantics

- Every derived row **MUST** have at least one incoming `MEMORY_DERIVATION` edge.
- Lineage **MUST** be queryable in both directions.
- `MEMORY_DERIVATION` edges **MUST NOT** form a cycle (DAG constraint).
- Deletion **MUST** traverse the DAG transitively (T5).

---

## 7. Deletion closure (§7.7)

In-boundary channels the deletion procedure **MUST** cover:

1. All `MEMORY_VERSION` rows of the memory item.
2. All transitively derived `MEMORY_VERSION` rows (via `MEMORY_DERIVATION`).
3. Full-text indexes referencing those rows.
4. Vector-store rows (pgvector) for those rows.
5. Job-queue payloads referencing those rows.
6. In-boundary cache entries (retrieval cache, consolidation cache).
7. Audit entries about the memory item **except** the deletion-log entry itself (which carries no content and no hash).

Out-of-boundary (explicitly excluded):

- Database backups beyond the published retention schedule.
- WAL beyond retention.
- Provider / API logs.
- Already-shown text in client transcripts.
- User-copied data.

A result of zero observed leaks across these channels is **MUST** be reported with a scenario-level Clopper–Pearson upper bound (per §10.6) and **MUST NOT** be presented as a guarantee of zero possible leakage.

---

## 8. API and worker boundaries

- Fast path (synchronous): retrieval, answer generation; **MUST** return within the §9 latency envelope.
- Slow path (asynchronous workers): extraction, validation gate, temporal state manager, minimal consolidation, optional retention/persona.
- Workers **MUST** use `FOR UPDATE SKIP LOCKED` on the `JOB` table.
- Each worker job **MUST** be idempotent per `(user_id, idempotency_key)`.
- Per-user advisory lock **MUST** serialise writes for a single user.
- Dead-letter table **MUST** retain failed jobs with full context.
- Visibility lag (slow-path commit → fast-path visibility) **MUST** be measured descriptively (§10 O6).

---

## 9. Error handling and observability

- All gate decisions, supersessions, archivals, and deletions **MUST** emit structured events with reason codes.
- Dashboards **SHOULD** show queue lag, visibility lag, gate acceptance rate, deletion backlog, invariant-check counts.
- Errors returned to the client **MUST** be typed (e.g. `VALIDATION_ERROR archival_precondition_unmet`) and **MUST NOT** leak another user's data.

---

## 10. Configuration and version pinning

- All model identifiers (LLM, embedding, judge) **MUST** be pinned per experiment and recorded in run manifests.
- All third-party commits (Mem0 at `0724291`; Graphiti at `d50741d` as recorded in `Source_Evidence_Ledger.md` §B rows 18–19 — update when P1 freezes a release) **MUST** be captured.
- PostgreSQL major version **MUST** be pinned (see Decision 9).
- Dataset release / commit **MUST** be pinned (LongMemEval `98d7416`, LoCoMo `locomo10.json` as checked).

---

## 11. Testable acceptance criteria (cross-reference to roadmap §G)

Every **MUST** above maps to one or more §G rows:

| Spec MUST | §G row |
|---|---|
| §3.3 invariant 2 | "Temporal predicates and version-transition tests" |
| §3.3 invariant 3 | "Temporal predicates and version-transition tests" |
| §3.3 invariant 4 | "Concurrency and transaction tests" |
| §3.3 invariant 7 | "Cross-user isolation and access-control tests" |
| §3.3 invariant 8 | "Deletion-closure tests" |
| §3.3 invariant 9 | "Queue idempotency and retry tests" |
| §3.3 invariant 10 | **T1 – T5 archival-precondition regression tests** |
| §3.5 retrieval predicates | "Retrieval correctness tests" + "Temporal predicates and version-transition tests" |
| §4 Archival precondition | **T1 – T5** |
| §6 Derivation-lineage DAG | "Derived-artifact lineage tests" |
| §7 Deletion closure | "Deletion-closure tests" |
| §8 Idempotency per `(user, key)` | "Queue idempotency and retry tests" |
| §8 Per-user advisory lock | "Concurrency and transaction tests" |

---

## 12. Non-goals

- No claim that this engine will outperform any external system (§4.1 of the proposal).
- No behavioural simulation of the user ("Personal Digital Twin" is defined conservatively in §1.1).
- No parameter-level unlearning (§6 Related Work; this project never modifies model parameters).
- No persona layer required for H1–H4.
- No team or schedule assertion (§15 of the proposal).

---

## 13. Change control

Any change to a **Frozen** decision in §2, to an invariant in §3.3, to a state-transition rule in §3.4, to a retrieval predicate in §3.5, to the archival precondition in §4, to the deletion closure in §7, or to the Testable acceptance criteria in §11 **MUST**:

1. Be documented in a new changelog (`CHANGELOG_v5.md` or later).
2. Re-run the fact-check audit for any affected citation.
3. Re-run `mechanical_checks.py` after updating the stale-wording guard if needed.
4. Update [`TRACEABILITY_MATRIX.md`](TRACEABILITY_MATRIX.md).
