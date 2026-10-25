# Technical Specification — Bi-Temporal Memory Engine & Evaluation Harness

**Status:** Normative implementation specification derived from [`Graduation_Project_Proposal.md`](Graduation_Project_Proposal.md) (V4).  
**Scope:** Bi-temporal data model, transactional lifecycle procedures, validation gate, hybrid retrieval, lineage deletion, evaluation harness, and statistical inference protocol.

---

## 1. Normative Sources & Authority Hierarchy

If a discrepancy arises between documents, resolve it in this order:

1. [`Graduation_Project_Proposal.md`](Graduation_Project_Proposal.md) §§4, 7, 8, 9, 10 (frozen research contract and hypotheses `H1`–`H4`).
2. [`Methodology_and_Consistency_Checklist.md`](Methodology_and_Consistency_Checklist.md) §§1–5 (audit checklists and `T1`–`T5` archival test contract).
3. This `TECHNICAL_SPECIFICATION.md` (concrete SQL, API, and harness contracts).
4. [`PLAN/DECISIONS_REGISTER.md`](PLAN/DECISIONS_REGISTER.md) (canonical status of all open, pilot-derived, and frozen decisions `D-01`–`D-27`, `D-ARCH`, `D-ENG-*`).
5. [`reference/Source_Evidence_Ledger.md`](reference/Source_Evidence_Ledger.md) and [`reference/Research_and_Fact_Check_Audit.md`](reference/Research_and_Fact_Check_Audit.md) (literature and audit evidence).

Any change to a **FROZEN** decision requires a dated entry in [`PLAN/DECISIONS_REGISTER.md`](PLAN/DECISIONS_REGISTER.md) and [`CHANGELOG.md`](CHANGELOG.md), plus a full re-run of [`validation/mechanical_checks.py`](validation/mechanical_checks.py).

---

## 2. Summary of Key Technical Decisions

The full 27-item decision register lives in [`PLAN/DECISIONS_REGISTER.md`](PLAN/DECISIONS_REGISTER.md). Its dated **2026-10-24 selected-value table** supersedes the original pre-dispatch defaults below. Current P1 memos live in [`../custom-memory-engine/decisions/`](../custom-memory-engine/decisions/) under the owner's root-placement revision; member/supervisor signatures remain pending.

| IDs | Current selected values (2026-10-24) | Status / authority |
|---|---|---|
| D-01–03 | Answer/extract `gpt-4.1-mini-2025-04-14`; embed BAAI/bge-small-en-v1.5 revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, d=384; judge `gpt-4.1-2025-04-14`; temperature 0; proposed combined USD 100 / 50M-token ceiling | Prepared; supervisor pending, RL-010 |
| D-04–08 | A10/A9 OFF in P, exploratory SKIP; BM25 and external queue OFF/SKIP; H5 OFF | Prepared; supervisor pending, RL-010 |
| D-15/16 | A2b SKIP; Suite D target dev 20 / test 50 scenarios, zero-leak test bound 0.071121736465 | Selected; peer and final pilot freeze pending, RL-014; D-13/D-14 not frozen |
| D-17/18 | CPython 3.12.12, Psycopg 3.3.2, pool 3.3.3, pgvector client 0.4.2, Pydantic 2.12.5; per-user advisory serialization + R1 + GiST exclusion; measured SERIALIZABLE candidate with bounded whole-transaction retries | Runtime compatibility verified; load/peer pending, RL-013/RL-012 |
| D-19/20 | H5 ethics not applicable to disabled scope (not institutional exemption); zero recruitment without written clearance; React/TS/Vite primary with HTML/HTMX fallback | Prepared/selected; review pending, RL-015/RL-013 |
| D-23/24/26 | Stable session sorting, 1 µs R3 tie increments, retain anomalies with reference clamped to last recorded clock and exclusion sensitivity; n≥100 stratified dev judge validation, dual blind labeling, κ≥0.75, accuracy≥0.90 and coverage/approval gates; MIT for original suites only | Paired/supervisor/ownership approvals pending, RL-016 |
| D-25 | PostgreSQL 16.15, vector 0.8.7, btree_gist 1.7; `pgvector/pgvector:0.8.7-pg16-bookworm@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a` | Live compatibility verified; peer pending, RL-012 |
| D-27 | Transactional archive_version plus trigger/bypass restrictions, same-transaction S∨C and replacement locking | Prepared; supervisor/full-engine T1–T5 pending, RL-010 |

The original planning summary follows for historical context; its OPEN/default labels are superseded by the dated table above:

