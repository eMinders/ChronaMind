# r9 — Pre-W1 Completion Report (G0 and G0b)

**Date:** 2026-11-01 · **Authority:** Project Owner instruction recorded as `APPROVAL.md` Amendment 2 ·
**Recorded by:** `elewashy` (maintainer session; AI-assisted, which does not replace the human peer review in
`PLAN/ASSIGNMENTS.md`) · **Decisions:** `DECISIONS_V2.md` §6, `V2-D44`-`V2-D53`.

**Outcome.** G0 and G0b are met. **W1 has not started**: no W1 task (`E-010`-`E-020`, `E-039`) has been
implemented, no schema migration was added, and no engine runtime read/write path changed. W1 starts only on a
separate, explicit Project Owner instruction.

---

## 1. Issues completed, with evidence

| Issue | Task | Closed by | Evidence |
|---|---|---|---|
| #51 | E-002 baseline | tag + PR #93 | Annotated tag `cme-v1.0.0` → `490904d632518b86e0f52ced1f3266b895c3c36b` on `origin`; decisions in `BASELINE.md` §7.1 (`V2-D44`); figures reproduced at that commit in r8 (#88) |
| #52 | E-004 invariant reachability | PR #93 | Criterion 3 superseded (`V2-D44`): #46/#47 closed those halves; `test_gaps_closed_in_r7_are_enforced_with_tests`, `test_known_v1_gaps_are_reported_not_silently_passed` |
| #54 | MA-011 scope isolation | PR #94 | Shared cases; r2 `LIKE` design fails case 1 (and 5), `LIKE` with separator fails 5, `starts_with` passes all (pure and PostgreSQL 16); RLS layer tested alone; app read set ⊆ ACL (exhaustive) |
| #55 | MA-012 API surface | PR #95 | 29 operations frozen (`V2-D46`); every operation name in ROADMAP/ARCHITECTURE checked against `API_V2.md` by `check_plan_refs.py` |
| #56 | MA-013 channel contract | PR #95 | C-P1..C-P6 decided (`V2-D48`); C-P1 implemented in `model.axes` (reachable space = legal space); C-P4 security rule on the matrix and in the model; `CHANNELS.md` frozen |
| #58 | MA-015 tier matrix | PR #95 | Tier B / Tier C / Admin + condition codes per row; TB1-TB10, AD1, each failing on a broken copy (`tests/test_tier_permissions.py`) |
| #60 | MA-017 plan coverage | PR #98 | 70 elements extracted mechanically, 0 unowned; mutations name the fake element; `--check` in CI |

Earlier in r8 (for completeness): #50, #53, #57, #59, #77, #78 closed with their PRs. New: **#97 (`E-039`)**
created in the W1 milestone, planning only.

## 2. Architecture and code changes

All code is pure contract/primitive code with tests, of the kind Amendment 1 already allowed; none is wired into
a runtime path.

| Area | Change |
|---|---|
| `scope.access` | `exact`/`subtree`/`with_visibility` selector modes; `authorize_write` (own namespace only, `V2-D53`); `acl_readable` (reference) and `rls_read_using` (the RLS `USING` expression `E-011` attaches; ACL-only, independent of the application) |
| `scope.locks` (new) | `serialisation_key` (owner namespace, `I9` for W1), `lock_order` (global UTF-8 order), `must_retry` |
| `model.axes` | `LEGAL_LIFECYCLE`; `require_transition(source, target, kind)` and `validate_axes` reject kind-illegal lifecycles |
| `api.contract` | 29 operations; `Condition` codes per row; `admin_only` + `authorize(..., admin=)`; `contract_violations` (TB1-TB10, AD1) |
| `scripts/check_plan_refs.py` | Operation-name check (call forms and retired names) against `API_V2.md` |
| `scripts/plan_coverage.py` (new) | MA-017 extraction, ownership validation, generated `COVERAGE_MATRIX.md`, `--check` |
| CI | Static job runs `plan_coverage.py --check`; ruff, format and pyright now also cover `scripts/` (three real type errors fixed in `bench_retrieval.py`) |
| Plan | `API_V2.md`, `CHANNELS.md` frozen; `ARCHITECTURE_V2.md` §5, §5.1, §8.1, §9.1, §10, §11 `I3`, §12 M2 amended with r9 notes; `MIGRATION_RISK.md` all decisions approved, D13 added; `DELETION_ORDER.md` §7 decided; new `COVERAGE.md`, `COVERAGE_MATRIX.md`; `ROADMAP_V2.md` `E-039`, G0/G0b notes |

## 3. Security, migration, concurrency and API decisions

| Decision | Content | Kind |
|---|---|---|
| `V2-D44` | `cme-v1.0.0` at `490904d`; E-004 criterion 3 superseded | baseline |
| `V2-D45` | Scope isolation frozen; **RLS enforces the ACL only** (selector GUC dropped); exact selector; M2 needs valid user-segment ids (D13) | security |
| `V2-D46` | `dissent`, `create_entity`, `register_method`, Scope class; `move`/revise compositions | API |
| `V2-D47` | Two tiers; admin is a Tier C role; condition codes on the matrix | API/security |
| `V2-D48` | C-P1..C-P6; **C-P4: Tier B cannot demote a fact via `contradicts`**; MA-013 frozen | channels/security |
| `V2-D49` | Migration D6-D12 (D8 made total; D11 stored `user_id`; D12 per-user job idempotency) | migration |
| `V2-D50` | Transitive re-derivation; all dependents re-derived; **retire-by-deletion tombstones content** | deletion |
| `V2-D51` | Global lock order; `I9` key = owner namespace in W1 | concurrency |
| `V2-D52` | Mechanical plan coverage; `E-039` public API entry point | plan |
| `V2-D53` | **Grants are read-only**: writes and deletions only in the caller's own namespace | security |

