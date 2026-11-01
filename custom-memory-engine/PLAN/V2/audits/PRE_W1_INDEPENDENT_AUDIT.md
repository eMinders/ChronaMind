# Pre-W1 Independent Audit (r10)

**Starting point:** `main` at `62de9fe71c0b5772d6e6b422e3b33a6fd5aa8620` (end of r9). **Mandate:** the Project
Owner's r10 instruction: an independent audit of the whole repository, not relying on earlier AI-generated
reports, with authority to fix everything found inside the pre-W1 scope. **Nature:** AI-assisted audit performed
on the Project Owner's behalf; see `PROJECT_OWNER_REVIEW_SUMMARY.md` for what that does and does not certify.

## 1. Method

Each claim was re-derived from the code, the database or a test run, not taken from a report:

| Area | How it was examined |
|---|---|
| Tenant isolation | Attacks issued through the restricted runtime role (`security.tenant_scope`) and the public engine functions against PostgreSQL 16.15, not as superuser |
| Database invariants | Trigger and constraint definitions read, then attacked (temporary-object shadowing, statement-level vs final-state checks) |
| Privileges | Every `INSERT`/`UPDATE`/`DELETE`/`FOR UPDATE`/`ON CONFLICT` the engine issues, compared with the grants held |
| Deletion and Invariant 8 | Every audit writer, every log call, every storage surface the leak scanner reads, and the scanner's composition logic |
| Invariants 9 and 10 | The missing `RL-090`/`RL-092` gates built and run; whatever they exposed was fixed |
| Test infrastructure | Which schema each integration suite actually builds; whether recorded evaluation reports still reproduce |
| Plans and status | Every `claimed` status recomputed by `scripts/audit_status.py`; stale and contradictory documents searched for and their references checked |
| CI | Workflow text, pinning, permissions, caching (verified from run logs), what is and is not validated |
| Governance | Open pull requests and issues against the W1 boundary |

## 2. Findings

Severity is impact if exploited or left in place. **S** = security, **C** = correctness, **P** = plan/process.