| ID | Parameter / Mechanism | Status | Value / Rule | Authority |
|---|---|---|---|---|
| `D-ARCH` | Repository & runtime boundary | **FROZEN (Owner)** | **Option C (owner placement revision 2026-10-24):** Self-contained implementation under root `custom-memory-engine/`, Research Lab as references, strictly isolated from `app/`. | [`PLAN/ARCHITECTURE.md`](PLAN/ARCHITECTURE.md) |
| `D-ENG-01` | Primary contrast for `H1` | **FROZEN (V4)** | `P − A2` (`P` vs `P-nohistory`) on suite `T`, evaluated on both co-primary strata (`history-dependent` superiority and `present-state` non-inferiority). `P − A1` (`P-flat`) is diagnostic only. | Proposal §§4.1, 10.4 |
| `D-ENG-02` | V4 Archival precondition (Invariant 10) | **FROZEN (V4)** | A version `r` may transition `Active → Archived` iff `(S)` `recorded_range` is closed OR `(C)` an `Active` derived replacement `r'` exists with `MEMORY_DERIVATION(derived_version_id = r', source_version_id = r)` covering `V(r)`. The V3 valid-ended branch `(A)` is removed. | Proposal §§7.4 `R5`, 7.5, 8 Inv. 10 |
| `D-ENG-03` | Reciprocal Rank Fusion constant | **FROZEN (V4)** | `RRF(m) = Σ_c 1 / (60 + rank_c(m))` (`k = 60`). Weighted-sum fusion is exploratory ablation `A7`. | Proposal §7.6 |
| `D-ENG-04` | Controlled deletion boundary | **FROZEN (V4)** | Covers PostgreSQL tables, FTS/vector indexes, `JOB` queue, application caches, assembled prompts, answers, and logs. Excludes WAL/backups/replicas, physical unvacuumed pages, external LLM/embedding provider logs, already-streamed text, and model weights. | Proposal §§7.7, 10.3 |
| `D-01`–`D-03` | Frozen answering LLM, embedding model, and judge model | **NEEDS SUPERVISOR SIGNOFF (`RL-010`)** | One frozen answering LLM (`temperature = 0`), one frozen embedder, and one validated judge model (distinct from answering LLM where feasible). | Proposal §§9, 10.1, 10.5 |
| `D-04`–`D-08` | Optional components (`A10` Persona, `A9` retention ranking, BM25, Redis/Celery, `H5` usability study) | **NEEDS SUPERVISOR SIGNOFF (`RL-010`)** | All **OFF** in primary configuration `P` for confirmatory runs `H1`–`H4`. Recommended default: keep disabled unless explicitly activated as exploratory work. | Proposal §§5, 7.2, 7.6, 9, 11 |
| `D-09`–`D-14` | Token budget `B_mem`, `k` in `Recall@k`, `δ_pres`, `δ_luar`, `u*`, positive-control share | **PILOT-DERIVED (`RL-011`, `RL-100`)** | Provisional until frozen on the development split in `RL-100` prior to any test-split run. | Proposal §§4.1, 10.1, 10.3, 10.6 |
| `D-15`, `D-16` | Exploratory `A2b` inclusion; Suite `D` scenario count `N_D` | **OPEN (`RL-014`)** | `N_D` sets the scenario-level Clopper–Pearson upper bound (`1 − (α/2)^(1/N_D)` at 0 leaks; e.g., `N_D = 36 → 0.0974` at one-sided `α = 0.025` / two-sided `α = 0.05` convention per `validation/cp_bounds.py`). | Proposal §§10.2, 10.4, 10.6 |
| `D-17`, `D-18` | Engine language/stack; Invariant 4 concurrency strategy | **OPEN (`RL-013`, `RL-012`)** | Python 3.12 + `psycopg` v3 + `pgvector` recommended (`RL-013`). Per-user advisory lock + `EXCLUDE USING gist`; `SERIALIZABLE` + retry if GiST contention appears under `RL-093` load. | Proposal §§7.3, 8, 9 |
| `D-25` | PostgreSQL & `pgvector` pinned versions | **OPEN (`RL-012`)** | Reduced-schema baseline ran on PostgreSQL `16.15` (`postgres:16-alpine`). Pin exact PostgreSQL `16.x`/`17.x` and `pgvector` image digest in `RL-012`/`RL-004`. | Proposal §§7.4, 9 |
| `D-27` | `T1`–`T5` archival precondition enforcement site | **NEEDS SUPERVISOR SIGNOFF (`RL-010`)** | Enforce `(S) ∨ (C)` inside the transactional archival procedure (`archive_version`), backed by a database trigger/function and verified by `RL-090`. | Proposal §§7.5, 8 Inv. 10 |

---

## 3. Bi-Temporal Data Model & Query Predicates

### 3.1 Core Relational Entities (Proposal §8)

