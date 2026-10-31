# ChronaMind V2 — Direction Index

**Status:** **Approved / Canonical Implementation Plan**. Approved by the Project Owner on
**2026-10-31**. Formal record: [`APPROVAL.md`](APPROVAL.md).
**Roadmap implementation is not yet authorised.** The Project Owner will authorise W0 and
later waves in a separate instruction. Until then, no roadmap task is executed.

<details><summary>Status history (preserved)</summary>

Before approval, r4.1 recorded the following, verbatim:

> **Status:** Proposed. **It has not been approved as the canonical implementation plan.**
> *(Checked 2026-10-31 for r4.1. There is no owner or supervisor approval record in
> `PLAN/DECISIONS_REGISTER.md`, in any issue, or in any PR. The `PLAN/V2/` files are not yet
> committed to `main`. GitHub milestones W0–W8 and issues #50–#80 now **exist**, but creating
> tracker artefacts is not the same as approving the plan. Until the Project Owner records
> approval, V2 is the proposed direction and v1 remains the governing record. On approval,
> change this line to `Approved (<date>, <approver>, <record link>)`. W0 work is safe to begin
> under either status, because it only audits and freezes v1.)*

Both pending conditions are now resolved. The Project Owner approved V2 on 2026-10-31
(`APPROVAL.md`), and r4.2 commits `PLAN/V2/` to `main`.
</details>

This plan **supersedes** the *planning* content of `PROJECT_ROADMAP.md`,
`PLAN/TASK_GRAPH.md`, `PLAN/SCHEDULE.md`, `PLAN/ARCHITECTURE.md`, and
`PLAN/DECISIONS_REGISTER.md` for all work after the v1 baseline freeze.
**Does not supersede:** `Graduation_Project_Proposal.md` (V4),
`Methodology_and_Consistency_Checklist.md`, `TECHNICAL_SPECIFICATION.md` §3.3–§3.5
(the bitemporal core, which survives intact), or any `evidence/*` record.
Those remain the historical and methodological record and are **edited by this plan
only through additive headers**. `validation/document_hashes.txt` stays valid.

**Revisions.**
- **r2** — post-review revision addressing user-model status, inference-verification
  statistics, architecture consistency, plan completeness, and research coverage.
- **r3** — statistical correction pass: `undetermined` partial identification, one
  confidence convention, dependence assumptions, facet vocabulary, `UM-015` status.
- **r4.2** — **approval and publication**. The Project Owner approved V2 as the canonical
  implementation plan ([`APPROVAL.md`](APPROVAL.md)). PR #49 (`RL-070`) was fixed and merged
  (`394dcf2`). `PLAN/V2/` was committed to `main`. No roadmap work has started.
  See [`SYNC_LOG.md`](SYNC_LOG.md) §9.
- **r4.1** — reporting corrections: W4 = 16 tasks (total 88); test figures reconciled
  per scope; open-issue breakdown 24 + 7 + 2 = 33; status confirmed **Proposed**; open PR #49
  reviewed and held. See [`TRANSITION_AUDIT.md`](TRANSITION_AUDIT.md) §7–§8.