| ID | Severity | Finding (measured) | Resolution |
|---|---|---|---|
| S-01 | **Critical** | PostgreSQL referential checks bypass RLS and every key was on `id` alone. Through `commit_initial_version` under the restricted role, tenant `u2` cited `u1`'s turn as evidence, derived from `u1`'s version, learned whether any id exists, and **blocked `u1`'s own `delete_memory(scope='all')`** with a `ForeignKeyViolation` `u1` can neither see nor clear | Fixed, migration 4 (`V2-D54`, #105): composite `(id, user_id)` keys; catalog rule binds future migrations |
| S-02 | High | Trigger functions resolved tables through the caller's `search_path`; a temporary `memory_evidence` table shadowed the real one inside `enforce_provenance`, and an `active` version with **no evidence** committed | Fixed, migration 4: every engine function pinned to `<schema>, pg_catalog, pg_temp`; catalog rule |
| S-03 | Medium | Runtime role held `UPDATE`/`DELETE` on every table: it could rewrite audit history, erase deletion receipts, edit turns and delete grants (migration 2 promises grant history is append-only) | Fixed: exact per-table matrix (`TABLE_GRANTS` = `rls.sql`), verified on provision |
| S-04 | Low | Global caller-chosen primary keys: `UniqueViolation` reveals an id is in use (and the P engine's ids are predictable, `turn_id:index`) | `E-039` requirement: server-generated keys (THREAT_MODEL T-27) |
| S-05 | Medium | `clock_now()` trusts the session setting `app.clock`; monotonicity is enforced in Python only, so a compromised runtime can backdate recorded time | `E-012` requirement: database-side watermark (T-28) |
| S-06 | Low | `deletion_request.target_ref` keeps the deleted key/topic in plaintext (spec-mandated column) | `E-037` requirement: keyed digest (T-29) |
| S-07 | Low | `scope_grant.principal_id` reveals user-id existence to a namespace owner | `E-039` requirement (T-30) |
| S-08 | Medium | Proposal §7.7 asks the forget action to offer deletion of a memory's exclusive evidence turns; v1 never deletes turns | `E-037` acceptance criterion (accepted risk recorded) |
| C-01 | **High** | **A memory archived via branch (C) could not be deleted**: `delete_memory` raised `archival_breaks_covered_source_invariant` because the archival constraint triggers judged each statement, not the deletion's final state. This is the `T5` contract row and the right to deletion | Fixed (`V2-D55`, #109): triggers deferred inside deletion and re-checked `IMMEDIATE` before return; deleting only the replacement is still refused |
| C-02 | Medium | `version_committed` audited `memory_key`, which the P engine builds as `subject:predicate`; audit rows survive deletion, so the subject and predicate of deleted memories persisted (spec §7.2: "no content, hashes, subjects or predicates") | Fixed: allowlist guard on every audit writer; static rules |
| C-03 | Low | The consolidator logged `memory_key` (Channel 7) | Fixed; static rule over every logger call |
| C-04 | Medium | `scan_deleted_key` let a caller's channel-1 probe **replace** the SQL storage scan: supplying a cache probe reported channel 1 "complete, zero" without scanning storage. `test_normal_deletion_measures_zero_on_all_seven_channels` was vacuous for channel 1 | Fixed: the caller's surface supplements storage. The Suite D harness already composed both, so recorded H4 results stand; `reports/deletion/suite_d_dev_{P,A8}.json` re-run **byte-identically** |
| C-05 | Medium | Channel 1 did not scan `audit_event` at all | Fixed (canary in details or `memory_ref` is counted) |
| C-06 | Medium | Nine integration suites and the Suite D harness built **migration 1 only**: engine behaviour and H4 were measured on a schema production never runs, and each new migration would have gone untested | Fixed: one source, `migrate.migration_scripts()`; `tests/integration/schema.py` |
| C-07 | Low | Gate: a per-predicate confidence threshold of `0.0` was overridden by the global `*` threshold (`or` on a falsy float); dead `classify_assertion` parameters | Fixed (#110) with a regression test; no recorded run uses `0.0` |
| C-08 | Low | Migration runner logged the module constant `VERSION`, not the applied head; the "upgrade" test faked an old schema by dropping objects instead of upgrading a real migration-1 install | Fixed (#105) |
| P-01 | Medium | Contradictory or stale files: a pre-V1 proposal draft differing from the frozen proposal; a blueprint building ChronaMind inside the product app (contradicts `INDEPENDENCE_BOUNDARY.md` and V2); advertisement assets and README block; unwired templates whose PR template prescribed issue-closing keywords (the cause of the r8 accidental close) | Deleted (#110), references repointed |
| P-02 | Low | Status claims vs evidence: `RL-071` claimed `Open` though #47 closed; `RL-005` unverifiable locally; `RL-091` duplicative of the ledger mechanism | Corrected; `tests/test_ci_workflow.py`; `V2-D56`. `audit_status`: 0 `claimed-unverified`, no discrepancies |
| P-03 | Medium | CI never validated workflow files themselves | `workflows` job (actionlint, pinned by digest) |
| P-04 | **Governance** | Seven W1 implementation PRs were opened by the repository author during this audit (#101-#104, #106-#108), unauthorized; #106-#108 also claim migration numbers `004`-`006`, colliding with `main` | **Not merged, not modified.** Each carries a note on the authorization status and the rebase requirements (`005+`, catalog rules, privilege matrix) |

Defects were not seeded or assumed: each was reproduced first, and each fix is pinned by a test that **fails on
the pre-fix code** (a control or a mutation run), listed in `SECURITY_AND_CORRECTNESS_REVIEW.md`.

## 3. What was verified and found sound

- G0: annotated tag `cme-v1.0.0` resolves to `490904d632518b86e0f52ced1f3266b895c3c36b` on `origin`.
- G0b clauses (`ROADMAP_V2.md` §3) hold on the new code: decisions `V2-D35`-`V2-D56` exist and are verified;
  `MA-011`'s isolation suite still fails on the flawed designs and passes on the corrected one; `API_V2.md` and
  `CHANNELS.md` frozen; `plan_coverage.py --check` 0 unowned; `check_plan_refs.py` 0 findings.
- The r9 primitives (`scope.access`, `scope.locks`, `model.axes`, `api.contract`): policy expressions bind tables
  at `CREATE POLICY` time (no shadowing); `authorize_write` own-namespace only; exhaustive lock-order proof holds.
- Frozen research records: `validation/document_hashes.txt` 5/5; mechanical checks pass; CP bounds identical.
- CI caching works (uv cache hits in run logs; `static` 26 s, `verify` about 2.5 min); no change made.
- Dead-code scan (`vulture`, import graph): no unreferenced engine module; the hits are pydantic validators,
  dataclass fields and entry points, except the gate parameters above.

## 4. Not changed, deliberately

- `PLAN/issue_drafts/`: the only full specifications of unpublished research-protocol tasks (`RL-100`, `RL-105`);
  still governed by the frozen proposal.
- Historical audits in `PLAN/V2/audits/`: they are the evidence cited by `V2-D35`-`V2-D53`; marked historical in
  the index rather than moved, so no decision loses its citation.
- The product application (`app/`, `evals/`): outside the engine's scope; only its README advertisement removed.
- v1 deletion's retention of turns: frozen protocol, measured by Suite D; the V2 change is owned by `E-037`.