All user-data tables carry `user_id` and enforce Row-Level Security (`FORCE ROW LEVEL SECURITY`) for the runtime application role (`Invariant 7`).

| Table | Primary Key | Key Columns & Types | Purpose |
|---|---|---|---|
| `APP_USER` | `id ( text / uuid )` | `external_ref text UNIQUE`, `created_at timestamptz NOT NULL` | User identity and tenant scope root. |
| `SESSION` | `id ( text / uuid )` | `user_id NOT NULL REFERENCES APP_USER`, `started_at timestamptz NOT NULL`, `ended_at timestamptz` | Conversational session grouping. |
| `TURN` | `id ( text / uuid )` | `session_id NOT NULL REFERENCES SESSION`, `user_id NOT NULL`, `sequence_no int NOT NULL`, `role text CHECK (role IN ('user','assistant'))`, `content text NOT NULL`, `occurred_at timestamptz NOT NULL` | Verbatim dialogue turns; evidence source for extraction. |
| `PREDICATE_SCHEMA` | `predicate_key ( text )` | `cardinality text NOT NULL CHECK (cardinality IN ('single','multi','event'))`, `default_layer text NOT NULL CHECK (default_layer IN ('episodic','semantic','opinion','persona'))` | Declares whether a predicate is single-valued, multi-valued, or an instantaneous event. |
| `MEMORY_VERSION` | `id ( text / uuid )` | `memory_key text NOT NULL`, `user_id NOT NULL`, `layer text NOT NULL CHECK (layer IN ('episodic','semantic','opinion','persona'))`, `status text NOT NULL CHECK (status IN ('candidate','conflict_pending','rejected','active','archived'))`, `subject_key text NOT NULL`, `predicate_key text NOT NULL`, `single_valued boolean NOT NULL`, `content text NOT NULL`, `valid_range tstzrange NOT NULL`, `valid_from_basis text NOT NULL CHECK (valid_from_basis IN ('explicit','inferred'))`, `recorded_range tstzrange NULL`, `assertion_mode text NOT NULL`, `confidence real`, `importance real`, `reinforcement_count int NOT NULL DEFAULT 0`, `extractor_version text NOT NULL`, `archival_branch text NULL CHECK (archival_branch IN ('S','C'))` | Immutable belief version row. `memory_key` groups all versions (including superseded and re-issued rows) of the same logical memory item (`R6`). |
| `MEMORY_EVIDENCE` | `(version_id, turn_id, evidence_span)` | `version_id NOT NULL REFERENCES MEMORY_VERSION ON DELETE CASCADE`, `turn_id NOT NULL REFERENCES TURN`, `evidence_span text NOT NULL`, `evidence_role text NOT NULL` | Grounds extracted memories in user-authored turn spans (`Invariant 1`). |
| `MEMORY_DERIVATION` | `(derived_version_id, source_version_id)` | `derived_version_id NOT NULL REFERENCES MEMORY_VERSION ON DELETE CASCADE`, `source_version_id NOT NULL REFERENCES MEMORY_VERSION`, `derivation_type text NOT NULL CHECK (derivation_type IN ('consolidation','summary','corroboration','re_derivation'))` | Directed lineage link `r' → r` from derived version `r'` (`derived_version_id`) to source version `r` (`source_version_id`). |
| `MEMORY_EMBEDDING` | `(version_id, embedding_model)` | `version_id NOT NULL REFERENCES MEMORY_VERSION ON DELETE CASCADE`, `embedding_model text NOT NULL`, `embedding vector(d) NOT NULL` | Dense vector representation bound 1-to-1 with a version row (`Invariant 8`). |
| `MEMORY_CONFLICT` | `id ( text / uuid )` | `candidate_version_id NOT NULL REFERENCES MEMORY_VERSION ON DELETE CASCADE`, `existing_version_id UUID NULL`, `relation text NOT NULL`, `decision text NOT NULL CHECK (decision IN ('accept','hold','reject'))`, `reason_code text NOT NULL`, `decided_by text NOT NULL`, `decided_at timestamptz NOT NULL` | Audit record for validation-gate and conflict-detector decisions (§7.5). |
| `AUDIT_EVENT` | `id ( text / uuid )` | `user_id NOT NULL`, `memory_ref text NOT NULL` (random token, no content), `event_type text NOT NULL`, `event_at timestamptz NOT NULL`, `details jsonb NOT NULL` (content-free schema) | Tamper-evident lifecycle and deletion log (`Invariant 8`). |
| `DELETION_REQUEST` | `id ( text / uuid )` | `user_id NOT NULL`, `scope text NOT NULL CHECK (scope IN ('memory','topic','all'))`, `target_ref text NOT NULL`, `status text NOT NULL`, `deletion_epoch bigint NOT NULL`, `requested_at timestamptz NOT NULL`, `completed_at timestamptz` | Tracks deletion execution and monotonic per-user `deletion_epoch` (§7.7). |
| `JOB` | `id ( text / uuid )` | `user_id NOT NULL`, `kind text NOT NULL`, `turn_id text NULL`, `idempotency_key text NOT NULL UNIQUE`, `status text NOT NULL CHECK (status IN ('queued','running','done','dead_letter'))`, `attempts int NOT NULL DEFAULT 0`, `next_run_at timestamptz NOT NULL`, `payload jsonb` | PostgreSQL background queue claimed via `SELECT ... FOR UPDATE SKIP LOCKED` (`Invariant 9`). |