- **r4** — backlog transition: 31 issues created (#50–#80), all 35 v1 issues mapped and
  dispositioned without closing or rewriting any of them. See
  [`TRANSITION_AUDIT.md`](TRANSITION_AUDIT.md) and
  [`TRACEABILITY_REPORT.md`](TRACEABILITY_REPORT.md).

Full audit trail and every defect found: [`SYNC_LOG.md`](SYNC_LOG.md).

---

## The one-paragraph version

ChronaMind today is a well-tested bi-temporal *claims* store for one conversational
user. The target is a **general-purpose cognitive memory engine**: a substrate that
holds episodes, claims, procedures and abstractions with exact temporal and
provenance semantics for *any* agent, plus an explicitly separated cognition layer
that performs reflection and pattern discovery **on top of the public API**, plus an
**evidence-grounded evolving user model** — the Personal Digital Twin — that is a core
research capability (F4), not an optional demo. Applications are *profiles* over that
API and hold no privileged access.

The redesign is not "add reflection." It is: **decompose the type system into
orthogonal axes**, **make epistemic status first-class with a hard no-factification
rule**, **separate provenance edges from association edges**, **gate abstraction behind
finite-sample verification**, and **make the user model a view over artifacts rather
than a store of its own**.

---

## Read in this order

| # | Document | What it decides |
|---|---|---|
| 1 | [`CRITIQUE.md`](CRITIQUE.md) | What is wrong with v1 (file:line evidence), what is worth keeping, and the 10 defects in the V2 draft itself. |
| 2 | [`CHARTER.md`](CHARTER.md) | Identity, three tiers, non-goals, 12 constraining principles. |
| 3 | [`ARCHITECTURE_V2.md`](ARCHITECTURE_V2.md) | Target model: axes, artifacts, two admission policies, scope isolation, channels, tier-B API surface, staged retrieval, deletion ordering, invariants, migration, profiles. |
| 4 | [`STATISTICS.md`](STATISTICS.md) | **Why the naive statistics in r1 were invalid**, and the corrected methodology for sparse, user-specific, temporally correlated data. |
| 5 | [`USER_MODEL.md`](USER_MODEL.md) | The evolving user model: structure, lifecycle, trust ladder, personalization semantics, `own_U` instrument, F4 hypotheses. |
| 6 | [`ROADMAP_V2.md`](ROADMAP_V2.md) | Waves W0–W8 + W0b, gates G0–G8, task IDs, corrected dependency order, corrected de-scope ladder. |
| 7 | [`RESEARCH_PROGRAM_V2.md`](RESEARCH_PROGRAM_V2.md) | Four contribution families, `S1`–`S19`, methodological corrections, falsification table, evaluation inventory. |
| 8 | [`DECISIONS_V2.md`](DECISIONS_V2.md) | `V2-D01`–`V2-D43`, with explicit supersessions of v1 decisions and of r1's own defects; §5 records the r7 blocker resolutions. |
| 9 | [`SYNC_LOG.md`](SYNC_LOG.md) | The r1→r2→r3 audit: what was checked, what broke, what changed, and what remains inconsistent. |
| 10 | [`TRANSITION_AUDIT.md`](TRANSITION_AUDIT.md) | v1→V2 backlog transition: the old→new issue mapping, dispositions, and the document-drift findings. |
| 11 | [`TRACEABILITY_REPORT.md`](TRACEABILITY_REPORT.md) | Proof that all 88 roadmap tasks are mapped, dependency order is verified, and the backlog is ready for W0/W0b. |
| 12 | [`APPROVAL.md`](APPROVAL.md) | Formal approval record: approver, date, scope, and what approval does **not** authorise (roadmap implementation). |
| 13 | [`API_V2.md`](API_V2.md) | The frozen public API surface (`MA-012`, `MA-015`): 29 operations, tier matrix with admin role and conditions, signatures and errors. Executable form: `custom_memory_engine.api.contract`. |
| 14 | [`audits/R7_RESOLUTION_REPORT.md`](audits/R7_RESOLUTION_REPORT.md) | r7: how B-01–B-09 were resolved, what is verified, what remains W1 integration, and #46/#47 completion evidence. The r6 audit documents in `audits/` are preserved history. |
| 15 | [`audits/W1_INTEGRATION_PR_REVIEW_AND_CI_REFACTOR_REPORT.md`](audits/W1_INTEGRATION_PR_REVIEW_AND_CI_REFACTOR_REPORT.md) | r8: review of every open PR, why W1 has not started (G0/G0b unmet), new owner findings N-1–N-6, and the GitHub Actions refactor with before/after measurements. |
| 16 | [`COVERAGE.md`](COVERAGE.md) | `MA-017`: owner of every architecture element other than invariants (those are in `INVARIANT_LEDGER.md`). |
| 17 | [`COVERAGE_MATRIX.md`](COVERAGE_MATRIX.md) | Generated by `scripts/plan_coverage.py`: all 70 architecture elements and their owners, and the differences from `SYNC_LOG.md` §3. CI fails if any element is unowned or the file is stale. |

---

## What happens to the existing v1 work

Nothing is deleted. The v1 engine becomes **the frozen substrate baseline (v1.0.0)**:

- All 1,374 passing tests (full CI scope: 1395 collected, 21 env-gated skips; see
  `TRANSITION_AUDIT.md` §1.1) remain as the **Tier-A regression suite**. Every V2
  wave must keep it green, or explicitly record a superseded invariant.
- `H1`–`H4` are **re-homed as substrate experiments** (F1 family). Their *framing*
  changes: `S1` is a cost/benefit frontier, not a superiority test, because the
  proposal itself concedes the superiority is near-tautological by construction.
- `suite T / G / D / I` generators are **extended**. Script-first generation is the
  best asset in the repo and the only honest way to measure abstraction precision
  (`own_P`) and user-model recovery (`own_U`).
- `evaluation/harness`, `judge`, `stats` are **kept and reused** for all new
  experiments. Cluster bootstrap, sign-flip permutation, Holm, and Clopper–Pearson stay —
  and Clopper–Pearson is now also the inference-admission bound, which is the single
  biggest consistency win of r2.

## What is explicitly dropped

- The `layer` enum as a type system; the mandatory `predicate_key` FK; `persona` as a
  layer; `A10` as a layer ablation.
- `turn` as the only evidence type; `user_id` as the only scope.
- `status='archived'` as the retention mechanism.
- `MAX_LINEAGE_DEPTH` as a *correctness* bound.
- r1's mandatory counter-support rule, ECE admission gate, and split-conformal
  verification (all replaced — see `STATISTICS.md` §8).
- The "add reflection as another slow-path step" approach.

See `DECISIONS_V2.md` for the formal supersession of each, and `SYNC_LOG.md` for the
r1→r2 delta.
