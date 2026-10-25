# Decisions Register (`custom-memory-engine/`)

**Single Source of Truth:** This register tracks every architectural, methodological, and engineering decision for the Custom Memory Engine workspace.  
**Decision Memos:** Canonical Phase P1 records are in [`../decisions/`](../decisions/) and mirrored in [`decisions/`](decisions/) (`RL-010`, `RL-012`–`RL-016`). All specifications, benchmarks, evaluation harnesses, and engine modules are consolidated under `custom-memory-engine/`.

**2026-10-25 status update:** The selected-value table below is authoritative over the original dispatch recommendations in §2.2. Phase P1 issues (`#1`–`#6`), Phase P0 workspace issues (`#9`–`#13`), and Wave 2 pilot/benchmark issues (`#14`–`#19`: `RL-011`, `RL-020`–`RL-024`) are implemented and verified locally. Project Owner single-maintainer review and self-attestation closed `#1`–`#6` and `#9`–`#13` on GitHub (2026-10-24); institutional supervisor approval and Phase P10 pilot freeze (`RL-100`) remain separate downstream gates before confirmatory test runs.

| ID | Current status | Selected value / disposition | Record |
|---|---|---|---|
| D-01 | PREPARED; supervisor pending | `gpt-4.1-mini-2025-04-14`, temperature 0; proposed aggregate USD 100 / 50M-token ceiling, dev USD 20 | [RL-010](../decisions/RL-010-scope-and-models.md) |
| D-02 | PREPARED; supervisor pending | BAAI/bge-small-en-v1.5 at `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, d=384 | RL-010 |
| D-03 | PREPARED; supervisor pending | `gpt-4.1-2025-04-14`; same-family bias disclosed; validation required | RL-010 |
| D-04 | PREPARED; supervisor pending | A10 OFF in P, exploratory SKIP | RL-010 |
| D-05 | PREPARED; supervisor pending | A9 OFF in P, exploratory SKIP | RL-010 |
| D-06 | PREPARED; supervisor pending | BM25 OFF / SKIP; PostgreSQL FTS primary | RL-010 |
| D-07 | PREPARED; supervisor pending | Redis/Celery/ARQ OFF / SKIP; PostgreSQL JOB queue | RL-010 |
| D-08 | PREPARED; supervisor pending | H5 OFF; zero recruitment | RL-010 |
| D-09–D-14 | PROTOCOL PRE-REGISTERED (`RL-011`); freeze in `RL-100` | Deterministic dev-split measurement and freeze functions in [`../evaluation/pilot_plan.md`](../evaluation/pilot_plan.md) & `pilot_rules.py` | [`pilot_plan.md`](../evaluation/pilot_plan.md) |
| D-15 | SELECTED; owner-reviewed | A2b OFF / SKIP | [RL-014](../decisions/RL-014-a2b-and-suite-d.md) |
| D-16 | SELECTED target; implemented in `RL-023` | Dev 20 / test 50 independent scenarios; test zero-leak upper bound 0.071121736464; D-13 remains pilot-derived | RL-014 |
| D-17 | VERIFIED; owner-reviewed | CPython 3.12.12, Psycopg 3.3.2 + pool 3.3.3, pgvector client 0.4.2, Pydantic 2.12.5; standalone uv lock | [RL-013](../decisions/RL-013-engine-runtime.md) |
| D-18 | SELECTED; load validation pending | Per-user advisory lock + exclusion + R1; measured SERIALIZABLE candidate, bounded whole-transaction tenacity retries for 40001/40P01 | [RL-012](../decisions/RL-012-postgres-pgvector.md) |
| D-19 | NOT APPLICABLE to disabled scope | H5 OFF; no institutional exemption asserted; written clearance required before recruitment if reopened | [RL-015](../decisions/RL-015-ethics-h5.md) |
| D-20 | SELECTED; owner-reviewed | React/TypeScript/Vite primary; server HTML/HTMX schedule fallback | RL-013 |
| D-21–D-22 | PROTOCOL PRE-REGISTERED (`RL-011`); freeze in `RL-100` | Deterministic `B4` inclusion gate (`≤25%` budget cap) and `LongMemEval_S` full vs. seeded stratified subset rule (`seed=42`) | [`pilot_plan.md`](../evaluation/pilot_plan.md) |
| D-23 | SELECTED & VERIFIED on `oracle`/`S` (`RL-020`) | Stable session sort, preserve turns, R3 1 µs tie increments; retain anomalies (`34/43` on `oracle`; `211/76` full & `39/44` evidence on `S`, `95` tied) with ρ=max(question_date,last recorded turn clock) | [RL-016](../decisions/RL-016-paired-d23-d24-d26.md) |
| D-24 | PREPARED; owner-reviewed, human audit in `RL-033` | At least 100 stratified dev items; dual blind annotation, inter-human κ≥0.75, judge κ≥0.75 and accuracy≥0.90; coverage, perturbation and approval gates | RL-016 |
| D-25 | VERIFIED; owner-reviewed | PostgreSQL 16.15 / pgvector 0.8.7 / btree_gist 1.7; `pgvector/pgvector:0.8.7-pg16-bookworm@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a` | RL-012 |
| D-26 | PREPARED; implemented in `RL-022`–`RL-024` | MIT for original generators/suites (`T, G, D, I`) only; zero LoCoMo text/schema reuse; public release blocked until pilot validation (`RL-132`) | RL-016 |
| D-27 | PREPARED; supervisor pending | Transactional archive_version + trigger and bypass restrictions; V4 S∨C; T1–T5 full-engine tests still required | RL-010 |

---

## 1. Status Legend

- **`FROZEN`**: Locked by the V4 proposal (`Graduation_Project_Proposal.md`) or owner decision. Cannot change without a documented revision and re-running `validation/mechanical_checks.py`.
- **`NEEDS SUPERVISOR SIGNOFF`**: Pre-implementation scope, model, or ethics decision assigned to `RL-010`, `RL-015`, or `RL-016`.
- **`OPEN`**: Technical or methodological design decision assigned to a Phase P1 task (`RL-012`–`RL-016`).
- **`PILOT-DERIVED`**: Numeric threshold or operational toggle whose measurement protocol is specified in `RL-011` (`evaluation/pilot_plan.md`) and frozen on the development split in `RL-100` before any test-split experiment runs.

---

## 2. Canonical Decisions Table

### 2.1 Frozen Architectural & Protocol Decisions

| ID | Topic | Status | Decision / Rule | Authority |
|---|---|---|---|---|
| `D-ARCH` | Repository & runtime architecture | `FROZEN` | **Option C, placement revised by owner 2026-10-24:** Self-contained implementation in root `custom-memory-engine/`; Research Lab remains the specification/reference workspace; strict dependency, database and runtime isolation from `app/`. | [`ARCHITECTURE.md`](ARCHITECTURE.md), [`INDEPENDENCE_BOUNDARY.md`](INDEPENDENCE_BOUNDARY.md) |
| `D-LAB-CI` | Research Lab CI workflow | `FROZEN` | Dedicated `.github/workflows/research-lab.yaml` workflow triggered on `research-lab/**` paths (`RL-005`). | [`INDEPENDENCE_BOUNDARY.md`](INDEPENDENCE_BOUNDARY.md) §4 |
| `D-ENG-01` | Primary `H1` confirmatory contrast | `FROZEN` | `P − A2` (`P` vs `P-nohistory`) across both co-primary strata on suite `T`. `P − A1` (`P-flat`) is diagnostic only. | Proposal §§4.1, 10.4 |
| `D-ENG-02` | V4 Archival Precondition (`Invariant 10`) | `FROZEN` | `Active → Archived` allowed iff `(S)` `recorded_range` is closed OR `(C)` an `Active` derived replacement `r'` exists with `MEMORY_DERIVATION(r' → r)` and `V(r') @> V(r)`. V3 valid-ended branch `(A)` is removed. | Proposal §§7.4 `R5`, 7.5, 8 Inv. 10 |
| `D-ENG-03` | Hybrid retrieval fusion constant | `FROZEN` | Reciprocal Rank Fusion `RRF(m) = Σ_c 1 / (60 + rank_c(m))` (`k = 60`). | Proposal §7.6 |
| `D-ENG-04` | Controlled deletion boundary | `FROZEN` | Covers PostgreSQL tables, FTS/vector indexes, `JOB` queue, caches, assembled context, answers, and logs (7 channels). Excludes WAL/backups, unvacuumed pages, external provider logs, already-streamed text, and model weights. | Proposal §§7.7, 10.3 |

### 2.2 Phase P1 Pre-Implementation Decisions (`RL-010`, `RL-012`–`RL-016`)

| ID | Topic | Status | Owning Task / Issue | Recommended Default / Constraint | Blocks |
|---|---|---|---|---|---|
| `D-01` | Frozen answering/extraction LLM (model ID, snapshot, decoding params, budget ceiling) | `NEEDS SUPERVISOR SIGNOFF` | `RL-010` (`#1`) | Pin one open-weight or hosted model at `temperature = 0` with seed logging and explicit USD/token ceiling. | `RL-022`, `RL-030`, `RL-031`, `RL-040`, `RL-100` |
| `D-02` | Frozen embedding model & vector dimension `d` | `NEEDS SUPERVISOR SIGNOFF` | `RL-010` (`#1`) | Pin one open-weight local embedder (e.g., `bge-small-en-v1.5` or `nomic-embed-text-v1.5`) across all configurable systems. | `RL-031`, `RL-040`, `RL-050`, `RL-100` |
| `D-03` | Validated LLM judge model | `NEEDS SUPERVISOR SIGNOFF` | `RL-010` (`#1`) | Pin a judge model distinct from `D-01` where feasible to prevent self-preference bias (`Proposal §10.5`). | `RL-033`, `RL-100` |
| `D-04` | Optional Persona layer (`A10`) | `NEEDS SUPERVISOR SIGNOFF` | `RL-010` (`#1`) | Keep **OFF** in `P` (mandatory per V4) and **SKIP** exploratory implementation unless capacity remains after `P7`. | `RL-081` |
| `D-05` | Optional retention-weighted ranking (`A9`) | `NEEDS SUPERVISOR SIGNOFF` | `RL-010` (`#1`) | Keep **OFF** in `P` and **SKIP** exploratory implementation unless capacity remains after `P7`. | `RL-080` |
| `D-06` | Optional BM25 lexical channel | `NEEDS SUPERVISOR SIGNOFF` | `RL-010` (`#1`) | Keep **OFF** unless pilot shows PostgreSQL `ts_rank_cd` is inadequate on the dev split. | `RL-082` |
| `D-07` | Optional Redis / Celery / ARQ queue | `NEEDS SUPERVISOR SIGNOFF` | `RL-010` (`#1`) | Keep **OFF**; use PostgreSQL `JOB` table with `FOR UPDATE SKIP LOCKED` to keep the deletion boundary single-store. | `RL-083` |
| `D-08` | Optional usability study (`H5`) | `NEEDS SUPERVISOR SIGNOFF` | `RL-010` (`#1`), `RL-015` (`#5`) | Keep **OFF** unless supervisor and university ethics review (`D-19`) explicitly approve before recruitment. | `RL-122` |
| `D-15` | Exploratory ablation `A2b` (valid-time history without `recorded_range`) | `OPEN` | `RL-014` (`#4`) | Recommend **OFF** (or low-priority exploratory) because `A2` is the sole confirmatory contrast for `H1`. | `RL-024`, `RL-101` |
| `D-16` | Suite `D` scenario cardinality `N_D` | `OPEN` | `RL-014` (`#4`) | Select `N_D` from `validation/cp_bounds.output.txt` (e.g., `N_D = 36` yields exact 97.5% upper bound `0.0974 ≤ 0.10` at 0 leaks; `N_D = 60` yields `0.0596`). | `RL-011`, `RL-023`, `RL-105` |
| `D-17` | Engine implementation stack & SQL driver | `OPEN` | `RL-013` (`#3`) | Python `3.12` + `psycopg` v3 (`psycopg[binary,pool]`) + `pgvector` + explicit SQL functions/triggers (`validation/bitemporal_checks.sql` pattern). | `RL-003`, `RL-030`, `RL-040` |
| `D-18` | Invariant 4 (`btree_gist`) concurrent-writer strategy | `OPEN` | `RL-012` (`#2`) | Combine `EXCLUDE USING gist` with per-user `pg_advisory_xact_lock(hashtext(user_id))`; promote to `SERIALIZABLE` + retry if `RL-093` load test shows GiST contention. | `RL-040`, `RL-042`, `RL-093` |
| `D-19` | University ethics / IRB determination for optional `H5` | `NEEDS SUPERVISOR SIGNOFF` | `RL-015` (`#5`) | Confirm written exemption/approval requirement with supervisor; if `D-08 = OFF`, record `NOT APPLICABLE`. | `RL-122` |
| `D-20` | Personal Digital Twin (PDT) UI framework | `OPEN` | `RL-013` (`#3`) | Minimal React + TypeScript (`Vite`) per Proposal §9, or lightweight server-rendered HTML fallback if schedule is tight in `P12`. | `RL-121` |
| `D-23` | `LongMemEval` timestamp anomaly rule (43/500 questions with `question_date < last_turn_ts`) | `OPEN` | `RL-016` (`#6`) | Recommended: keep all 43 questions with `ρ = max(question_date, last_turn_ts)` (`Proposal §7.4` rule `3a`) and report a sensitivity check excluding them. | `RL-020`, `RL-025`, `RL-102` |
| `D-24` | Judge human-validation threshold (`κ_min` and dev sample size) | `OPEN` | `RL-016` (`#6`) | Recommended: `n ≥ 100` stratified dev items scored by 2 human annotators with `Cohen's κ ≥ 0.75` required before scoring test splits (`Proposal §10.5`). | `RL-033`, `RL-100`, `RL-104` |
| `D-25` | Pinned PostgreSQL & `pgvector` version/image | `OPEN` | `RL-012` (`#2`) | Pin PostgreSQL `16.x` (matching the `16.15` reduced-schema check) + `pgvector` (`v0.7+` / `v0.8+` with iterative scan support) by Docker image digest. | `RL-004`, `RL-040` |
| `D-26` | Own-suite (`T`, `G`, `D`, `I`) open release license | `NEEDS SUPERVISOR SIGNOFF` | `RL-016` (`#6`) | Release under `MIT` or `CC BY 4.0` after pilot validation (`Proposal §10.2`), ensuring zero content copied from `LoCoMo` (`CC BY-NC 4.0`). | `RL-132` |
| `D-27` | `T1`–`T5` archival precondition enforcement architecture | `NEEDS SUPERVISOR SIGNOFF` | `RL-010` (`#1`) | Enforce `(S) ∨ (C)` inside the transactional `archive_version` SQL/engine procedure with a `BEFORE UPDATE` trigger check on `MEMORY_VERSION.status = 'archived'`. | `RL-042`, `RL-061`, `RL-090` |

### 2.3 Pilot-Derived Decisions (`RL-011` Protocol → Frozen in `RL-100`)

| ID | Parameter | Target Hypothesis / Component | Measurement Protocol (`RL-011`) | Freeze Gate |
|---|---|---|---|---|
| `D-09` | Retrieved memory token budget `B_mem` | All systems (`B2`, `B2h`, `B3`, `B4`, `P`, `A1`–`A10`) | Inspect recall-vs-token curve on dev split (`512`, `1024`, `2048` tokens). | `RL-100` |
| `D-10` | Primary `k` in `Evidence Recall@k` (plus secondary `k` set) | `H2` (`LongMemEval_S`) | Inspect `k ∈ {3, 5, 10}` on dev split; designate one primary `k` before test freeze. | `RL-100` |
| `D-11` | Present-state non-inferiority margin `δ_pres` | `H1` (`TAA` present-state stratum) | Estimate scenario-level SD and B0/P gap on Suite `T` dev split (`Proposal §4.1`). | `RL-100` |
| `D-12` | Legitimate-update acceptance non-inferiority margin `δ_luar` | `H3` (`LUAR` on Suite `G`) | Calibrate gate acceptance vs. `A3` on Suite `G` dev split (`Proposal §4.1`). | `RL-100` |
| `D-13` | Scenario-level PDLR upper-bound threshold `u*` and minimum `A8` positive-control leak share | `H4` (`PDLR` on Suite `D`) | Pair `u*` with `N_D` (`D-16`) so `1 − 0.025^(1/N_D) ≤ u*` (e.g., `u* = 0.10` for `N_D ≥ 36`), and set minimum `A8` detection share (e.g., `≥ 0.80`) on dev split. | `RL-100` |
| `D-14` | Final test-split scenario counts & Suite `T` stratum ratio | Suites `T`, `G`, `D`, `I` | Compute scenario-level variance of paired differences on dev split to size test splits for target CI width (`Proposal §§10.2, 10.6`). | `RL-100` |
| `D-21` | Conditional inclusion of Baseline `B4` (Graphiti) | `B4` (`Proposal §10.1`) | Run `B4` adapter on dev sample in `RL-031`/`RL-100`; include iff end-to-end execution succeeds with frozen models within budget (`D-01`). | `RL-100` |
| `D-22` | Full `LongMemEval_S` (500 histories) vs. seeded stratified subset | `H2` (`Proposal §10.2`) | Measure per-history extraction cost in `RL-100`; if 500 histories exceed `D-01` budget, freeze a seeded stratified subset prior to test run. | `RL-100` |

---

## 3. Decision Memo Template (`PLAN/decisions/<ID>.md`)

Every Phase P1 decision task (`RL-010`, `RL-012`–`RL-016`) commits a Markdown record under `custom-memory-engine/decisions/` (owner placement revision, 2026-10-24) containing:
1. **Decision ID(s) & Title**
2. **Selected Option / Frozen Value**
3. **Evidence & Justification** (citing `Graduation_Project_Proposal.md`, `TECHNICAL_SPECIFICATION.md`, or empirical check output)
4. **Downstream Impact** (tasks unblocked and files updated)
5. **Sign-Off** (Owner / Supervisor / Peer Reviewer and date)