### 3.2 Lifecycle Status vs. Derived Temporal Conditions

- **Stored `status` (`MEMORY_VERSION.status`):** `'candidate' | 'conflict_pending' | 'rejected' | 'active' | 'archived'`.
  - `'deleted'` is **terminal**: rows are physically removed from `MEMORY_VERSION` (`DELETE FROM memory_version`), and a content-free event is written to `AUDIT_EVENT` and `DELETION_REQUEST`.
- **Derived conditions (never stored as `status`):** Given reference time `ρ`, `V(r) = valid_range`, and `R(r) = recorded_range`:
  - **Current belief:** `r.status IN ('active', 'archived') AND upper_inf(R(r))` (open upper bound on `recorded_range`).
  - **Superseded:** `r.status IN ('active', 'archived') AND NOT upper_inf(R(r))` (closed upper bound on `recorded_range`).
  - **Currently valid:** `V(r) @> ρ`.
  - **Historical:** `NOT upper_inf(V(r)) AND upper(V(r)) <= ρ`.
  - **Critical distinction:** A *historical current belief* (`status = 'active'`, `upper_inf(R(r))`, `upper(V(r)) <= ρ`) is **not** superseded; it represents what the system currently believes held in a past period and must remain reachable via `historical_as_of`.

### 3.3 Range Representation & Constraints (Invariants 2 & 4)

All ranges use PostgreSQL `tstzrange` with canonical half-open bounds `[lower, upper)`:

```sql
CONSTRAINT valid_wellformed
  CHECK (NOT isempty(valid_range) AND NOT lower_inf(valid_range)),
CONSTRAINT recorded_iff_believed
  CHECK ((status IN ('active', 'archived')) = (recorded_range IS NOT NULL)),
CONSTRAINT recorded_wellformed
  CHECK (recorded_range IS NULL OR (NOT isempty(recorded_range) AND NOT lower_inf(recorded_range))),
CONSTRAINT no_bitemporal_overlap
  EXCLUDE USING gist (
    user_id WITH =,
    subject_key WITH =,
    predicate_key WITH =,
    valid_range WITH &&,
    recorded_range WITH &&
  ) WHERE (single_valued AND status IN ('active', 'archived'))
```

### 3.4 Rules `R1`–`R8` (Proposal §7.4)

- **`R1` (Close then insert):** A supersession transaction **MUST** first `UPDATE` the replaced row(s) to close `recorded_range` at the transaction clock `c` (`tstzrange(lower(recorded_range), c, '[)')`), and only then `INSERT` the truncated re-issue row (if any) and the new replacement row with `recorded_range = tstzrange(c, NULL, '[)')`. All statements in the transaction use the same clock instant `c`.
- **`R2` (Truncated re-issue):** When a single-valued belief `old` with `valid_range = [v_old, ∞)` is superseded by a new belief starting at `v_new`:
  - If `v_old < v_new`: insert a re-issued row `old'` carrying `old`'s content and `memory_key` with `valid_range = [v_old, v_new)` and `recorded_range = [c, ∞)`.
  - If `v_old = v_new` (same-instant correction): close `old` in recorded time without inserting a truncated re-issue row.
  - If `v_new < v_old` (out-of-order retroactive start prior to `v_old`): route the candidate to `conflict_pending` rather than auto-superseding.
