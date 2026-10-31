# V1 → V2 Transition Audit

**Date:** 2026-10-31 · **Scope:** the complete GitHub issue backlog (35 v1 issues), the
v1 milestones, and every `RL-*` task claimed by the planning documents.
**Method:** independent inspection of the repository at `main` @ `998558f` and the live
issue tracker — **not** a reading of the documents' own claims. Where a document says a
task is complete and the code disagrees, the code wins and the disagreement is recorded.

**Governing rule for this audit:** no issue is closed, recreated, retitled, or deleted to
make the backlog look clean. Dispositions are recorded as **additive comments**, and
milestones are re-pointed without altering history.

---

## 1. Ground truth established by inspection

| Check | Command / artefact | Result |
|---|---|---|
| Issue backlog | `gh issue list --state all` | 35 issues: 33 closed, 2 open (#46, #47) |
| v1 milestones | `gh api …/milestones` | 8 milestones (P0–P7), all but P7 fully closed |
| Test suite | see §1.1 (three scopes, each reproduced) | full CI scope: **1374 passed, 21 skipped** (1395 collected); non-integration: **1234 passed, 18 skipped, 143 deselected** (1252 selected) — *r4 originally printed "1252 passed" here; corrected in r4.1, see §7* |
| Frozen research record | `md5sum -c validation/document_hashes.txt` | **all 5 OK** |
| Engine modules present | `ls src/custom_memory_engine/*/` | `engine/`, `schema/`, `clock/`, `queue/`, `consolidation/` |
| Benchmarks present | `ls research/benchmarks/` | `longmemeval/`, `locomo/`, `own_{G,I,D,T}/`, `splits/` ✅ (the audit's first pass used wrong paths; corrected) |
| CI present | `ls .github/workflows/` | `custom-memory-engine.yaml`, `ci.yaml`, … |
| Security package | `ls src/custom_memory_engine/security/` | **does not exist** |
| `A8` ablation | `grep -n 'type AblationMode'` | `Literal["P","A1","A2","A2b"]` — **no A8** |
| Archival T1–T5 suite | `ls tests/integration/archival/` | **does not exist**; T1–T4 logic lives inline in `test_transactions_db.py` / `test_consolidation_db.py` |
| Invariants suite | `ls tests/integration/invariants/` | **does not exist** (RL-091) |
| Load/HNSW test | `tests/integration/test_concurrency_and_hnsw.py` | **does not exist** (RL-093) |
| Lineage depth bound | `engine/deletion.py:40` | `MAX_LINEAGE_DEPTH = 3` — correctness bound, flagged by V2 |

### 1.1 Test-count reconciliation (r4.1)

Three different figures appear in the project record. Each one is correct for its own
command and environment. They are not interchangeable. Every row below was reproduced at
`main` @ `998558f` on 2026-10-31, using a disposable `pgvector/pgvector:0.8.7-pg16` database
(`compose.yaml`; DSN from the local `.env`).

| Scope | Install | Command (in `custom-memory-engine/`) | Collected | Result |
|---|---|---|---:|---|
| **Full suite: CI `verify` job** | `uv sync --frozen --extra baselines` | `uv run pytest -q` | **1395** | **1374 passed, 21 skipped** |
| Non-integration | `--extra baselines` | `uv run pytest -q -m 'not integration'` | 1252 selected / 143 deselected | **1234 passed, 18 skipped** |
| Integration only | `--extra baselines` | `uv run pytest --collect-only -q -m integration` | 143 | collection only. These 143 run inside the full-suite row |
| Full suite: CI `default-install` job | `uv sync --frozen` (no extras) | `uv run pytest -q` | 1376 | 1356 passed, 23 skipped |
| Non-integration, no extras | `uv sync --frozen` | `uv run pytest -q -m 'not integration'` | — | 1216 passed, 20 skipped, 143 deselected |

**Which figure means what:**

- **1,374** is the full CI-scope pass count: 1395 collected, 21 env-gated skips (Falkor
  port, frozen model assets, and baseline-Postgres coordination are not provided locally). This
  matches `CHANGELOG.md`, `PROJECT_STATUS.md` and `REFACTORING.md`. It is the correct size of
  the **Tier-A regression wall** cited in `ROADMAP_V2.md` G1 and `README.md`.
- **1,252** is a **selection count**, not a pass count. It is the number of tests that
  `-m 'not integration'` selects (1395 − 143). The r4 reports wrote "1252 passed". That was
  wrong: 1252 = 1234 passed + 18 skipped.
- **1,234 / 18 / 143** is the non-integration run. It needs no database, and it is the figure
  the r3 baseline recorded.
- The no-extras rows differ because the vendor-baseline modules skip at module level when
  `redis`/`graphiti` are absent. So "collected" depends on the install as well as the code.
- `REFACTORING.md`'s "1240 passed, 155 skipped without a database" is a historical figure
  from an earlier commit. It is left untouched as a frozen record.

**Conclusion of §1:** the engine, schema, harness, suites and statistics modules are
real and tested. The three areas the docs called "remaining" are genuinely remaining:
security/RLS role (`security/` absent), deletion completeness (`A8` absent, scanner
absent), and the P9 verification suite (three directories absent).

---

## 2. Major finding: planning-document drift

The audit found one class of problem that is more serious than any missing feature:
**several documents assert status the repository does not support.**

| Document | Claim | Reality | Resolution |
|---|---|---|---|
| `PLAN/PROJECT_STATUS.md` §1 | Engine work "Not Yet Executed" | Implemented and tested | Header already corrects it; closed by #77 (`E-003`) |
| `PROJECT_ROADMAP.md` §A | `P0` "READY TO START" | P0 complete, milestone closed | Superseded by `PLAN/V2/ROADMAP_V2.md`; notice added |
| `PLAN/TASK_GRAPH.md` | Phase P0 `Complete` | Correct | Retained as v1 record |
| Issue #12 title | `.github/workflows/research-lab.yaml` | Actual file is `custom-memory-engine.yaml` | **Disposition `update`**, not `retain` |
| `REFACTORING.md` §6 | "Remaining: peer review on #45" | #45 closed; review recorded | No action needed |
| `PLAN/SCHEDULE.md` | 24-week P0–P13 calendar | Superseded by W0–W8 | Notice added by #77 |

**Why this matters for the transition:** an audit that trusted the documents would have
classified 33 issues as verified without opening a file. `E-001` (#50) exists precisely
to make this class of drift impossible to repeat — it generates `STATUS_AUDIT.md` from a
script rather than from prose.

---

## 3. Complete old → V2 mapping

### 3.1 Dispositions used

`retain` — no scope change · `extend` — retained, with V2 additions tracked elsewhere ·
`update` — acceptance criteria or deliverable names changed · `reorder` — dependencies
moved · `merge`/`split` — issue structure changed · `superseded` — V2 replaces the
mechanism · `verify` — implemented, needs verification work.

### 3.2 The 33 closed v1 issues

| # | RL | Title (short) | Disposition | V2 milestone | V2 home |
|---|---|---|---|---|---|
| 1 | RL-010 | Scope & supervisor decisions | retain | W0 | `D-01`–`D-08`, `D-27` still binding |
| 2 | RL-012 | PostgreSQL / pgvector evidence | retain | W0 | `D-18`, `D-25` |
| 3 | RL-013 | Engine runtime | retain | W0 | `D-17`, `D-20` |
| 4 | RL-014 | A2b & Suite D cardinality | retain | W0 | `D-15`, `D-16` |
| 5 | RL-015 | Ethics (H5) | retain | W0 | `D-19`, `V2-D16` |
| 6 | RL-016 | D-23/D-24/D-26 | retain | W0 | judge/timestamp/licence rules |
| 9 | RL-002 | Scaffold & `.gitignore` | retain | W0 | v1 layout |
| 10 | RL-003 | `pyproject.toml` / lock / env | retain | W0 | `I12` model-free engine |
| 11 | RL-004 | `compose.yaml` pgvector+btree_gist | retain | W0 | reused by every wave |
| 12 | RL-005 | CI workflow | **update** | W0 | file renamed; `MA-017` #60 + `MA-018` #78 add checks |
| 13 | RL-006 | ruff / pyright / import guard | retain | W0 | model for `MA-015` tier guard |
| 14 | RL-011 | Pilot protocol | retain | W4 | reused; `V2-D30` adds τ freeze |
| 15 | RL-020 | LongMemEval acquire/verify | retain | W0 | `D-23` anomaly rule |
| 16 | RL-021 | LoCoMo acquire/verify | retain | W0 | descriptive only |
| 17 | RL-022 | Suites G & I | **extend** | W4 | + `own_P` `C-011`; suite I extended by #62 |
| 18 | RL-023 | Suite D | **extend** | W3 | 7 → 11 channels under `E-037` #73 |
| 19 | RL-024 | Suite T | retain | W0 | instrument for `S1` (frontier framing) |
| 20 | RL-030 | Harness + adapter | retain | W0 | every profile runs through it |
| 21 | RL-025 | Benchmark adapters | retain | W0 | unchanged |
| 22 | RL-026 | Split freeze | retain | W4 | discipline inherited by `own_P`/`own_U` |
| 23 | RL-031 | Baselines B0–B4 | retain | W0 | `X-*` ablations are additions |
| 24 | RL-032 | Extraction replay cache | retain | W0 | basis for `METHOD` versioning |
| 25 | RL-040 | 12-table DDL | **extend** | W1 | preserved as v1.0.0; M1–M9 additive |
| 30 | RL-033 | Judge validation | retain | W4 | + `SD-001` scoring |
| 31 | RL-034 | Statistics module | retain | W4 | CP reused for admission (`STATISTICS.md` §4.2) |
| 32 | RL-041 | Migration runner | retain | W1 | extend runner for M1–M9 (`MA-016`) |
| 33 | RL-042 | Supersession & archival | retain | W1 | R1–R8 + `(S)∨(C)` kept; generalised by `V2-D10`/`V2-D29` |
| 34 | RL-043 | Temporal predicates | retain | W1 | survive the M3 rename |
| 35 | RL-044 | Clock & queue | retain | W1 | unaffected by V2 axes |
| 42 | RL-050 | Hybrid retrieval | retain | W1 | RRF survives as one fusion operator |
| 43 | RL-051 | Renderer | **extend** | W3 | subset of four-channel assembly (`E-017`, `E-033`) |
| 44 | RL-060 | 5-check gate | **extend** | W1 | becomes `ground()`; `assess_soundness()` added |
| 45 | RL-061 | Consolidation | retain | W1 | compaction kept; abstraction is a new kind (`C-010`) |

### 3.3 The 2 open v1 issues — retained and re-scoped, **not closed**

| # | RL | Disposition | Milestone | Change |
|---|---|---|---|---|
| 46 | RL-070 | **update (re-scope)** | W3 | Engine closure kept. Remaining work re-homed to `E-037`/`E-038` (#73). `MAX_LINEAGE_DEPTH=3` recorded as a correctness bug for V2 to fix. `A8` toggle confirmed absent **on `main`**. *(r4.1: open PR #49 implements `A8`, the 7-channel scanner and the epoch guard. It is held open on a reproduced liveness defect; see §8.)* *(r4.2: PR #49 was fixed and **merged** as `394dcf2`, so `A8`, the scanner and the epoch guard are now on `main`. #46 stays open for `E-037`/`E-038`; see §9.)* |
| 47 | RL-071 | **update (expand)** | W1 | **Two critical defects added**: `/u/4` vs `/u/42` prefix leak, and the unscoped-`ENTITY` existence oracle. Design in #54 (`MA-011`), implementation in #62 (`E-011`). |

### 3.4 v1 tasks that never had an issue — now created or explicitly bucketed

| RL | Original intent | Status | V2 home |
|---|---|---|---|
| RL-001 | Approve architecture | done by owner decision, no issue | folded into `D-ARCH`; no issue needed |
| RL-072 | Audit/log redaction guard | **never created** | W3 under `E-037` (11 channels) |
| RL-090 | T1–T5 archival harness | drafted (`RL-090.md`) but **no issue** | W1 under `E-016`/`E-004`; T5 under `E-037` |
| RL-091 | Invariants 1–9 suite | **no issue, no directory** | W1 `E-004` (#52) |
| RL-092 | Clock/queue determinism | **no issue**; logic exists in 25 inline tests | W1 `E-016` (#67) |
| RL-093 | 8-worker + HNSW study | **no issue, no test file** | W1→W3 under `E-036`/`E-037` measurement |
| RL-094 | §G testing matrix gate | drafted (`RL-094.md`) but **no issue** | replaced by gates G0–G8 |
| RL-100–105 | Pilot & H1–H4 | drafted only | W6 `R-010` under #76 |
| RL-120–122 | PDT app | drafted only | W5 `UM-*` + W7 `A-011` |
| RL-130–132 | Thesis/release | drafted only | W8 #80 |

**Judgement recorded:** `RL-090`–`RL-094` were never issues because their content was
absorbed into invariants and gates. Creating four retroactive issues would add noise, so
the *work* is tracked under V2 tasks and the *absence* is recorded here for traceability.

---

## 4. V2 issue backlog created

**31 new issues**, made up of **24 individual task issues** (W0, W0b, W1) and **7 wave trackers**
(W2–W8). Together they cover all **88** roadmap tasks. Milestones W0–W8 created
(#9–#18 of the milestone sequence).

| Wave | Issues | Coverage |
|---|---|---|
| W0 | #50 `E-001`, #51 `E-002`, #77 `E-003`, #52 `E-004` | all 4 W0 tasks |
| W0b | #53–#60 (`MA-010`–`MA-017`), #78 (`MA-018`) | all 9 W0b tasks |
| W1 | #61–#71 (`E-010`–`E-020`) | all 11 W1 tasks |
| W2 | #72 tracker | 7 tasks (`E-021`–`E-027`) listed in the tracker |
| W3 | #73 tracker (+ #46) | 9 tasks (`E-030`–`E-038`) |
| W4 | #74 tracker (+ #14, #17, #22, #30, #31) | **16** tasks (`C-010`…`UM-003`) — *r4 said 17; corrected in r4.1, see §7* |
| W5 | #75 tracker | 13 tasks (`C-020`…`UM-015`) |
| W6 | #76 tracker | 6 tasks (`C-030`, `R-010`–`R-015`) |
| W7 | #79 tracker | 8 tasks (`A-010`–`A-017`) |
| W8 | #80 tracker | 5 tasks (`R-030`–`R-034`) |

**Why W2–W8 are trackers rather than 60 individual issues:** the roadmap gates are
per-wave, and creating 60 speculative issues before the wave opens would (a) invite the
same drift this audit is fixing, and (b) force re-scoping every wave as measurement
changes the plan. Each tracker carries the full task list as a checklist; tasks are
promoted to individual issues **when the wave opens**. This is an explicit trade-off, not
an omission — recorded in `TRACEABILITY_REPORT.md` §2.

---

## 5. V1 assets explicitly preserved

| Asset | Preserved as | Guarded by |
|---|---|---|
| v1 engine (12 tables, R1–R8, `(S)∨(C)`, predicates, clock, queue) | v1.0.0 baseline, tag by `E-002` | Tier-A regression wall: full CI-scope suite, 1395 collected / **1374 passed** / 21 skipped (§1.1). *r4 said "1252 tests"* |
| v1 milestone history P0–P7 | untouched | never deleted or renamed |
| 33 closed issues + their comments | untouched, disposition added as a comment | additive-only policy |
| Frozen research record | untouched | `md5sum -c` still passes |
| `evidence/*`, `CHANGELOG.md`, `REFACTORING.md` | untouched (historical) | `E-003` explicitly forbids rewriting |
| Suites `T/G/D/I`, harness, judge, stats, replay cache | reused by all V2 waves | §3.2 `retain` rows |

---

## 6. Contradictions resolved

| Contradiction | Resolution |
|---|---|
| `PROJECT_ROADMAP.md` §A vs `TASK_GRAPH.md` on P0 status | V2 notice added; `E-001` generates one answer |
| Issue #12 names a workflow file that does not exist | `update` disposition; correct file recorded |
| v1 `layer` enum vs V2 axes | `V2-D01`; `A10` persona ablation retired |
| v1 gate vs V2 two-policy admission | `V2-D02`; v1 gate becomes `ground()` |
| v1 archival vs V2 retention floors | `V2-D10` + `V2-D29` |
| v1 `MAX_LINEAGE_DEPTH=3` vs deletion completeness | `V2-D09`; depth reclassified as performance parameter |
| v1 7-channel deletion vs V2 11 channels | `E-037`; suite D extended, not replaced |

No contradiction required changing a frozen historical record.

---

## 7. r4.1 corrections (audit trail)

These corrections came from a review of the r4 deliverables. Every corrected value was
re-derived from the live tracker or repository. Earlier values are kept in place,
marked *r4 said …*, and are not silently overwritten.

| # | r4 statement | Verified value | Source of truth |
|---|---|---|---|
| 1 | W4 has "17 tasks" | **16** | `ROADMAP_V2.md` §7 task-table rows. The 17th id in the section text is `C-013`, a **retired alias** of `SD-001` and not a task. Tracker #74 lists exactly the 16 |
| 2 | Total tasks implicitly 89 (4+9+11+7+9+17+13+6+8+5) | **88** (4+9+11+7+9+16+13+6+8+5) | Same extraction. The "91 task IDs" in r3 `SYNC_LOG.md` counts unique ids, including the 3 retired aliases `C-013`→`SD-001`, `C-024`→`UM-010`…`UM-014`, `R-014`→`E-037` |
| 3 | "1252 passed" (§1, §5; `TRACEABILITY_REPORT.md` §4, §7) | 1252 is the **selected** count. Result: **1234 passed, 18 skipped, 143 deselected** | §1.1 |
| 4 | "1252-test suite is the Tier-A regression wall" (footer of issues #50–#80; #51 deliverable) | The wall is the **full CI-scope suite**: 1395 collected, 1374 passed, 21 skipped | §1.1. Issue bodies were edited with a visible *Correction (r4.1)* note, and GitHub keeps their edit history |
| 5 | `TRACEABILITY_REPORT.md` §7: "66 issues, 24 open" | **66 issues, 33 open, 33 closed** | `gh issue list --state all` (see breakdown below) |
| 6 | Issue footers: "Authoritative plan: `PLAN/V2/`" | V2 is **Proposed**, not approved, and `PLAN/V2/` is **not yet committed** (0 tracked files), so the link 404s on `main`. *(r4.2: V2 was approved on 2026-10-31 and `PLAN/V2/` was committed to `main`; the footers were updated again, see §9)* | `git ls-files custom-memory-engine/PLAN/V2` → 0. Footers now say "Proposed plan" |
| 7 | Direction notices in `README.md`, `PROJECT_ROADMAP.md`, `DECISIONS_REGISTER.md` read as already in force ("authoritative", "superseded", "wins") | Reworded to make supersession **conditional on owner approval**. `DECISIONS_REGISTER.md` notice range corrected from `V2-D01`–`V2-D17` to `V2-D01`–`V2-D34` | `README.md` "Status" section |
| 8 | r4 inspected issues only, not PRs | Open PR #49 implements part of #46 | §8 |

### 7.1 Open-issue breakdown (live, 2026-10-31)

| Group | Count | Issues |
|---|---:|---|
| Individual V2 task issues (W0, W0b, W1) | **24** | #50–#71, #77, #78 |
| V2 wave trackers (W2–W8) | **7** | #72–#76, #79, #80 |
| Retained, re-scoped v1 issues | **2** | #46 (W3), #47 (W1) |
| **Open total** | **33** | |
| Closed v1 issues | 33 | §3.2 |
| **All issues** | **66** | 35 v1 + 31 V2 |

---

## 8. Open pull requests (r4.1)

`gh pr list --state all`: 14 PRs in total. 13 are merged and 0 were closed without
merging. **1 is open.**

| PR | Purpose | CI | Decision | Reason |
|---|---|---|---|---|
| #49 `rl/070-deletion-security` (author `Ahmed-LS22`, 4 commits, +1226/−81) | `RL-070` remainder: `deletion/` package (closure, re-derivation, 7-channel scanner), `A8` ablation, epoch-keyed cache invalidation, stale-worker epoch guard | All 6 checks green; mergeable, no conflicts (merge-base = `main`) | **Left open** (r4.1) → **merged** in r4.2 as `394dcf2` after fixes in `d087eb7` (§9) | **Blocking defect, reproduced.** A job that captured a deletion epoch is refused without being consumed, and stays `queued`/`attempts=0`. The worker's `ORDER BY next_run_at,id LIMIT 1` selects it again on every pass, so all later jobs for that user starve. The epoch is per-user, so deleting *any* memory triggers this. Reproducer: 5 worker passes, 0 handler runs, both jobs still queued. The PR's own test asserts the stuck state. Fix and reproducer are posted on the PR as a review |

**Local verification of #49 at head `6e7a88a`** (CI-equivalent install, disposable DB): ruff
clean; pyright 0 errors; `pytest -q` → **1395 passed, 21 skipped** (1416 collected). The bot's
earlier review comment (imports inside functions, an unreachable `return`) is resolved in
`081b8cd`. The PR does **not** duplicate merged work. It refactors `engine/deletion.py` into a
`deletion/` package and adds the features that are absent on `main`. It is **compatible
with V2**: `E-037` (#73) extends it, and keeping `MAX_LINEAGE_DEPTH = 3` matches the v1 spec.
Merging it would not change the W0/W0b/W1 readiness verdict.

---

## 9. r4.2: approval, PR #49 merge, publication

**Approval.** On 2026-10-31 the Project Owner accepted the r4.1 audit and approved V2 as
the **canonical implementation plan** ([`APPROVAL.md`](APPROVAL.md)). Roadmap implementation
was explicitly **not** authorised; that will come in a separate instruction.

**PR #49, fixed and merged.** The r4.1 hold was resolved by fixing the defect, not by
waiving it. Re-reviewing the full diff surfaced two further defects, both introduced by
the same PR. Every defect was reproduced before it was fixed, and each has a regression
test that fails on the previous head:

| # | Defect | Fix (`d087eb7`) |
|---|---|---|
| 1 | Stale-epoch job stays `queued` forever and starves the user's queue (r4.1 finding) | Dead-letter it in the same transaction, without charging an attempt; write a content-free `job_discarded_stale_deletion_epoch` audit event; return a `WorkerResult` so the next call reaches the next job |
| 2 | Job purge used `payload::text ILIKE '%<id>%'`. Version ids are caller-supplied, so deleting `v1` purged surviving work that referenced `v10` (reproduced: 5 purged, expected 3) | Exact JSON string-value match with `jsonb_path_exists('$.** ? (@ == $id)')`. The contract is documented in `queue/README.md` |
| 3 | A summary rebuild dropped the edge from a memory derived *from* that summary. A later deletion of the summary's last source then left the derived memory behind (a deletion leak) | Rebuild summaries first, then restore the other survivors' edges |

- **Verification on `d087eb7`:** all 6 PR checks green.
- **Merged:** commit `394dcf2`, 2026-10-31T14:36:00Z.
- **Post-merge `main`:** CI green.

**Test figures on `main` after the merge** (same commands as §1.1; the `998558f` rows
there remain the historical baseline at audit time):

| Scope | `998558f` (r4.1) | `394dcf2` (r4.2) |
|---|---|---|
| Full suite, `--extra baselines` | 1395 collected; 1374 passed, 21 skipped | 1423 collected; **1402 passed, 21 skipped** |
| `-m 'not integration'` | 1234 passed, 18 skipped, 143 deselected | 1248 passed, 18 skipped, 157 deselected |
| Full suite, no extras | 1356 passed, 23 skipped | 1384 passed, 23 skipped |

**Effect on §1 findings.** These items are now on `main`:
- the `A8` toggle;
- the 7-channel scanner;
- the stale-worker epoch guard;
- epoch-keyed cache invalidation.

These remain open, and are unchanged:
- `security/` package and restricted `app_role` (#47);
- `MAX_LINEAGE_DEPTH = 3` as a correctness bound (`E-037`);
- 11 deletion channels (`E-037`);
- P9 verification directories (`E-004`, `E-016`).

**#46** stays open, as recorded in §3.3. Its v1 engine scope is now delivered, and it
remains the anchor for `E-037`/`E-038` (#73).

**Publication.** `PLAN/V2/`, the direction notices, and `scripts/check_statistics.py` are
committed to `main` in r4.2. The script is the runnable validation for `STATISTICS.md` §10.
**Not** done, because it belongs to W4 task `SD-007`: wiring it into CI and asserting it
against `cp_bounds.output.txt`.

**Readiness is unchanged.**
- W0: ready once implementation is authorised.
- W0b: ready after G0.
- W1: not ready, because of the `MA-011` fail-first test, the `MA-016` M3 specification,
  and #47's `app_role`.
