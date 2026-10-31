# Plan Correction Register

> **Superseded status (r7, 2026-10-31).** This r6 document is preserved as written. Every
> blocker it lists is now resolved; see `R7_RESOLUTION_REPORT.md` and `DECISIONS_V2.md` §5.


**Status:** Audit deliverable. Records corrections and dispositions; it does not approve
anything.
**Audit:** r6 — Plan Corrections & Final Readiness Audit.
**Baseline:** `main` @ `490904d` (working tree clean at audit start). Open PRs were read
**as proposals only** and are never treated as adopted fact.
**Method:** every finding below was re-derived from the repository, the frozen evidence, or
a live database. No finding is carried over on the authority of an earlier audit.

## Classification vocabulary

| Class | Meaning |
|---|---|
| **Factual error** | A statement contradicted by computation or by the repository. |
| **Doc inconsistency** | Two places in the plan say different things; neither is provably wrong. |
| **Missing verification** | A claim is correct but nothing checks it. |
| **Unresolved decision** | The correct behaviour is not settled; requires the Project Owner. |
| **Missing element** | The plan references something that does not exist and is not scheduled. |
| **Not a defect** | Investigated and withdrawn. |

## Disposition vocabulary

| Disposition | Meaning |
|---|---|
| **Corrected** | Changed in place. Used only where the intended value/behaviour is already fixed by an approved normative statement, so the edit restores intent rather than choosing it. |
| **Corrected + decision open** | The immediate hazard was closed fail-closed; the normative choice still needs the owner. |
| **Requires approval** | Left unchanged. A proposed change is written in `OPEN_DECISIONS_AND_BLOCKERS.md`. |
| **Withdrawn** | Not a defect. |

---

## A. Confirmed documentation errors

### A1 — `STATISTICS.md` §4.2 printed the wrong `CP_upper(0, 29)` — **Corrected**

| | |
|---|---|
| **Class** | Factual error |
| **Disposition** | Corrected (controlled) |
| **Authoritative source** | `STATISTICS.md` §4.2's own convention: "All bounds use a **one-sided 97.5%** limit"; the table's column heading "at one-sided 97.5%"; `evaluation/stats/clopper_pearson.py::cp_upper` default `tail_probability=0.025`. |

**Reproduced independently, three ways.** `CP_upper(0,29)`:

- `scipy.stats.beta.ppf(0.975, 1, 29)` = **0.119445**
- a binomial tail search (`P(X ≤ 0 | p) = 0.025`) = **0.119445**
- `evaluation/stats/clopper_pearson.py::cp_upper(0, 29)` = **0.119445**