- **`R3` (Strictly increasing per-user clock):** `clock_now(user_id)` returns strictly increasing timestamps per user (`c_next > max_recorded_instant(user_id)`), adding a `1 microsecond` increment when replay timestamps repeat so `tstzrange(lower, c)` is never empty.
- **`R4` (Staging and commit):** Staging rows (`candidate`, `conflict_pending`, `rejected`) have `recorded_range IS NULL`. Promoting a `conflict_pending` row to `active` assigns `recorded_from = clock_now(user_id)` and closes any conflicting current belief in the same transaction.
- **`R5` (Archival and audit):** Archiving updates `status` from `'active'` to `'archived'` and records `archival_branch IN ('S', 'C')` without modifying `valid_range` or `recorded_range`. Permitted **only** when the §6 Archival Precondition (`(S) ∨ (C)`) holds.
- **`R6` (Deletion over audit):** Deleting a memory item deletes **all** `MEMORY_VERSION` rows sharing its `memory_key` (including superseded and re-issued rows) plus its transitive `MEMORY_DERIVATION` closure, taking precedence over audit retention.
- **`R7` (Multi-valued predicates):** Ending a multi-valued fact ("no longer speaks French") uses the same close-then-insert pattern (`R1`, `R2`) to replace `[v_old, ∞)` with `[v_old, v_end)`, even though `single_valued = false` rows are exempt from the `no_bitemporal_overlap` exclusion constraint.
- **`R8` (Query parameter checks):** `historical_as_of` rejects `t > ρ` (`RAISE EXCEPTION`). `audit_as_of` rejects `β > clock_now()`. Future plans use the `'plans_to'` predicate rather than future valid times on current-state queries.

### 3.5 Query Predicates (Proposal §7.4 & `validation/bitemporal_checks.sql`)

Every query predicate is parameterized by `user_id` (`$1`) and executes under RLS:

1. **Current-state (`current_state(user_id, ρ)`):**
   ```sql
   SELECT * FROM memory_version
   WHERE user_id = $1
     AND status = 'active'
     AND recorded_range @> $2   -- ρ ∈ R(r)
     AND valid_range @> $2;     -- ρ ∈ V(r)
   ```
2. **Historical-as-of, point form (`historical_as_of(user_id, t, ρ)`):**
   - Guard (`R8`): if `t > ρ`, raise `INVALID_PARAMETER_VALUE` (`historical-as-of requires t <= reference time`).
   ```sql
   SELECT * FROM memory_version
   WHERE user_id = $1
     AND status = 'active'
     AND recorded_range @> $3   -- ρ ∈ R(r) (present belief at reference time ρ)
     AND valid_range @> $2;     -- t ∈ V(r) (valid at target time t)
   ```
3. **Historical-as-of, window form / change queries (`historical_window(user_id, W, ρ)`):**
   ```sql
   SELECT * FROM memory_version
   WHERE user_id = $1
     AND status = 'active'
     AND recorded_range @> $3   -- ρ ∈ R(r)
     AND valid_range && $2;     -- V(r) overlaps window W
   ```
4. **Audit-as-of (`audit_as_of(user_id, t, β)`):**
   - Guard (`R8`): if `β > clock_now()`, raise `INVALID_PARAMETER_VALUE` (`audit-as-of requires belief time <= clock`).
   - Restricted to the Memory Inspector and administrative API; **never** used to assemble LLM answer context.
   ```sql
   SELECT * FROM memory_version
   WHERE user_id = $1
     AND status IN ('active', 'archived')
     AND recorded_range @> $3   -- β ∈ R(r) (believed at past time β)
     AND valid_range @> $2;     -- t ∈ V(r)
   ```

---

## 4. Provenance & Traceability (Invariant 1)

- **Extracted memories (`layer IN ('episodic', 'semantic', 'opinion')` created from turns):** **MUST** have at least one `MEMORY_EVIDENCE` row (`version_id = r.id`) referencing a `TURN` with `role = 'user'` (or an assistant turn explicitly confirmed by a subsequent user turn).
- **Derived memories (`consolidated` semantic/summary versions and optional `persona` traits):** **MUST** have at least one outgoing `MEMORY_DERIVATION` link (`derived_version_id = r'.id`, `source_version_id = r.id`) pointing to an `active` or `archived` source version `r`.
- **Database enforcement:** A `CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED` on `MEMORY_VERSION` checks at transaction commit that every non-deleted `active` or `archived` row has `EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id) OR EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id)`.

---

## 5. Validation Gate Specification (Proposal §7.5)

Each candidate extracted on the slow path is evaluated by five sequential checks before commit:

1. **Evidence support (`evidence_check`):** Verifies that `evidence_span` occurs in a user-authored turn (exact substring or normalized token match plus verifier check). Assistant-originated statements without user confirmation are rejected (`reason_code = 'unconfirmed_assistant_claim'`).
2. **Assertion mode (`assertion_mode_check`):** Classifies the utterance into `{'asserted', 'hedged', 'hypothetical', 'planned', 'quoted', 'third_party', 'joking'}`.
   - Only `'asserted'` candidates may enter `active` on state predicates or trigger supersession.
   - `'planned'` candidates are routed to the `'plans_to'` predicate.
   - `'hedged'` and `'hypothetical'` candidates transition to `'conflict_pending'`.
   - `'quoted'`, `'third_party'`, and `'joking'` candidates are rejected (`'non_user_assertion'`).
