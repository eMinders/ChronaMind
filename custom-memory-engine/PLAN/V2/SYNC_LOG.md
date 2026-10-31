# r1 → r2 Audit: Consistency Review and Change Log

**Purpose.** Record what was actually checked in the r2 review, what broke, what was
changed, and **what is still not fully resolved**. This is the document that makes the
plan auditable against itself; `MA-017` and `MA-018` mechanise the parts of it that can
be mechanised.

**Method.** Every claim below was checked by reading the documents and by scripting
extractions over them (task IDs, gates, decision IDs, migrations, tables, invariants).
Where a defect is claimed, the evidence is a quoted line or a cross-reference, not an
impression.

---

## 1. The five review concerns, and what happened to each

| # | Concern | Verdict | Where resolved |
|---|---|---|---|
| 1 | PDT / evolving user model must be core, not a cuttable demo | **Concern valid; r1 was wrong.** r1 placed user-model inference at de-scope step 4 and had no research family for it, so the plan could have succeeded at its subgoals and failed its purpose. | `USER_MODEL.md` (new), `V2-D26`, `V2-D33`, F4 + `S14`–`S19`, G4b/G5b, new `UM-*` tasks, corrected ladder |
| 2a | Mandatory non-empty counter-support | **Concern valid; error is a conflation, not the rule's intent.** Fixed with a three-state ledger rather than by deleting the rule. | `STATISTICS.md` §2, `V2-D18` |
| 2b | ECE as an admission requirement | **Concern valid; r1 was wrong.** Batch statistic, circular, gameable, undefined at n ≈ 12. | `STATISTICS.md` §4, `V2-D19` |
| 2c | Conformal methods | **Concern valid but the recommended direction needed correcting.** Conformal is valid only in blocked/adaptive form and is the wrong tool for per-hypothesis admission. | `STATISTICS.md` §5, `V2-D23` |
| 2d | Sparse / user-specific / temporally correlated data | **Not previously addressed at all.** Hierarchy + temporal blocking + exact bounds + always-valid inference. | `STATISTICS.md` §5.2–§5.3, `V2-D20`–`V2-D22` |
| 2e | Counterevidence vs absence of observed counterevidence | **Concern valid; this is the deepest issue in the review.** Operationalised as opportunity/undetermined counts, `coverage`, refutation attempts, and a negative-control slice. | `STATISTICS.md` §2–§3, `V2-D21` |
| 3 | Architecture consistency audit | **Valid; 9 defects found, 2 critical (cross-tenant).** | §2 below |
| 4 | Plan completeness cross-check | **Valid; 4 orphans and 1 ordering error found.** | §3 below |
| 5 | Research must measure personalization utility, not only abstraction quality | **Concern valid; r1 measured abstraction precision and stopped.** F4 with `UM1`–`UM10` added, including the intervention-cost frontier. | `USER_MODEL.md` §5, `RESEARCH_PROGRAM_V2.md` §1–§2 |

---

## 2. Architecture defects found (all fixed)

| ID | Defect | Evidence | Severity | Fix |
|---|---|---|---|---|
| A1 | Scope prefix match is ambiguous: `/u/4/` matches `/u/42/…` | r1 §5: ``LIKE 'S%'`` with no separator rule | **Critical** (cross-tenant read) | `V2-D27`, §5 trailing separator + `path_with_prefix` |
| A2 | `ENTITY` unscoped — existence/attribute oracle across tenants | r1 §2.1 called entities "canonical referents" with no principal scope | **Critical** (information leak) | `V2-D25`, §5.2 |
| A3 | Tier-B contract self-contradictory: "cannot change lifecycle" while `infer` creates lifecycle | r1 §8 | High | §8.1 capability classes; `adjudicate` and `delete` removed from B |
| A4 | No channel for `contested` content | r1 §9 listed three channels | High | `V2-D28`, §9.1 four channels |
| A5 | `stale` as a stored `verification_status` can disagree with the graph | r1 §7 field list | High | `V2-D24`, §9.2 derived staleness |
| A6 | `verified` abstraction could survive deletion of its sources (depth bound + no reliance check) | r1 §10 | High | §10 ordering rule: `verified ∧ deleted-source` unreachable |
| A7 | Provenance rendering could widen authorization across scopes | r1 §9 "provenance bundle always returned", no visibility rule | High | §9.3 opaque provenance |
| A8 | "Six artifacts" mixed memories and structure | r1 §2.1 | Low | §2.1 four kinds + four structural tables |
| A9 | Retention orthogonal to answerability, but `working` expiry would silently drop claims | r1 §1 axis table | Medium | `V2-D29` retention floors |
| A10 | `METHOD` vs `INFERENCE_METHOD` naming split | r1 §2.1 vs §7 | Low | §2.1 single name, field `method_id` |
| A11 | Duplicate `confidence` row in the abstraction field table | r1 §7 | Low | §7 rewritten |

