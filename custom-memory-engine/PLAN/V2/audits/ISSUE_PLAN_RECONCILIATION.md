# Issue ↔ Plan Reconciliation

**Status:** Audit deliverable.
**Audit:** r6 — Plan Corrections & Final Readiness Audit.
**Scope:** the 33 open issues (#46, #47, #50–#80) and their agreement with the corrected
`PLAN/V2/` documents.
**Constraint honoured:** no issue was closed, recreated, retitled, relabelled, re-milestoned,
or had a relationship changed. Only body text was edited, and only where a corrected fact
made the previous text wrong.

---

## 1. What was checked

| Check | Method | Result |
|---|---|---|
| Every task id referenced by an issue exists in `ROADMAP_V2.md` | regex over all 33 live bodies vs the 88 roadmap task rows (+3 retired aliases) | **0 undefined** |
| Every `#NNN` reference resolves to a real issue or PR | live API enumeration (66 issues, 25 PRs) | **0 dangling** |
| No dependency cycle in the live issue graph | graph build from `## Dependencies` sections, DFS | **0 cycles** |
| No forward-wave dependency | consumer lines excluded, then manual read of every W2+ reference | **0 violations** |
| Issues do not assert superseded facts | grep for the pre-correction values | **0 residual** |
| Issue count, states, labels, milestones unchanged | before/after capture on every edit | **unchanged (33 open / 33 closed)** |

---

## 2. Cycle elimination (C1) — how and why

The r5 issue bodies put **consumer** lists inside `## Dependencies`. A mechanical reader
therefore saw `#53 → #54` ("Feeds `MA-011`") and `#54 → #53`, producing **31 apparent
cycles** including a nine-node loop.

Those were a formatting defect in the issues, not a defect in the roadmap. The fix moved the
consumer lines into a **`## Consumed by`** section, which is now separate from
`## Dependencies`. Sixteen bodies were reformatted (#53–#59, #61, #62, #64–#68, #70, #71);
no dependency was added, removed or weakened.

**Resulting graph (acyclic, verified):**

```text
#50→#77   #51→#50   #54→#53   #55→#54   #56→#53   #58→#55   #60→#55
#61→#51,#59   #62→#47,#54,#59,#61   #63→#59,#61,#62   #64→#59,#61
#65→#53,#55,#59,#61,#62   #66→#55,#61,#64,#65   #67→#55,#61,#65
#68→#56,#62,#65,#66,#67   #69→#61,#62,#64,#65,#67,#68,#70,#71
#70→#61,#62,#71   #71→#61   #78→#84
```

Valid start order: `#77 #50 #51 #53 #54 #55 #56 #58 #60 #59 #61 #47 #62 #63 #64 #65 #66
#67 #68 #71 #70 #69 #84 #78`.

`#52` (E-004) has no prerequisites. `#46` and `#47` are v1 originals and start on
authorisation, not on a gate.

---

## 3. Edits made to issue bodies in this audit

Only three bodies had to change, and each change removes a claim that the corrected plan
makes false. Nothing about scope, acceptance intent or priority was altered.

| Issue | Was | Now | Reason |
|---|---|---|---|
| #66 (`E-015`) | "the `o = 29` row **prints** `0.0981`" (a live defect) | "printed … now corrected to `0.1194`; `check_statistics.py` asserts the table" | A1 + A2 corrected the document; the issue must not instruct an implementer to work around a fixed error |
| #74 (`W4` tracker) | "the `o = 29` row **prints** `0.0981`" | same, past tense, marked **corrected r6** | the `SD-007` cross-check requirement is unchanged and still mandatory |
| #65 (`E-014`) | "§11 `I3`, §12 M6 and `ROADMAP_V2.md` `E-014` still say `INFERENCE_METHOD`" | M6 and `E-014` corrected in r6; §11 `I3` was never an instance | A4 corrected two of the three sites; `I3` is a prose invariant naming no table |

**Deliberately not edited.** The nine `B-*` blockers are unchanged in the issues, because
none is resolved. Each issue already carries the blocker as an open question, and
`OPEN_DECISIONS_AND_BLOCKERS.md` now states the decision, impact and deadline in one place.

---

## 4. Per-issue reconciliation

`✓` = the issue's specification agrees with the corrected plan. Blocker ids point to
`OPEN_DECISIONS_AND_BLOCKERS.md`.

### Retained v1 issues

| Issue | Task | Status | Note |
|---|---|---|---|
| #46 | `RL-070` | ✓ | The remainder (channels 1-cache, 3, 5, 6, 7) is unaffected by any correction. Stays open as the traceability anchor. |
| #47 | `RL-071` | ✓ | Unaffected. Note PR #87 implements it and is **not** affected by this audit. |

### W0

| Issue | Task | Status | Note |
|---|---|---|---|
| #50 | `E-001` | ✓ | Unaffected. |
| #51 | `E-002` | ✓ | **Updated basis:** A5 now records the P50/P95 distinction in G3. `E-002` still owns the choice of comparator; its acceptance criteria already require the exact figure to be recorded. |
| #52 | `E-004` | ✓ | Unaffected. |
| #77 | `E-003` | ✓ | Unaffected. |

### W0b

| Issue | Task | Status | Note |
|---|---|---|---|
| #53 | `MA-010` | ✓ | Carries **B-01** (LIKE) as an open question; unchanged, correctly. |
| #54 | `MA-011` | ✓ | Carries **B-01**, **B-02**; the design gate is where both must close. |
| #55 | `MA-012` | ✓ | Carries **B-04**. |
| #56 | `MA-013` | ✓ | Carries **B-03**; `MA-013` is the freeze point for it. |
| #57 | `MA-014` | ✓ | Unaffected. |
| #58 | `MA-015` | ✓ | Carries **B-03**/`B-04` as consequences of #55/#56. |
| #59 | `MA-016` | ✓ | Owns the runner contract; must also specify **B-05** (`M1`/`M7`) and **B-08** (`M5`/`M7` owners). |
| #60 | `MA-017` | ✓ | Its "known gaps" (M5, M7) are confirmed real — see **B-08**. |
| #78 | `MA-018` | ✓ | Runs clean on the corrected tree (0 findings). |

### W1

| Issue | Task | Status | Note |
|---|---|---|---|
| #61 | `E-010` | ✓ | Carries **B-05**. |
| #62 | `E-011` | ✓ | Carries **B-01**, **B-02**; requires #47. |
| #63 | `E-012` | ✓ | Unaffected. |
| #64 | `E-013` | ✓ | Carries **B-07**. |
| #65 | `E-014` | ✓ *(edited)* | A4 rewording applied. Carries **B-02**. |
| #66 | `E-015` | ✓ *(edited)* | A1/A2 rewording applied. Carries **B-06**, **B-07**. |
| #67 | `E-016` | ✓ | Owns the `I1′` guard. |
| #68 | `E-017` | ✓ | Carries **B-03**. |
| #69 | `E-018` | ✓ | Orchestrates G1; depends on the whole W1 set. |
| #70 | `E-019` | ✓ | Unaffected. |
| #71 | `E-020` | ✓ | Carries **B-05**. |

### Trackers

| Issue | Wave | Status | Note |
|---|---|---|---|
| #72 | W2 | ✓ | Unaffected. |
| #73 | W3 | ✓ | Unaffected; the G3 clause it cites is now corrected (A5). |
| #74 | W4 | ✓ *(edited)* | A1/A2 rewording applied; `SD-007` scope unchanged. |
| #75 | W5 | ✓ | Carries **B-06** as its first risk. |
| #76 | W6 | ✓ | Carries **B-09**. |
| #79 | W7 | ✓ | Unaffected. |
| #80 | W8 | ✓ | Unaffected. |

---

## 5. Open PRs — inspected, unchanged

All eleven open PRs were inspected (#82–#92; #91 and #92 were opened during this audit).
**No PR code, review status or merge state was touched by this audit.** Where an issue body changed, the PR was checked for whether it depends on the
corrected text.

| PR | Closes | Branch head | Depends on a corrected fact? | Status |
|---|---|---|---|---|
| #82 | #77 | `5b529c7` | no | open, checks green, **unchanged** |
| #83 | #78 | `1f02051` | no — rebuilt against the corrected tree, 0 findings | open, **unchanged** |
| #84 | #50 | `05f7889` | no | open, **unchanged** |
| #85 | #52 | `adc8f9d` | no | open, **unchanged** |
| #86 | #53 | `a0c34a9` | **no** — it independently raises B-01 as `T-02` | open, **unchanged** |
| #87 | #47 | `9c1a8aa` | no | open, **unchanged** |
| #88 | #51 | `563e9e6` | **partially** — its `BASELINE.md` quotes the G3 "27 ms" tolerance; A5 now states the reference is a P50 | open, **unchanged**; noted below |
| #89 | #59 | `4b7ff1a` | **partially** — it is the migration review that must eventually cover B-05/B-08 | open, **unchanged**; noted below |
| #90 | #57 | `27c2af5` | no | open, **unchanged** |
| #91 | #56 | `7cbbfc3` | **partially** — `CHANNELS.md` carries B-03's provenance-opacity question as T-10 (open) and adds an `audit_only` channel for `retired` | open, **unchanged**; corroborates B-03 |
| #92 | #55 | `3bd5bb8` | **partially** — `API_V2.md` already specifies **23 operations** and `retract_own`, i.e. the §8.1 surface rather than `V2-D07`'s five calls | open, **unchanged**; relevant to B-04 |

**Note on #88 and #89.** Both were already open and reviewed against the pre-audit text.
This audit does not reject them and does not accept them; A5 and B-05/B-08 are recorded so the
reviewer can decide whether they are follow-ups. Neither PR is blocked by this audit.

**On #87 (`RL-071`).** It implements #47, whose specification this audit did not change.
Nothing here weakens its acceptance criteria.

---

## 6. Residual mismatches

Two remain, and both are **intentional** — they are the unresolved decisions, not drift:

1. **`MA-011`/`E-011`** specify `LIKE ANY` behaviour that **B-01** shows is leaky. The issues
   correctly carry this as a blocker; the documents are unchanged pending approval.
2. **`MA-012`/`MA-015`/`E-017`** reference the five-call list and the `id + scope + type`
   provenance format, which **B-04** and **B-03** respectively contradict. Again carried as
   blockers, not silently rewritten.

No other mismatch exists: every task id resolves, every cross-reference resolves, every
dependency is necessary and acyclic, and no issue asserts a fact the corrected plan denies.