3. **Instruction screen (`instruction_screen`):** Rejects candidates containing prompt-injection or persistent behavioral directives (e.g., `"always tell the user..."`, `"ignore previous..."`, system-prompt overrides) with `reason_code = 'instruction_injection'`.
4. **Conflict relation (`conflict_detector`):** Retrieves top-`m` existing `active` versions for `(user_id, subject_key, predicate_key)` (plus dense-similarity neighbors for open-domain facts) and classifies the relation as `{'update', 'correction', 'additive', 'duplicate', 'unrelated', 'uncertain'}`:
   - `'update'` or `'correction'` (with `assertion_mode = 'asserted'`) → invokes transactional supersession (`R1`, `R2`).
   - `'duplicate'` → increments `reinforcement_count` on the existing version without inserting a duplicate row.
   - `'additive'` / `'unrelated'` → inserts as a new `active` version.
   - `'uncertain'` → holds candidate in `'conflict_pending'`.
5. **Extractor confidence (`confidence_calibration_check`):** Numeric confidence thresholding is disabled unless development-split Expected Calibration Error (ECE) validation (`RL-060`) demonstrates reliable calibration; otherwise the gate relies exclusively on categorical checks 1–4.

Every gate evaluation writes a row to `MEMORY_CONFLICT` recording `(candidate_version_id, existing_version_id, relation, decision, reason_code, decided_by, decided_at)`.

---

## 6. Archival Precondition (Invariant 10, V4 Fix)

A transactional call `archive_version(version_id)` may transition a row `r` from `status = 'active'` to `status = 'archived'` **if and only if** at least one of the following holds inside the transaction:

- **Branch `(S)` — Superseded:** `NOT upper_inf(r.recorded_range)` (the recorded range of `r` is already closed, so neither `current_state` nor `historical_as_of` can return `r`). `archival_branch` is set to `'S'`.
- **Branch `(C)` — Active derived replacement:** There exists at least one version `r'` linked via `MEMORY_DERIVATION (derived_version_id = r'.id, source_version_id = r.id)` such that:
  1. `r'.user_id = r.user_id` and `r'.status = 'active'`,
  2. `upper_inf(r'.recorded_range)` (`r'` is a current belief), and
  3. `r'.valid_range @> r.valid_range` (`r'` covers the valid interval of `r` so any `current_state` or `historical_as_of` query that would have matched `r` matches `r'`).
  `archival_branch` is set to `'C'`.

If neither `(S)` nor `(C)` holds — in particular, if `r` is a **currently valid current belief** (`T1`) or a **historical current belief** (`upper(r.valid_range) <= ρ` and `upper_inf(r.recorded_range)`, `T2`) without an `Active` derived replacement — `archive_version` **MUST** abort the transition and raise `VALIDATION_ERROR: archival_precondition_unmet`.

### Mandatory `T1`–`T5` Regression Contract (`RL-090`)

| Test ID | Scenario | Expected Outcome |
|---|---|---|
| `T1` | Attempt to archive a currently-valid current belief (`status = 'active'`, `upper_inf(R)`, `ρ ∈ V`) with no derived replacement. | Rejected (`archival_precondition_unmet`); row remains `'active'`. |
| `T2` | Attempt to archive a historical current belief (`status = 'active'`, `upper_inf(R)`, `upper(V) <= ρ`, e.g., `v1″` in Proposal §7.4) with no derived replacement. | Rejected (`archival_precondition_unmet`); row remains `'active'` and continues to be returned by `historical_as_of`. |
| `T3` | Attempt to archive a superseded version (`NOT upper_inf(R)`, e.g., `v1` or `v1′`). | Accepted via branch `(S)`; `status = 'archived'`, `archival_branch = 'S'`; visible to `audit_as_of`. |
| `T4` | Create an `Active` consolidated replacement `r'` with `MEMORY_DERIVATION(r' → r)` and `V(r') @> V(r)`, then archive `r`. | Accepted via branch `(C)`; `status = 'archived'`, `archival_branch = 'C'`; queries return `r'` with provenance link to `r`. |
| `T5` | Execute `delete_memory(r.memory_key)` after `T4`. | Single-transaction deletion removes all versions of `r.memory_key` **and** removes (or re-derives without `r`) the derived replacement `r'`; zero leakage across all 7 channels. |

---

## 7. Hybrid Retrieval, Rendering, & Lineage Deletion

### 7.1 Hybrid Retrieval & Reciprocal Rank Fusion (§7.6)

