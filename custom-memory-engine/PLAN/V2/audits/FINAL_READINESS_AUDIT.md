# Final Readiness Audit

> **Superseded status (r7, 2026-10-31).** This r6 document is preserved as written. Every
> blocker it lists is now resolved; see `R7_RESOLUTION_REPORT.md` and `DECISIONS_V2.md` §5.


**Status:** Audit deliverable. Independent review of the corrected plan.
**Audit:** r6 — Plan Corrections & Final Readiness Audit, 2026-10-31 (originally mis-dated 2026-11-01; corrected in r7).
**Baseline:** `main` @ `490904d` at audit start; the corrections in this audit are uncommitted
working-tree changes.
**Posture:** written as a reviewer who did not write the corrections, and who treats the
corrections themselves as claims requiring evidence.

---

## 1. The ten review questions

### 1. Every finding from the previous audit has an explicit disposition — **PASS**

Eleven findings were re-derived independently and each has a disposition in
`PLAN_CORRECTION_REGISTER.md`:

- **Corrected:** A1, A2, A4, A5.
- **Withdrawn after investigation:** A3 (rule out of scope), C1 (formatting artefact).
- **Requires approval:** B-01 … B-09.

Nothing is silently dropped. A3 and C1 are withdrawn *with reasons*, which is the part that
matters: withdrawing a finding without evidence would be the same failure as accepting one
without evidence.

### 2. Every confirmed error is corrected or clearly marked as requiring approval — **PASS**

The four corrections (A1, A2, A4, A5) were applied only where an approved normative statement
already fixed the intended value: §4.2's own stated convention, its column heading, §2.1's
declared table name, and the frozen benchmark file. The nine blockers are marked, not fixed.

### 3. Every unresolved decision has an owner or an explicit approval requirement — **PASS**

`OPEN_DECISIONS_AND_BLOCKERS.md` gives each `B-*` a required owner (Project Owner) and a
"latest decision point". Pre-existing open decisions keep the owners already assigned in
`DECISIONS_V2.md` §4 (`V2-D13` engine lead, `V2-D16` supervisor, `V2-D30`/`D31`/`D34`
methods, `V2-D14`/`D32` product + methods).

### 4. Every blocker is connected to the affected task and issue — **PASS**

Each `B-*` names the issues and tasks it blocks: B-01 → `#54`/`#62`/G0b/G1; B-02 →
`#62`/`#65`; B-03 → `#56`/`#68`; B-04 → `#55`/`#58`; B-05 → `#61`/`#71`; B-06 →
`#66`/`C-021`; B-07 → `#64`/`#66`; B-08 → W1 entry, `#60`/`#65`; B-09 → W6.

### 5. No impossible dependency or premature start condition — **PASS, with one caveat**

The live issue graph is **acyclic** (verified by DFS over the reformatted bodies, and by a
topological sort producing a valid start order). No task depends on a later wave. However
**G0b as written is currently unsatisfiable** — see `B-08`: it requires `MA-017` green while
`MA-017`'s own specification says it fails on `M5`/`M7`, which have no owner. That is a gate
defect, not a dependency defect, and it is why W0b is *not* ready.

### 6. The 33 issue specifications agree with the authoritative plan — **PASS**

Verified mechanically: 0 undefined task ids, 0 dangling `#NNN` references, 0 residual claims
of superseded facts, section structure intact after the reformat. Two intentional residual
mismatches remain and are documented (the blockers B-01/B-03/B-04, which the issues correctly
carry as open questions rather than as settled fact).

### 7. Security and tenant isolation requirements are not weakened — **PASS, and strengthened**

Nothing was relaxed. Critically, A5's temptation was refused: relabelling the G3 gate to a P95
would have widened the tolerance from ≈54 ms to ≈59 ms, and the gate was left at the stricter
value. B-01 is a **defect in the approved design that this audit reproduced on a live
PostgreSQL 16 instance**, and it is recorded rather than papered over. The withdrawal of A3
did not remove any security requirement, because A3 was never one.

### 8. Statistical definitions, confidence levels and numerical tables consistent — **PASS**