---

## 3. Plan-completeness defects found (all fixed)

| ID | Defect | Fix |
|---|---|---|
| P1 | `E-037`/deletion-through-cognition scheduled in W6, **after** cognition exists in W5 → reflection runtime would be built on an undeletable substrate and rebuilt | Moved to W3 as `E-037`; `E-038` added for derived staleness (#13 in the dependency-change table) |
| P2 | Statistics corrections (`STATISTICS.md`) had no tasks | `SD-001`–`SD-005` in W4; `C-021` depends on them |
| P3 | User model had no instrument and no tasks | `UM-001`–`UM-003` (W4), `UM-010`–`UM-015` (W5), `R-015` (W6) |
| P4 | Cross-tenant and API-surface defects had no home | New wave **W0b** (`MA-010`–`MA-018`) |
| P5 | Architecture elements with **no owning task**: invariants `I13`–`I17`, the four-channel contract, retention floors, entity opacity | `E-017` (channels), `E-020` (facets/floors), `E-038` (staleness), `UM-012`/`UM-015` (dissent), `MA-017` (mechanical coverage check) |
| P6 | r1's `C-013` metrics module was referenced by `C-021` but never scheduled before it | Renamed `SD-001`, explicitly in W4 |

**Architecture → plan coverage matrix** (the artifact `MA-017` will enforce):

| Architecture element | Task |
|---|---|
| Four kinds + lifecycle + retention axes (M1) | `E-010`, `E-016` |
| Scope hierarchy + RLS (M2/M9) | `E-011`, `MA-011` |
| Three clocks (M3) | `E-012` |
| Generalised `SOURCE` (M4) | `E-013` |
| `ASSOCIATION`, `ENTITY`, `METHOD`, `ABSTRACTION_STATS`, `VERIFICATION`, `NEGATIVE_CONSTRAINT` (M6) | `E-014` |
| `um_facet` + retention floors (M8) | `E-020`, `E-018` |
| Scope path separator + `path_with_prefix` (M9) | `E-011`, `MA-011` |
| Two admission policies | `E-015` |
| Four channels | `E-017`, `E-033`, `E-035` |
| Public API surface + capability classes | `MA-012`, `MA-015`, `A-013`, `A-017` |
| Staged retrieval + refine | `E-031`–`E-034`, `E-036` |
| Deletion closure + ordering + staleness | `E-037`, `E-038` |
| Invariants `I1′`–`I12` | `E-004` reachability test + per-wave tests |
| Invariants `I13`–`I17` | `E-037`, `E-038`, `E-017`, `UM-012`, `MA-011` |
| Statistics (ledger, bounds, hierarchy, conformal, always-valid) | `SD-001`–`SD-005` |
| User model (facets, trust ladder, dissent, decay, deny-list) | `UM-010`–`UM-015` |
| Profiles + generality | `E-021`–`E-027`, `A-010` |

---

## 4. What remains unresolved (deliberately)

These are open *by design*, not overlooked.

| # | Open item | Why it is open | Tracked as |
|---|---|---|---|
| 1 | `τ_confidence`, `τ_coverage` numeric defaults | Must be frozen from pilot data, as v1 froze `D-09`–`D-14` | `V2-D30` |
| 2 | Population `μ`/`κ` window (all users vs rolling) | Needs measurement of population drift; wrong choice biases cold start | `V2-D31` |
| 3 | Abstraction expiry → auto-re-derive vs prompt | Privacy posture differs between profiles | `V2-D32` |
| 4 | `K` dissent-persistence window numeric confirmation | Default 7 cycles; confirm on pilot | `V2-D34` |
| 5 | Whether `S8` (labelling detectability) runs | Ethics determination | `V2-D16` |
| 6 | Association-type cap | Needs measurement of graph growth | `V2-D13` |
| 7 | Whether hierarchy is exercised by real (not simulated) users | `own_U` is simulated; construct validity is unproven without `S8`/study | declared limitation |
| 8 | Write throughput under graph + association growth | Not measured; v1 numbers are retrieval-only | `E-036` measurement, `R-013` |

---

## 5. Residual risks I judge material

| Risk | Why it matters | Mitigation in plan | Residual |
|---|---|---|---|
| **`S14` fails** — recovering preferences does not improve decisions | The project's stated purpose is unachieved | Declared in advance as a legitimate outcome; F4 reported as negative | High — cannot be mitigated, only reported |
| **Simulated users are not real users** | `own_U` may encode a decision policy that is easier than reality | `UM-001` solvability check; optional human study | Medium |
| **Verification cost dominates** | Abstractions are cheap to generate, expensive to verify; cost could make the layer uneconomic at small scale | `C-027` cost accounting; G6 frontier; expiry | Medium |
| **Hierarchical pooling leaks population priors into individual output** | A facet prior derived from other users appears in a single user's confidence | Priors are facet-level only (`V2-D22`); never per-item; documented as a privacy boundary | Low–Medium |
| **Channel separation is enforced but callers ignore it** | An agent could paste both channels into one prompt | Engine-side is airtight (`S7`); downstream composition is out of scope, documented | Medium |
| **Deletion closure cost grows with cognition** | Unbounded correctness closure + staleness hashing could be slow | Closure-size histogram; async closure with completion barrier; never a silent cap | Medium |

---

## 6. Documents changed in r2

| Document | Change |
|---|---|
| `README.md` | Rewritten: r2 revision notice, 9-document reading order incl. `STATISTICS.md`, `USER_MODEL.md`, `SYNC_LOG.md`; corrected "what is dropped" list |
| `CRITIQUE.md` | Added §A1–A10 defect table for r1's own errors |
| `CHARTER.md` | One-sentence identity now includes the user model; tier table distinguishing cuttable surfaces from uncuttable capabilities; P2/P4 restated for opportunity accounting; P11 (instruments first), P12 (user model is a view); non-goals + behavioural-simulator line |
| `ARCHITECTURE_V2.md` | Axes: `facet` tag + retention floors; four kinds + four structural tables; §2.2 admission flow updated to ledger + CP bound; §2.3 two explicit transition rules; §5 rewritten (trailing separator, selector semantics, RLS defence-in-depth, GUC limit, entity opacity); §7 field table rewritten; §8 rewritten as the full API surface with capability classes; §9 four channels + derived staleness + provenance opacity; §10 deletion ordering + 11 channels; invariants `I13`–`I17`; migrations `M8`, `M9` |
| `STATISTICS.md` | **New.** Three-state ledger, refutation procedure, CP-bound admission, ECE demotion, blocked/adaptive conformal, hierarchical pooling, always-valid looks, held-out verification requirements, and a §8 table of my own r1 errors |
| `USER_MODEL.md` | **New.** Definition and non-goals, seven facets as tags, lifecycle (strengthen/weaken/revise/contest), sticky dissent, trust ladder, `own_U` instrument, `UM1`–`UM10`, safety table, `S14`–`S19`, de-scope protection |
| `ROADMAP_V2.md` | Rewritten: W0b added, `E-037`/`E-038` moved to W3, `SD-*`/`UM-*`/`MA-*` task families, G4b and G5b, corrected de-scope ladder (capabilities uncuttable), corrected dependency graph with a table of what moved and why |
| `RESEARCH_PROGRAM_V2.md` | Rewritten: F4 added, `S10` re-stated, `S12` re-specified as bound reliability, `S8` reinstated in narrow form, 8 methodological corrections table, 9-row falsification table, expanded evaluation inventory |
| `DECISIONS_V2.md` | §2 added (`V2-D18`–`V2-D29`, `V2-D33`), §3 carried-forward table, §4 open decisions incl. `V2-D30`–`V2-D32`, `V2-D34`; `V2-D15` resolved |
| `SYNC_LOG.md` | **New.** This document |

**Not changed:** `Graduation_Project_Proposal.md`,
`Methodology_and_Consistency_Checklist.md`, `TECHNICAL_SPECIFICATION.md`,
`CHANGELOG.md`, `reference/Source_Evidence_Ledger.md`,
`reference/Research_and_Fact_Check_Audit.md`, and every `evidence/*` record.
`md5sum -c validation/document_hashes.txt` remains the check, and it passes.

**Outside `PLAN/V2/`:** direction notices only, in `README.md`, `PROJECT_ROADMAP.md`,
`PLAN/DECISIONS_REGISTER.md`. No task, timeline, or baseline content was rewritten.

---

## 7. r3 — final statistical correction pass

**Trigger.** A focused review of r2 raised five points. Each was checked numerically
before any edit (`scripts/check_statistics.py`, run output reproduced in `STATISTICS.md`
§10). Two were confirmed as **critical r2 defects**; three were confirmed as
documentation inconsistencies; **none was rejected as technically incorrect**, but one
recommendation was reshaped (see item 3).

### 7.1 Issues verified and corrected

| # | Claim | Verification | Verdict | Correction |
|---|---|---|---|---|
| 1 | `undetermined` is implicitly treated as success | `s=5, k=0, u=45`: r2 bound gives `confidence_lower = 0.9289` → **admitted at τ=0.90**; counting `u` as failure gives `0.0333` | **CONFIRMED, critical** | Partial identification: `confidence_lower = 1 − CP_upper(k+u, o)`; `coverage` promoted from report to gate. `V2-D18`/`V2-D19` amended; `STATISTICS.md` §2.2 rewritten with an explicit estimand and missing-data statement |
| 2 | Clopper–Pearson examples use mixed conventions | r2 quoted `≈0.32` for `n=8`, which is the **one-sided 95%** value `0.3123`; the system convention gives **`0.3694`**. Zero-counter threshold is 29 under one-sided 95% vs 36 under one-sided 97.5% | **CONFIRMED** | One convention fixed repo-wide: **one-sided 97.5%** (identical to v1 deletion). Numerical table replaces prose approximations; `SD-007` asserts agreement with `validation/cp_bounds.output.txt` to `1e-9` |
| 3 | Statistical validity under temporal correlation / repeated looks / pooling | CP assumes exchangeability; positively autocorrelated data make it **anti-conservative**. Design effect at `n=40`, `ρ=0.2`, cluster 8 gives `n_eff=17` and bound `0.195` instead of `0.088` | **CONFIRMED** — but the recommendation to "use exact methods only" is **not adoptable**: no exact finite-sample bound exists under arbitrary dependence without strong assumptions. Reshaped into a *guarantee table* with mandatory qualifiers + `SD-008` (design effect / block bootstrap, most-conservative rule) | `STATISTICS.md` §5.4 (units, guarantee table, composition of error controls, honest limit). The word "exact" is now reserved for the exchangeable case |
| 4a | Facet definitions inconsistent (`inferred_preference`) | Present as a facet in `USER_MODEL.md`, absent in `ARCHITECTURE_V2.md` | **CONFIRMED** | Facet vocabulary reduced to six, canonical in `USER_MODEL.md` §2.1. `inferred_preference` removed because it duplicates the lifecycle axis and permits the contradictory state `facet=inferred_preference ∧ lifecycle=asserted`, which `I1′` forbids |
| 4b | `UM-015` status inconsistent | Named in `V2-D33` and `S18`, absent from the r2 never-cut list | **CONFIRMED** | Added to the never-cut list; `R-015` vs `UM-015` disambiguated in the ladder |
| 5 | `V2-D18`–`D20` may conflict with `STATISTICS.md` / `ARCHITECTURE_V2.md` | Grep for the superseded form `CP_upper(k, o)` found live uses in `DECISIONS_V2.md`, `ARCHITECTURE_V2.md` §7, and `ROADMAP_V2.md` `SD-002` | **CONFIRMED** | All normative uses updated; remaining occurrences are explicitly labelled as the *superseded r2 rule* (regression test / ablation control) |

### 7.2 What was rejected or reshaped

- **"Use exact methods only" under temporal dependence** — rejected as impossible. No
  exact finite-sample bound exists for arbitrary dependence without an assumption. The
  plan states assumptions and uses the most conservative available correction instead.
- **`S12` re-specified** so that it is falsifiable: the realized failure rate must not
  exceed `1 − confidence_lower` on held-out data, with `X-ignore-undetermined` (`SD-006`)
  as the positive control. A claim that cannot be violated is not a hypothesis.

### 7.3 Documents modified in r3

`STATISTICS.md` (§2.2, §4.2, §5.4 new, §7, §8, §10 new),
`ARCHITECTURE_V2.md` (§7 confidence/coverage fields, promotion rule, facet pointer),
`DECISIONS_V2.md` (`V2-D18`, `V2-D19`), `ROADMAP_V2.md` (`SD-002` rewritten,
`SD-006`–`SD-008` added, G4 strengthened, `UM-015` protected, ladder note),
`RESEARCH_PROGRAM_V2.md` (`S12`, corrections table rows 9–11, inventory),
`USER_MODEL.md` (§2.1 canonical facets),
`SYNC_LOG.md` (this section),
`scripts/check_statistics.py` (new validation script).

**Unchanged:** `CRITIQUE.md`, `CHARTER.md` (its statistical statements were already
qualitative and remain correct).

---

## 8. r4 / r4.1: backlog transition and reporting corrections

**r4** created the V2 tracker: 10 milestones, 31 issues (#50–#80), and dispositions for all
35 v1 issues. Its reports are [`TRANSITION_AUDIT.md`](TRANSITION_AUDIT.md) and
[`TRACEABILITY_REPORT.md`](TRACEABILITY_REPORT.md).

**r4.1** was a review of the r4 deliverables. Every figure was re-derived from the live
tracker and from test runs at `main` @ `998558f`.

| Defect in r4 | Resolution |
|---|---|
| W4 reported as 17 tasks in `TRANSITION_AUDIT.md` §4 | 16. `C-013` is a retired alias of `SD-001`. The total of 88 holds |
| "1252 passed" | 1252 is the number of tests *selected* by `-m 'not integration'`; the run gives 1234 passed, 18 skipped. The full CI scope gives 1374 passed, 21 skipped, 1395 collected. Each figure is tied to its command in `TRANSITION_AUDIT.md` §1.1 |
| Issue footers called the 1252 tests "the regression wall" and `PLAN/V2/` "authoritative" | Footers of #50–#80 and the deliverable in #51 were edited, each with a visible *Correction (r4.1)* note |
| "66 issues, 24 open" | 33 open = 24 task issues + 7 trackers + 2 retained v1 issues |
| Direction notices read as already in force; the index says Proposed | Notices made conditional on approval. `DECISIONS_V2.md` "ADOPTED" scoped to the proposal. `V2-D01`–`V2-D17` corrected to `V2-D34` |
| Open PRs not reviewed | PR #49 reviewed and **held open**: reproduced queue starvation on a stale deletion epoch |

**Still open:** `PLAN/V2/` and the r3/r4 edits are **not committed** to `main`, so links from
issues to `blob/main/.../PLAN/V2/` will 404 until they are. Committing them is the first
action of `E-003` (#77), or it can be a standalone docs PR. V2 approval is still pending.

---

## 9. r4.2: approval, PR #49, publication

| Item | Result |
|---|---|
| V2 status | **Approved / Canonical Implementation Plan**: Project Owner, 2026-10-31 ([`APPROVAL.md`](APPROVAL.md)). The prior `Proposed` text is kept in `README.md` under *Status history*. `DECISIONS_V2.md` `ADOPTED` and the `Normative` statuses are now binding. The three v1 direction notices were updated |
| PR #49 | Three defects fixed in `d087eb7` and merged as `394dcf2`: the r4.1 queue starvation, plus two found on re-review, the substring job over-purge and the lineage loss through summary rebuild. Details: `TRANSITION_AUDIT.md` §9 |
| #46 | Kept open as recorded. The v1 engine scope is delivered; it now anchors `E-037`/`E-038` |
| Issue footers #50–#80 | Updated from "Proposed … not yet committed" to the approved status, with a visible *Update (r4.2)* note |
| Publication | `PLAN/V2/`, the direction notices and `scripts/check_statistics.py` committed to `main` (see the commit list in the r4.2 report) |
| Roadmap execution | **None.** No W0/W0b/W1 task executed, no implementation PR opened, no roadmap engine change. The only code change was the PR #49 fix, within that PR's scope |

---

## 10. r5: issue readiness and specification audit

**Scope:** specifications only — every open issue rewritten as a self-contained engineering
specification. No engine code, test, schema or migration changed; no issue closed, retitled,
relabelled or re-milestoned.

| Item | Result |
|---|---|
| Issues rewritten | **33 of 33** (#46, #47, #50–#80). All needed change: previous bodies carried no requirements, no open questions and no verification steps, and several carried acceptance criteria that were wrong |
| Implementation issues | Nine sections each: context, scope (in/out/preserved), requirements (mandatory vs recommended), open questions/blockers, dependencies, acceptance criteria, testing/verification, deliverables, definition of done |
| Trackers (#72–#76, #79, #80) | Wave structure kept: objective, boundaries, tasks, entry and exit gate, gate verification, open decisions, risks. 64 tracked tasks in total (7+9+16+13+6+8+5) |
| Conventions introduced | Items added during the audit tagged **[r5]**; reviewer decides whether a new item gates an in-flight PR or becomes a follow-up. Contradictions recorded as proposals, never fixed inside the approved documents |
| In-flight work protected | PRs #87/#88 and claims on #55/#56/#57/#59 were live during the audit. Those bodies carry a banner listing exactly which requirements are new relative to the text each PR targeted |

**Findings raised (not resolved) by r5:** the `LIKE` wildcard leak, missing ACL storage,
provenance opacity, the Tier B five-call list, the `archived` retention value, the CP-bound
application point, non-turn grounding, `V2-D13` gating, and the test-split gate. Each is
carried as an open question in the affected issue and is consolidated in
[`audits/OPEN_DECISIONS_AND_BLOCKERS.md`](audits/OPEN_DECISIONS_AND_BLOCKERS.md) by r6.

---

## 11. r6: plan corrections and final readiness audit (2026-10-31; originally mis-dated 2026-11-01, corrected in r7)

**Scope:** planning artefacts only. Every finding from r5 was **independently re-derived**
from the repository, the frozen evidence, or a live database; none was carried over on the
authority of the earlier report. The approved V2 plan was treated as the governing baseline
but not assumed correct.

### 11.1 Corrections applied (controlled)

Applied only where an approved normative statement already fixed the intended value or name,
so each edit restores intent instead of choosing it.

| # | Finding | Correction | Evidence |
|---|---|---|---|
| A1 | `STATISTICS.md` §4.2 printed `CP_upper(0,29) = 0.0981` — the one-sided **95%** figure under a one-sided **97.5%** heading | Corrected to **0.1194** (`confidence_lower = 0.8806`; verdict unchanged: still not admissible at τ=0.90). Dated note added | Computed three ways: `scipy beta.ppf`, binomial-tail search, and the repo's own `cp_upper` — all 0.119445. Every other row verified correct |
| A2 | Nothing checked the document's numerical tables; the script printed the right value and never compared it | `scripts/check_statistics.py` now parses §4.2 and asserts every row and every admissibility verdict | Negative test: reverting the cell to `0.0981` fails with two named findings; restoring it exits 0 |
| A4 | `METHOD` vs `INFERENCE_METHOD` after §2.1 declared one name | `M6` and `E-014` now say `METHOD`; the `V2-D07` line keeps the old name only as an explicit historical note | §2.1 is the authoritative statement |
| A5 | G3 called the 27 ms reference a **P95**; it is the dense **P50** | Clause rewritten to name the reference a P50 and give the real figures (dense P95 29.41, fused P95 29.84). **The gate value and threshold are unchanged** — relabelling to 29.41 would have loosened it from ≈54 ms to ≈59 ms | `reports/benchmarks/retrieval_2000.json`, frozen |

### 11.2 Findings withdrawn

| # | Finding | Why withdrawn |
|---|---|---|
| A3 | `APPROVAL.md` breaches the no-personal-names rule | Not a violation. The rule is scoped to `research-lab/` files, and that directory does not exist here; the mechanical check scans only the three files passed on its command line. `elewashy` is a GitHub account handle, which `ASSIGNMENTS.md` explicitly uses. No change made |
| C1 | 31 dependency cycles in the issue graph | An artefact of the r5 issue formatting: consumer lists sat inside `## Dependencies`. Bodies reformatted so consumers live in `## Consumed by`; the graph is now **acyclic**, verified by DFS and by a topological sort. No dependency added, removed or weakened |

### 11.3 Left open, requiring Project Owner approval

Nine blockers, none resolved by this audit: **B-01** `LIKE` wildcard tenant leak
(*reproduced on PostgreSQL 16*), **B-02** missing ACL/membership storage, **B-03** provenance
rendering vs `I17`, **B-04** `V2-D07`'s five calls vs §8.1's 23 operations, **B-05**
`archived` as a retention value, **B-06** CP-bound application point, **B-07** non-turn
grounding, **B-08** `V2-D13` absent from every gate and G0b unsatisfiable while `M5`/`M7` are
unowned, **B-09** no test-split gate. Full alternatives, impact and deadlines:
[`audits/OPEN_DECISIONS_AND_BLOCKERS.md`](audits/OPEN_DECISIONS_AND_BLOCKERS.md).

### 11.4 Issue bodies changed

Three, each removing a claim made false by 11.1: **#65** (A4 rewording), **#66** and **#74**
(A1/A2 past tense). Sixteen further bodies were reformatted by 11.2/C1 with no semantic
change. No issue was closed, retitled, relabelled, re-milestoned, or had a relationship
altered. Reconciliation: [`audits/ISSUE_PLAN_RECONCILIATION.md`](audits/ISSUE_PLAN_RECONCILIATION.md).

### 11.5 State at the end of r6

| Check | Result |
|---|---|
| Frozen record | `md5sum -c validation/document_hashes.txt` → **5/5 OK** |
| Mechanical checks | **ALL HARD CHECKS PASSED** |
| CP bounds | `diff` against `validation/cp_bounds.output.txt` → **identical** |
| Statistics | `scripts/check_statistics.py` → **exit 0**, all assertions pass |
| Plan references | `check_plan_refs.py` on `PLAN/V2` → **0 findings**; also 0 when the new `audits/` files are included |
| Issue states | **33 open / 33 closed**, unchanged |
| Open PRs | **9**, all open and untouched (#82–#90) |
| Roadmap implementation | **None started.** No engine, test, schema or migration change |

**Deliverables:** [`audits/PLAN_CORRECTION_REGISTER.md`](audits/PLAN_CORRECTION_REGISTER.md),
[`audits/OPEN_DECISIONS_AND_BLOCKERS.md`](audits/OPEN_DECISIONS_AND_BLOCKERS.md),
[`audits/ISSUE_PLAN_RECONCILIATION.md`](audits/ISSUE_PLAN_RECONCILIATION.md),
[`audits/FINAL_READINESS_AUDIT.md`](audits/FINAL_READINESS_AUDIT.md).

**Verdict:** the plan is **more consistent but not yet unblocked**. W0 remains ready to start
on authorisation; **W0b cannot be declared until B-01, B-02, B-03 and B-08 are decided**, and
no roadmap work was begun or is authorised by this audit.

---

## 12. r7: blocker resolution, V1 completion and direct delivery (2026-10-31)

**Authority:** explicit Project Owner authorization (`APPROVAL.md` Amendment 1).
**Baseline:** `main` @ `490904d`. Every change was pushed directly to `main` as fast-forward
commits, never force-pushed. Full status: `audits/R7_RESOLUTION_REPORT.md`.

| Item | Result |
|---|---|
| B-01 … B-09 | Each has a decision (`DECISIONS_V2.md` §5: `V2-D35`–`V2-D43`, `V2-D13`), an implementation and tests. Wiring into the `M1`–`M9` structures is W1 integration, named per blocker |
| #46 (`RL-070`) | Seven-channel harness probes, Suite D runner under `P`/`A8`, recorded frozen-model reports. `P`: 0/20 leak, 0 incomplete; `A8`: 20/20, exact match with the generator's expectations |
| #47 (`RL-071`) | PR #87's commit cherry-picked (authorship kept), plus a catalog privilege assertion, the suite `I` CP bound, and write/delete paths under the role |
| Engine defect found | Deleting a gate-superseded belief failed (FK on `memory_conflict.existing_version_id`). Fixed with a regression test |
| Migration runner | Multi-version (`MIGRATIONS`, `pending` state), migrations 2 (`scope_grant`) and 3 (association cap) |
| Reused PR work | #87 (`RL-071`) and #92 (`MA-012`, `API_V2.md`) cherry-picked. Both PRs were left open for their author, with a comment |
| CI | `check_statistics.py` and `check_test_split_gate.py` added as steps |
| Tests | Fresh-clone CI-equivalent: 1792 passed (baseline 1402 passed + 21 skipped); no-extras job 1558 passed |

**Errors made during r7 and corrected (recorded deliberately):**

1. Pushed head `85b2c06` **failed CI**. The Suite D runner read `research/benchmarks/own_D/data/`,
   which is gitignored; local runs passed only because a generated copy existed. Fixed in
   `0846330`: the split is now regenerated and checked against `MANIFEST.txt`. Verification has
   since run from a fresh clone of the committed HEAD.
2. The fresh clone then exposed three pyright errors that an earlier local check had not
   surfaced, because its output was misread as clean. Fixed in `75c56e4`.
3. The r6 documents were dated **2026-11-01**; the actual date was 2026-10-31. Corrected in
   place, with the original noted.

**Still open (not resolved by r7):** G0b is not declared (`MA-013`, `MA-017`). The signed
`PilotFreeze` for confirmatory runs is a human action. Open decisions `V2-D14`, `D16`, `D17`,
`D30`, `D31`, `D32` and `D34` were not taken.

## 13. r8: PR audit, W1 gate check and CI/CD refactor (2026-10-31)

**Authority:** Project Owner instruction to continue V2, review/merge ready PRs and refactor GitHub
Actions, with direct-to-`main` delivery. **Baseline:** `main` @ `b907125`. Full status:
`audits/W1_INTEGRATION_PR_REVIEW_AND_CI_REFACTOR_REPORT.md`.

| Item | Result |
|---|---|
| W1 (`E-010`–`E-017`) | **Not started.** Entry gate G0b is not met (`MA-013` not frozen, `MA-017` not implemented, G0 not met), and `APPROVAL.md` Amendment 1 excludes W1 beyond the r7 primitives. Status comment posted on #61–#68 |
| PR reviews | All 11 open PRs reviewed against full diffs and linked issues, with findings reproduced. `REQUEST_CHANGES`: #82–#86, #88–#91. `COMMENT` (already on `main`; author to close): #87, #92 |
| Merges / issue closures | **None.** No PR met the merge conditions; no issue's acceptance criteria are fully met on `main` (#55 checked: two criteria open) |
| New findings for the owner | N-1 channel legality vs `V2-D38` transitions; N-2 §11 `I3` still names `INFERENCE_METHOD`; N-3 no `create_entity`/`register_method`; N-4 Tier B `contradicts` demotion; N-5 transitive re-derivation content; N-6 `v1.0.0` tag commit |
| CI | `22636e1`: duplicate PR runs removed (−39% engine job-seconds per PR push); root CI now runs on `main` (it targeted `master`); `deploy.yaml` (never ran) made manual-only; commit-SHA pins, least privilege, timeouts, concurrency; `static` job split out (37 s feedback); shared `setup-cme` action. Gate-command diff: none dropped. Verified on real runners |
| Tests | Local and remote at `22636e1`: 1792 passed (full CI scope); no-extras 1558 passed, 218 skipped. Frozen records 5/5 OK |

**Still open:** G0, G0b, `MA-013`, `MA-017`, PilotFreeze (unsigned; human action), open decisions
`V2-D14`, `D16`, `D17`, `D30`, `D31`, `D32`, `D34`, and N-1–N-6.