**Defects found and fixed during this round** (each pinned by a test that fails on the defective form):
1. *RLS column capture* (in my own draft of `rls_read_using`): an unqualified `scope_path` inside the grant
   sub-query bound to `scope_grant.scope_path`, so any grantee read every tenant's project/shared rows.
   Qualification is mandatory; `test_the_unqualified_policy_column_leaks_which_is_why_it_is_qualified`.
2. *Grantee writes and deletes* (in my own #94 write rule): fixed by `V2-D53`;
   `test_a_grant_never_authorizes_a_write_into_the_owners_namespace`.
3. *M2 path forgery*: v1 user ids may contain `/` (D13 preflight + `CHECK`).
4. *Kind-blind lifecycle*: `inferred → contested → asserted` could make an abstraction a fact (C-P1).
5. *Tier B fact demotion* through `link(contradicts)` (C-P4).
6. *Cross-tenant idempotency oracle* observed by a v1 test (D12).
7. *Unowned API entry point* (`E-039`), and *stale plan text* (`UM-012` `refuted(...)`, `I3` `INFERENCE_METHOD`,
   §12 M2 "generated column", §9.1 overlap), found by the new checks.

## 4. Tests and CI

| Check | Result |
|---|---|
| Full CI scope (fresh clone of the final `main`, disposable PostgreSQL 16 + FalkorDB) | see §9 |
| Default install, no database | see §9 |
| ruff, ruff format, pyright (now including `scripts/`) | clean |
| `md5sum -c validation/document_hashes.txt` | 5/5 OK (frozen research records untouched) |
| mechanical checks, CP bounds, statistics, G-TS integrity | pass |
| `check_plan_refs.py`, `plan_coverage.py --check`, `audit_status.py --check` | 0 findings |
| actionlint | clean |
| GitHub: every PR merged only with all checks green on its exact head; post-merge `main` CI | green |

Test count grew from 1,961 (r8 end) to 2,098 before this PR. New suites: `test_scope_isolation` (pure + DB),
`test_tier_permissions`, `test_scope_locks`, `test_plan_coverage`; extended: `test_model_axes`,
`test_channel_membership_model`, `test_api_contract`, `test_check_plan_refs`.

## 5. Pull requests and issues

| PR | Title | Merge |
|---|---|---|
| #93 | W0 closeout: cme-v1.0.0 decisions, E-004 criterion 3 | `1f34d45` |
| #94 | MA-011 isolation suite; RLS enforces the ACL | `5a9520b` |
| #95 | Contract freeze: MA-012, MA-013, MA-015 | `68408fb` |
| #96 | MA-014/MA-016 decisions; global lock order | `cf938b2` |
| #98 | MA-017 plan coverage | `9596516` |
| #99 | Grants are read-only | `a2b5f8f` |
| this PR | G0b declaration and this report | see §9 |

Issues closed: #51, #52, #54, #55, #56, #58, #60. Created: #97. Milestones closed: W0, W0b, and the eight
empty v1-era milestones `P0`-`P7`. W1 issues #61-#63, #65-#69 received comments listing the r9 decisions that bind
them (bodies unchanged).

## 6. Remaining risks, limitations and deferred work

None blocks W1; each has an owner.

| Item | Status | Owner |
|---|---|---|
| v1 `Invariant 8` content-free audit: the `CHECK` blacklists three top-level keys only; no redaction guard (tenant-internal, T-23) | `UNENFORCED`, documented | `RL-072` |
| v1 `Invariant 9` end-to-end replay gate; `Invariant 10` standalone T1-T5 harness | `UNENFORCED`, documented | `RL-092`, `RL-090` |
| V2 invariants other than `I1′`, `I7`, `I12`, `I17` | `PLANNED` (their mechanisms are W1+ tasks) | ledger owners |
| Decisions not needed before W1: `V2-D14`, `D16`, `D17`, `D30`, `D31`, `D32`, `D34` | open by design | W4/W5/W7 |
| `M5` | deferred (`V2-D43`) | revisit with `E-021` |
| Every primitive here is unwired | by design; wiring is W1 | `E-011`, `E-016`, `E-017`, `E-037`, `E-039` |
| Peer review | the designated human peer review of these changes has not happened | Project Owner |

## 7. G0b readiness

**Met.** Clause-by-clause evidence is in `ROADMAP_V2.md` §3 (r9 note). G0 was met first (§2 r9 note).

## 8. W1 not started — confirmation

`git diff --stat 8b18560 HEAD -- custom-memory-engine/src` (r8 end → this round) lists exactly four files:
`api/contract.py`, `model/axes.py`, `scope/access.py` and the new `scope/locks.py`, all pure modules with no I/O on
a runtime path. Nothing under `schema/`, `engine/`, `queue/`, `clock/`, `deletion/`, `grounding/`, `soundness/`,
`consolidation/` or `security/` changed. Since the baseline, `schema/` differs from `490904d` only by r7's additive
migrations 2 and 3 and its multi-version runner. No W1 issue was started; their bodies were not edited.

## 9. Final verification

Recorded in `SYNC_LOG.md` §15 after this PR merged, with the exact final `main` commit.