- The one-sided 97.5% convention is now stated once and applied everywhere.
- Every row of §4.2 verified against the computed bound; the single wrong cell corrected.
- The zero-counter threshold claim (`o ≥ 36` at 97.5%, `o ≥ 29` at 95%) verified by computing
  the smallest `o` satisfying `1 − CP_upper(0,o) ≥ 0.90` under both conventions.
- `scripts/check_statistics.py` now **enforces** the table, with a negative test proving it
  fails on the old value.
- The `r3` regression case (`s=5, k=0, u=45` → rejected) still reproduces.

### 9. Traceability is complete — **PASS**

Every correction traces from finding → authoritative source → disposition → affected task →
affected issue → verification evidence. That chain is the structure of
`PLAN_CORRECTION_REGISTER.md` §D and `SYNC_LOG.md` §11.

### 10. No roadmap implementation has started — **PASS**

Verified: `git status` shows only `PLAN/V2/` markdown and `scripts/check_statistics.py`
(planning/verification scope). No file under `src/`, `tests/`, `schema/` or `.github/` was
modified by this audit. No W0/W0b/W1 task was executed. The nine→eleven open PRs are the
Project Owner's own, and none was created, modified, reviewed or merged by this audit.

---

## 2. Validation commands and actual results

Run from `custom-memory-engine/` on the corrected working tree:

| # | Command | Result |
|---|---|---|
| 1 | `md5sum -c validation/document_hashes.txt` | **5/5 OK** — frozen research record intact |
| 2 | `python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md` | **ALL HARD CHECKS PASSED** (proposal/checklist/changelog unchanged) |
| 3 | `diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt` | **identical** |
| 4 | `uv run python scripts/check_statistics.py` | **exit 0**, `ALL STATISTICS ASSERTIONS PASSED` |
| 5 | `uv run python scripts/check_plan_refs.py PLAN/V2` *(PR #83's checker)* | **0 findings** |
| 6 | same checker over `PLAN/V2` **plus** the new `audits/*.md` | **0 findings** |
| 7 | Independent CP computation (scipy `beta.ppf`, binomial-tail search) | `CP_upper(0,29) = 0.119445` by both methods |
| 8 | PostgreSQL 16 reproduction of `LIKE` wildcard leakage | leak **reproduced**; `starts_with` correct |
| 9 | Dependency graph DFS + topological sort over 31 live issue bodies | **0 cycles**, valid order emitted |
| 10 | Live tracker enumeration | **66 issues (33 open, 33 closed); 11 open PRs** |

**Note on check 4 and CI.** `check_statistics.py` is **not wired into CI** — there is no
workflow step for it on `main`. Two open PRs (#83, #84) both edit
`.github/workflows/custom-memory-engine.yaml`, so adding a step here would create a
three-way conflict; that wiring is left to `SD-007`, whose scope it already is. Recorded as a
residual risk below rather than fixed.

---

## 3. Corrections re-verified independently

| Correction | How it was re-verified | Verdict |
|---|---|---|
| A1: `0.0981` → `0.1194` | Re-derived by two independent numerical methods and by the repo's own `cp_upper`; all agree | **correct** |
| A2: table assertion added | Ran the script; then reverted the cell to `0.0981` and confirmed two failures; restored and confirmed exit 0 | **works** |
| A4: `METHOD` naming | Confirmed §2.1 is the authoritative statement and that the two build-facing sites now match | **correct** |
| A5: P50/P95 | Read the frozen JSON; confirmed 26.99 / 29.41 / 29.84 | **correct** |
| C1: cycles eliminated | Re-ran DFS on the live bodies (not local copies) after the first attempt read stale files — see §5 | **correct** |

---

## 4. Residual risks

| Risk | Severity | Mitigation |
|---|---|---|
| **B-01 is a live tenant-isolation defect in the approved design.** `MA-011` will implement it unless the design changes | **Critical** | Fail-closed id rejection is specified as an interim; G0b is the decision point. Independent corroboration: PR #86 (`T-02`) reaches the same conclusion |
| **G0b cannot be declared** until `V2-D13` is answered and `M5`/`M7` get owners or `deferred` markers | **High** | Listed as B-08; requires the Project Owner |
| **`check_statistics.py` is not in CI**, so A1's class of error is only caught when someone runs it locally | Medium | Scope of `SD-007`; flagged, not silently fixed, because of the workflow-file conflict with PRs #83/#84 |
| **`archived` → `retention` is unimplementable** as written (B-05) | Medium | A one-line fix is available once the Project Owner picks the mapping |
| Corrections are **uncommitted**; issue bodies (already on GitHub) now cite documents on `main` that still contain the old values | Medium | A docs PR is required to publish them. This is the same sequencing gap noted in `SYNC_LOG.md` §9, and it is why the `audit` and `corrections` must be committed before anyone relies on the issue bodies |
| Three issue bodies were edited while PRs #89/#91/#92 are open on related documents | Low | Banners state what changed and defer to the reviewer; no PR was rejected or approved |

---

## 5. Errors made and corrected during this audit

Recorded because an audit report that shows only successes is not credible.

1. **A false "31 cycles" alarm.** A naive parse flagged 31 dependency cycles. Investigation
   showed they were an artefact of the r5 issue formatting (consumer lists inside
   `## Dependencies`). The finding was withdrawn and the formatting fixed.
2. **A premature "cycles eliminated" claim.** After reformatting, the re-check still reported
   cycles — because the verification script had re-read **stale local copies** of 10 issue
   bodies instead of re-fetching from GitHub. The check was corrected (16 bodies *were*
   edited correctly; only the verification was wrong), re-run against freshly fetched bodies,
   and then confirmed acyclic. The claim in §2 check 9 rests on the post-fix run.
3. **A withdrawn A3.** The earlier report's "maintainer name in `APPROVAL.md`" finding was
   investigated and found to be outside the rule's scope. It was not repeated just because it
   had appeared before.

---

## 6. Does the approved plan still hold?

**The approval stands.** No approved architectural decision, research objective, project
scope, gate threshold or acceptance criterion was changed. A1/A2/A4/A5 restore values that the
approved documents already specified elsewhere; they are controlled documentation
corrections, and the original approval history in `APPROVAL.md` is preserved untouched.

**An approval amendment is required before W0b, but not before W0.** Specifically:

- **B-01** changes an approved normative rule (§5 rule 4). If the Project Owner adopts the
  `starts_with` fix, it must be recorded as a decision — it is a security correction, so it
  can be approved quickly, but it cannot be made by an audit.
- **B-02, B-03, B-04, B-05, B-06, B-07, B-08, B-09** each resolve an ambiguity or a
  contradiction in approved text and need a `V2-Dnn` record.
- **B-08 alone blocks G0b.**

So: **the plan is internally more consistent, technically more accurate, and still not
unblocked.** It needs one group of decisions, not a rewrite.

---

## 7. Is W0 ready to start?

**Yes, unchanged.** W0 (#50, #51, #52, #77) has no entry gate beyond Project Owner
authorisation, depends on no open decision, and its tasks are unaffected by every correction
and every blocker. A5 slightly *improves* W0 by giving `E-002` the correct baseline
definition.

**W0b is not ready.** G0b requires `MA-011` frozen, `MA-012`/`MA-013` frozen, `MA-017`/`MA-018`
green, and every review item decided. B-01, B-02, B-03 and B-08 each prevent one of those.
W1 inherits this through G0b.

**This audit does not authorise starting W0.** `APPROVAL.md` requires a separate instruction,
and this report is not that instruction.

---

## 8. Confirmation

- **No roadmap implementation occurred.** No W0, W0b, W1 or later task was executed.
- **No engine code, test, schema, migration or workflow was modified.** The only non-markdown
  change is a validation script (`scripts/check_statistics.py`), which adds a documentation
  check and computes nothing the frozen v1 asset does not already compute.
- **No issue was closed, recreated, retitled, relabelled or re-milestoned.** 33 open / 33
  closed, unchanged.
- **No PR was merged, closed, approved, rejected, or had its code touched.** All 11 open PRs
  remain open and unmodified.
- **No decision was taken on the Project Owner's behalf.** Nine blockers remain open by
  design.
- **The frozen research record is unchanged** (`md5sum -c` → 5/5 OK).

**Awaiting explicit Project Owner authorisation before any implementation.**