`0.098145` is the **one-sided 95%** value. The row printed `0.0981` under a one-sided 97.5%
heading, in the same section that opens by correcting exactly this mistake for `o = 8`
(where the r2 draft's 95% figure `≈ 0.32` was replaced by the 97.5% figure `0.3694`). The row
was therefore internally inconsistent with its own section.

**Verdict impact: none.** `1 − 0.1194 = 0.8806 < 0.90`, so the row is still not admissible.
The *number* was wrong; the *decision* it supports was right.

**Every other row in the table was verified correct** (8, 36, 40, 100 all agree with the
computed bound to < 2e-5), and the narrative claim "the zero-counter threshold is `o ≥ 36`
at one-sided 97.5%, `o ≥ 29` at one-sided 95%" was verified correct.

**Artifacts changed.**
- `STATISTICS.md` §4.2: `0.0981` → `0.1194`; the "marginal" wording → the explicit
  `confidence_lower = 0.881` rationale; a dated correction note added, which points back to
  this register.
- The approval history is untouched: this is a numerical correction within an approved
  document, not a change to an approved decision. See `FINAL_READINESS_AUDIT.md` §6.

---

### A2 — No automated verification of the numerical tables — **Corrected**

| | |
|---|---|
| **Class** | Missing verification |
| **Disposition** | Corrected |
| **Authoritative source** | `SD-007` ("reproduce every number in `STATISTICS.md`"); `check_statistics.py`'s own docstring: "so that no number in the V2 documentation is asserted without a runnable derivation". |

**Reproduced.** Before the change, `scripts/check_statistics.py` verified only the ledger
admission cases, the two zero-counter thresholds, three closed-form agreements, a design-effect
table and the r3 regression. It printed `cp_upper(0,29) = 0.119445` in its own output while the
document printed `0.0981`, and **never compared the two**. That is precisely how A1 survived.

**Correction.** Added `audit_documented_table()`, which parses the §4.2 table out of
`STATISTICS.md` and asserts each printed bound against the computed one (tolerance `1e-4`) and
each printed admissibility verdict against the computed verdict. The document is parsed, not
restated, so a wrong table entry is a failure rather than a second copy of the same mistake.

**Proof the check works (negative test).** Reverting the table cell to `0.0981` yields:

```text
FAILED:
  - §4.2 table o=29: printed 0.0981 != computed 0.1194
  - §4.2 table o=29: printed value implies a different verdict
```

and restoring `0.1194` returns exit 0. This is the only change to a script in this audit; it
adds no engine behaviour and computes nothing new (it reuses the frozen v1 `cp_upper`).

---

### A3 — Maintainer account name in `APPROVAL.md` — **Withdrawn**

| | |
|---|---|
| **Class** | Not a defect |
| **Disposition** | Withdrawn (no change) |
| **Authoritative source** | `PLAN/CONTRIBUTING.md` §1.4; `validation/mechanical_checks.py` §5; `PLAN/ASSIGNMENTS.md`. |

**Investigated and found not to be a violation.** The earlier audit reported that
`APPROVAL.md` line 9 (``Recorded by: `elewashy` ``) breaches the no-personal-names rule.
Checking the rule's actual scope:

- `CONTRIBUTING.md` §1.4: "Never commit personal names (`Mazen`, `Ehab`, `Elewashy`, `Nexa`)
  **inside `research-lab/` files**."
- `ASSIGNMENTS.md`: "documents **inside `research-lab/`** use role identifiers (`Member 1`–`Member 5`) rather than personal names."
- `validation/mechanical_checks.py` §5 scans exactly the three paths passed on the command
  line (proposal, checklist, changelog) — not `PLAN/`.

So the rule is scoped to a v1 research directory that **does not exist in this repository**
(`research-lab/` is absent), and the mechanical check cannot see `PLAN/V2/` at all. Further,
`elewashy` is a GitHub account handle, not a personal name; `ASSIGNMENTS.md` explicitly maps
role ids to GitHub handles.

**No change made.** Recording an approval without an audit trail would be worse than the
cosmetic objection. Residual observation for the owner, not a defect: the repository has **no**
repo-wide no-personal-names rule. If one is wanted, it is a new rule, not a correction to an
existing one, and it must be recorded as such.

---

### A4 — `METHOD` vs `INFERENCE_METHOD` — **Corrected**

| | |
|---|---|
| **Class** | Doc inconsistency |
| **Disposition** | Corrected (controlled) |
| **Authoritative source** | `ARCHITECTURE_V2.md` §2.1: "`METHOD` is the physical table; the field is `method_id`. (The first draft used both `METHOD` and `INFERENCE_METHOD`; one name now.)" `SYNC_LOG.md` A10 records the same resolution. |

**Reproduced.** After §2.1 declared one name, three sites still said `INFERENCE_METHOD`:
`ARCHITECTURE_V2.md` §12 `M6`, `ROADMAP_V2.md` `E-014`, and `DECISIONS_V2.md` `V2-D07`
consequences. Two of the three are *normative statements about what gets built*, so an
implementer reading only the migration table would create the wrong table name.

**Correction.** The two build-facing sites (`M6`, `E-014`) now say `METHOD`. The
`DECISIONS_V2.md` line keeps the historical name in parentheses with an explicit "was the
first-draft name, retired in `ARCHITECTURE_V2.md` §2.1" so the decision's own history is
preserved rather than silently rewritten.

**No decision changed.** This restores the name §2.1 already chose.

---

### A5 — The G3 "27 ms" baseline is a P50, not a P95 — **Corrected, with the gate kept strict**

| | |
|---|---|
| **Class** | Factual error (mislabel) |
| **Disposition** | Corrected, comparator choice flagged for `E-002` |
| **Authoritative source** | The frozen measurement `reports/benchmarks/retrieval_2000.json`. |

**Reproduced from the frozen file**, `candidates = 2000`:

| Channel | P50 | P95 |
|---|---|---|
| dense | **26.99 ms** | **29.41 ms** |
| lexical | 11.11 ms | 11.95 ms |
| fused | 28.16 ms | **29.84 ms** |

`ROADMAP_V2.md` G3 said "Retrieval **P95** at v1 scale within 2× of the **27 ms** baseline".
27 ms is the dense **P50**; the corresponding P95s are 29.41 ms and 29.84 ms. `CRITIQUE.md`
states it correctly ("P50 27 ms @ 2,000 candidates"), which is how the error is provably a
mislabel in the roadmap rather than a different measurement.

**Decision deliberately *not* taken.** Relabelling the figure as a P95 (29.41 ms) would
loosen the gate from ≈54 ms to ≈59 ms — an acceptance criterion silently weakened by a
documentation fix. The gate therefore keeps **27 ms** and is now explicit that the reference
is a P50 compared against a **P95** measurement, which is the stricter reading. Which pair
should ultimately be compared (P50-vs-P50 or P95-vs-P95) is recorded for `E-002` (#51), the
task that owns the baseline, rather than chosen here.

**Artifacts changed.** `ROADMAP_V2.md` G3 only.

---

## B. Architectural, security and governance findings

Every item below is **unchanged in the approved documents**. Full alternatives, impact and
deadlines are in `OPEN_DECISIONS_AND_BLOCKERS.md`; this table is the register view.

| ID | Finding | Class | Disposition | Blocks | Detail |
|---|---|---|---|---|---|
| **B-01** | `path_with_prefix LIKE ANY(...)` lets `_` and `%` in a scope id match another tenant | Security defect (**reproduced on PG 16**) | Corrected + decision open | `MA-011`, `E-011`, G0b, G1 | `OPEN_DECISIONS_AND_BLOCKERS.md` B-01 |
| **B-02** | No storage or lifecycle for project/shared access lists, though §5 rules 2–3 require an ACL | Missing element | Requires approval | `E-011`, `E-014` | …/ B-02 |
| **B-03** | Provenance renders `id + scope + type`, which contradicts `I17` | Doc inconsistency | Requires approval | `MA-013`, `E-017` | …/ B-03 |
| **B-04** | `V2-D07` names five calls (one of which, `retract`, does not exist) against §8.1's 23 operations / 17 Tier B | Factual error in an ADOPTED decision | Requires approval | `MA-012`, `MA-015` | …/ B-04 |
| **B-05** | `M7` calls `archived` a *retention* value; the retention enum has three values and none is `archived`. `M1` leaves v1 statuses unmapped | Doc inconsistency | Requires approval | `E-010`, `E-020` | …/ B-05 |
| **B-06** | The CP bound's application point is contradictory: §2.2 rejects below-τ at creation, `STATISTICS.md` §4.2 renders it as "hypothesis only" | Doc inconsistency | Requires approval | `E-015`, `C-021` | …/ B-06 |
| **B-07** | `G1a` permits "any declared source type" but the gate only verifies user-authored turns | Missing element | Requires approval | `E-013`, `E-015` | …/ B-07 |
| **B-08** | `V2-D13` is open and marked "needed before W1" but appears in no gate; G0b also requires `MA-017` green, which its own spec says fails on unowned `M5`/`M7` | Governance defect | Requires approval | W1 entry | …/ B-08 |
| **B-09** | No V2 gate certifies test-split integrity, though G5 consumes the `own_P` test split | Missing element | Requires approval | W6 | …/ B-09 |

### B-01 reproduction detail (the only one that is a live vulnerability)

```text
SELECT path FROM (VALUES ('/u/a_b/'), ('/u/aXb/')) v(path)
 WHERE path LIKE ANY (ARRAY['/u/a_b/']);
   /u/a_b/
   /u/aXb/          <- another tenant's row, returned to alice's selector
```

`starts_with(path_with_prefix, '/u/a_b/')` returns only `/u/a_b/`. The trailing-separator rule
(`V2-D27`, ADOPTED) is necessary but **not sufficient**: it stops the `/u/4/` vs `/u/42/`
prefix leak and does nothing about metacharacters. PR #86's `THREAT_MODEL.md` independently
raises this as `T-02` and proposes the same fix.

---

## C. Cross-issue and dependency consistency

### C1 — Apparent dependency cycles — **Not a defect (artifact of issue formatting)**

| | |
|---|---|
| **Class** | Not a defect |
| **Disposition** | Withdrawn; issue formatting improved |

**Reproduced, then explained.** A naive parse of the 31 task issues' `## Dependencies`
sections reported **31 cycles**, including `#54 ↔ #55`, `#69 ↔ #68`, `#61 ↔ #59`, and a
nine-node loop. On inspection, every cycle was created by *consumer* lists living inside the
Dependencies section: `MA-010`'s section ends with "Feeds `MA-011` (#54), …", so a mechanical
reader saw `#53 → #54` and `#54 → #53`.

Excluding the `Consumers:`/`Feeds:` lines yields **zero cycles** and this valid start order:

```text
#77 #50 #51 #52 #53 #54 #55 #56 #57 #58 #59 #60 #61 #47 #62 #63 #64
#65 #66 #67 #68 #71 #70 #69 #84 #78
```

**Action.** Nothing in the roadmap changed. The dependency sections of the affected issues are
being restated as "Depends on" with consumers in a separate `## Consumed by` heading, so the
graph is machine-readable and this class of false cycle cannot recur. No dependency was added
or removed by that reformatting.

### C2 — Dependency corrections from the earlier audit — **Verified as necessary**

Each was re-checked against the task it gates, not accepted on prior authority:

| Edge | Verified necessary? | Evidence |
|---|---|---|
| `E-018` → `E-016` | Yes | `E-018` asserts legal `(kind, lifecycle, verified, stale)` tuples; the legal set is defined by §2.3's transition table, built in `E-016`. |
| `E-018` → `E-019` | Yes | Its "six memory types via profile config only" criterion needs the profile schema. |
| `E-018` → `E-011` | Yes | Storage is scope-qualified; G1 requires `MA-011`'s isolation test green. |
| `E-018` → `E-013` | Yes | Its tuple set includes episodes with non-turn evidence (M4). |
| `E-015` → `E-013` | Yes | `ground()` is "generalised over source types"; without M4 only `turn` exists. Soft: a `turn`-only `ground()` could land first, which is why it is recorded as a hard edge — the task's own description requires the generalisation. |
| `E-016` → `E-014` | Yes | The `inferred → inferred (verified=true)` transition is recorded in a `VERIFICATION` row and needs `METHOD`; neither exists before M6. |
| `E-017` → `E-014` | Yes | Rendering reads `ABSTRACTION_STATS` (counts, coverage, verification status). |
| `E-017` → `E-011` | Yes | Provenance opacity is decided by whether the caller can read the source scope. |
| `E-019` → `E-011` | Yes | Profile scope roots are scope semantics. |
| `E-019` → `E-020` | Yes | The profile vocabulary must be a subset of the six-value facet enum. |
| `E-011` → `#47` | Yes | G1 requires isolation proven under a non-bypass role; #47 ships that role. |

**No edge was found that is not technically necessary.** One is soft (`E-015` → `E-013`) and is
marked as such in the issue.

### C3 — No forward-wave dependency exists — **Verified**

No task issue depends on a task in a later wave. Every reference to a W2+ task (`E-021`,
`E-026`, `E-032`, `E-037`, `C-010`, `C-021`, `C-026`, `SD-002`, `UM-010`–`UM-014`, `A-010`,
`A-017`, `E-025`) appears only in a **consumer** line. This is what makes the wave gates
meaningful; it was worth checking and it holds.

### C4 — Multi-version migration runner — **Ownership recorded (already assigned)**

`migrate.py` has `VERSION = 1` and `HISTORY_TABLE` and applies exactly one schema version;
V2 needs M1–M9. This is not an `ARCHITECTURE_V2.md` element, so `MA-017` would not catch it.
Ownership is recorded as: **`MA-016` (#59) specifies the runner contract; `E-010` (#61)
implements it unless `MA-016` assigns it elsewhere.** Verified present in both issues'
scope sections. Not a new assignment — it was recorded in the r5 issue pass and is
re-confirmed here.

### C5 — `I1′` database guard — **Ownership recorded (already assigned)**

`I1′` is enforced at two levels (§2.2): a `CHECK`/constraint and an API contract. No roadmap
task owned the database-level guard. It is assigned to **`E-016` (#67)**, which owns the
lifecycle-conditioned constraints, and is cross-referenced from `E-014` (#65). Verified
present in #67's requirements and acceptance criteria.

### C6 — `M5` and `M7` have no owning task — **Confirmed; cannot be fixed here**

Verified mechanically: mapping each `M1`–`M9` row in `ARCHITECTURE_V2.md` §12 to roadmap task
rows gives owners for M1–M4, M6, M8, M9 and **none for M5 and M7**. `MA-017` (#60) *will*
fail on this, which is its purpose, but G0b requires `MA-017` green → **B-08**. Assigning an
owner is a Project Owner decision (the same conclusion `MA-016` #59 and `MA-017` #60 already
record). Nothing was assigned by this audit.

### C7 — `MA-017` correctly flags migration gaps — **Verified by construction**

Read from its specification: it extracts elements from `ARCHITECTURE_V2.md` mechanically
(tables, invariants, channels, API operations, `M1`–`M9`), counts an element owned only if it
maps to a real roadmap task id or an explicit `deferred` marker with a reason and decision
reference, and fails CI naming each unowned element. Its "Known current gaps" section already
names M5 and M7. So the detector is correct; the *gap* is real and unresolved (C6).

### C8 — Task ids, decisions, gates, milestones — **Verified**

- `ROADMAP_V2.md` defines **88** task rows, no duplicates.
- Three ids referenced in the roadmap are retired aliases, all accounted for: `C-013` →
  `SD-001`, `C-024` → `UM-010`, `R-014` → `E-037` (moved to W3 during r4.1).
- Running the `MA-018` checker (PR #83, unmerged) against `PLAN/V2/`: **0 findings** before
  and after this audit's edits, so no undefined id, gate, decision, migration, duplicate
  heading or broken link was introduced.
- Milestones W0–W8 and the 33 open issues are unchanged; no title, label, milestone, state or
  relationship was modified.

---

## D. Corrections made in this audit — complete list

| File | Change | Finding |
|---|---|---|
| `PLAN/V2/STATISTICS.md` | `CP_upper(0,29)` `0.0981` → `0.1194`; "marginal" → explicit `0.881`; dated correction note | A1 |
| `scripts/check_statistics.py` | Added `audit_documented_table()`: parses and asserts every §4.2 table row and verdict | A2 |
| `PLAN/V2/ARCHITECTURE_V2.md` | `M6`: `INFERENCE_METHOD` → `METHOD` | A4 |
| `PLAN/V2/ROADMAP_V2.md` | `E-014`: `INFERENCE_METHOD` → `METHOD` | A4 |
| `PLAN/V2/DECISIONS_V2.md` | `V2-D07` consequences: `METHOD` canonical, old name kept as historical note | A4 |
| `PLAN/V2/ROADMAP_V2.md` | G3: reference identified as dense P50 26.99, P95 29.41 / fused 29.84; gate value unchanged | A5 |

**No approved architecture, research objective, scope, decision, gate threshold, or
acceptance criterion was changed.** No engine code, test, schema, migration or workflow was
touched. `md5sum -c validation/document_hashes.txt` still reports 5/5 OK.

## E. Findings deliberately left open

`B-01` … `B-09` (nine items). All require Project Owner approval; none was resolved by this
audit. Their effect on readiness is in `FINAL_READINESS_AUDIT.md` §5.