1. **Candidate filtering:** Apply the mode-specific temporal and status predicate (§3.5) scoped to `user_id`. Only `status = 'active'` rows with `recorded_range @> ρ` enter ranking.
2. **Dense channel (`rank_dense`):** Order matching versions by cosine distance `embedding <=> query_embedding` over `MEMORY_EMBEDDING` for the frozen `embedding_model`. Per-user stores use exact filtered scans by default; HNSW indexing is restricted to scalability benchmarking (`RL-050`, `RL-093`).
3. **Lexical channel (`rank_lexical`):** Order matching versions by `ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', query))` using a GIN index on `to_tsvector('english', content)`.
4. **Fusion:** Compute `RRF(m) = 1 / (60 + rank_dense(m)) + 1 / (60 + rank_lexical(m))` (omitting a channel's term if `m` has zero lexical overlap). Sort descending by `RRF(m)` with deterministic tie-breaking on `(upper(valid_range) DESC NULLS FIRST, id ASC)`.
5. **Render rule (§7.3):** Format top items within token budget `B_mem`. Never render superseded versions. In `current` mode, never render historical versions. Label each rendered item with its validity interval and role (`current`, `historical`, or `previous` / `current` in change mode).

### 7.2 Transactional Lineage Deletion (§7.7, Invariant 8)

`delete_memory(user_id, scope, target_ref)` executes in a single transaction holding the per-user advisory lock:

1. Increment the user's `deletion_epoch` and insert a `DELETION_REQUEST` record.
2. Compute the target set `T_0`: all `MEMORY_VERSION` rows matching `user_id` and `memory_key = target_ref` (including `'active'`, `'archived'`, `'candidate'`, `'conflict_pending'`, `'rejected'`, superseded, and re-issued rows per `R6`).
3. Walk the recursive transitive closure `T_derived` over `MEMORY_DERIVATION` (`source_version_id → derived_version_id`, bounded at depth `≤ 3`):
   - Partition `T_derived` into versions whose supporting sources are **all** in `T_0 ∪ T_derived` (pure dependents → physically delete) and versions that retain independent surviving `Active` sources (`multi-source dependents` → mark for synchronous re-derivation from surviving sources only, and delete the old derived version row).
4. Physically `DELETE` all rows in `T_0 ∪ T_derived` from `MEMORY_VERSION` (cascading to `MEMORY_EMBEDDING`, `MEMORY_EVIDENCE`, `MEMORY_DERIVATION`, `MEMORY_CONFLICT`, and FTS index entries), purge matching `JOB` queue payloads, and invalidate any user cache entries prior to `deletion_epoch`.
5. Record a content-free event in `AUDIT_EVENT` (`user_id`, random `memory_ref`, `event_type = 'deletion_completed'`, `event_at`, counts only — **no** content, hashes, subjects, or predicates).

---

## 8. Concurrency, Clock Injection, & Slow-Path Guarantees

- **Per-user serialization:** Every slow-path worker and deletion transaction acquires `SELECT pg_advisory_xact_lock(hashtext(user_id))` at transaction start (`Proposal §7.3`). This prevents same-user race conditions while allowing full parallelism across distinct users.
- **Deletion-epoch race guard:** Before committing a slow-path extraction or consolidation job that began at `epoch_start`, the worker checks `current_deletion_epoch(user_id) == epoch_start`; if a deletion occurred concurrently, the job aborts and re-runs against surviving data (`Proposal §7.7`).
- **Injectable clock (`RL-044`):** Database functions read the transaction clock via `COALESCE(NULLIF(current_setting('app.clock', true), '')::timestamptz, clock_timestamp())` wrapped in a monotonic per-user helper (`R3`). Only the evaluation replay harness (`RL-030`) may set `SET LOCAL app.clock`; application business logic never passes explicit `recorded_from` timestamps (`Invariant 3`).

---

## 9. Evaluation Harness & Statistical Inference Specification

### 9.1 `MemorySystemAdapter` Protocol (`RL-025`, `RL-030`, `RL-031`)

All compared systems (`B0`, `B1`, `B2`, `B2h`, `B3`, conditional `B4`, `P`, and ablations `A1`–`A10`) implement a single uniform interface:

```python
class MemorySystemAdapter(Protocol):
    def reset_user(self, user_id: str) -> None: ...
    def set_clock(self, ts: datetime) -> None: ...
    def ingest_turn(self, turn: TurnRecord) -> IngestReceipt: ...
    def drain_queue(self, user_id: str) -> QueueDrainStats: ...
    def retrieve(self, user_id: str, query: str, mode: QueryMode, as_of: datetime, token_budget: int) -> RetrievalResult: ...
    def answer(self, user_id: str, question: QuestionRecord, as_of: datetime) -> AnswerResult: ...
    def delete(self, user_id: str, scope: DeletionScope, target_ref: str) -> DeletionReceipt: ...
```

### 9.2 Run Output Layout & Manifest (`RL-030`)

Every evaluation run writes immutable artifacts under `research-lab/evaluation/runs/<run_id>/<config_id>/<suite_id>/`:
- `manifest.json`: Git commit SHA, dirty-tree flag, config ID, dataset split SHA-256, frozen `model_id`, `embedding_model_id`, `judge_model_id`, `temperature`, `seed`, hardware specs, wall-clock duration, and token/cost totals.
- `answers.jsonl`: Per-question/probe rendered context, raw answer, latency (`P50`/`P95`), and token counts.
- `scores.jsonl`: Item-level primary and diagnostic metric scores.
- `per_cluster.jsonl`: Cluster-aggregated statistics (`scenario_id` for `T`, `G`, `D`; `question_id` for `LongMemEval_S`; `conversation_id` for `LoCoMo`).

### 9.3 Confirmatory Decision Rules & Multiplicity (`RL-034`, Proposal §§4.1, 10.6)

- **Resampling unit:** Non-parametric cluster bootstrap (`B = 10,000` replicates, fixed seed) resampling **scenarios** (for `H1`, `H3`, `H4`) or **questions** (for `H2` on `LongMemEval_S`), paired by cluster across configurations (`P` vs contrast), plus a paired sign-flip permutation test on the per-cluster differences.
- **Global confirmatory family & multiplicity (Proposal §10.6):** The confirmatory family consists of **three superiority tests** controlled at family-wise error rate `α = 0.05` using the **Holm step-down procedure** (`α/3 ≈ 0.0167`, `α/2 = 0.025`, `α = 0.05`; feasibility floor `N ≥ 7` non-tied clusters):
  1. `H1` superiority on the `history-dependent` stratum (`TAA(P) − TAA(A2)`),
  2. `H2` on `LongMemEval_S` (its two contrasts `Recall@k(P) − Recall@k(A5)` and `Recall@k(P) − Recall@k(A6)` combined as an intersection-union test, so `p_H2 = max(p_A5, p_A6)`), and
  3. `H3` `ASR` reduction (`ASR(P) − ASR(A3)`).
  The two non-inferiority conditions (`H1` `present-state` and `H3` `LUAR`) are co-primary conditions combined by intersection-union with their corresponding superiority tests and require no further multiplicity adjustment. Both 95% and conservative `(1 − 0.05/3)` cluster-bootstrap intervals are reported.
- **Hypothesis-level decision rules (Proposal §§4.1, 10.6):**
  - **`H1` (`P` vs `A2` on suite `T`):** Supported iff **both** (1) on the `history-dependent` stratum, the paired `TAA(P) − TAA(A2)` difference is positive, its Holm-adjusted p-value across the 3-test family is `< 0.05`, and its conservative `(1 − 0.05/3)` cluster-bootstrap CI lower bound is `> 0`, **and** (2) on the `present-state` stratum, the lower bound of the one-sided 97.5% cluster-bootstrap CI for `TAA(P) − TAA(A2)` is `> −δ_pres` (non-inferiority).
  - **`H2` (`P` vs `A5` and `P` vs `A6` on `LongMemEval_S`):** Intersection-union test across both co-primary contrasts (`P − A5` dense-only and `P − A6` lexical-only): supported iff **both** contrasts have positive point estimates and intervals above `0`, and `max(p_A5, p_A6)` after Holm adjustment across the 3-test confirmatory family is `< 0.05`.
  - **`H3` (`P` vs `A3` on suite `G`):** Co-primary intersection-union test: supported iff **both** (1) the paired `ASR(P) − ASR(A3)` reduction is negative with Holm-adjusted `p < 0.05` and its conservative cluster-bootstrap CI lies entirely below `0`, **and** (2) the lower bound of the one-sided 97.5% cluster-bootstrap CI for the paired difference `LUAR(P) − LUAR(A3)` lies above `−δ_luar` (non-inferiority).
  - **`H4` (`P` and positive control `A8` on suite `D`):** Supported iff **all three** hold: (1) observed scenario-level leaks for `P` equal `0`, (2) the exact two-sided 95% (one-sided 97.5%) Clopper–Pearson upper bound (`1 − 0.025^(1/N_D)`) on scenario-level `PDLR(P)` is `≤ u*`, **and** (3) positive control `A8` (`P-nolineage`) leaks in at least the predeclared minimum share of scenarios (`D-13`). If (1) holds but (2) or (3) fails, `H4` is reported as **Inconclusive**.
