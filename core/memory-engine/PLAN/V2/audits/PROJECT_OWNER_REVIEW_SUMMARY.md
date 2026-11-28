# Project Owner Review Summary (r10): AI-assisted

> **What this document is.** An **AI-assisted review performed on the Project Owner's behalf**, under the
> Project Owner's written r10 instruction granting full decision authority over pre-W1 work. It was produced by
> an AI coding agent operating through the `elewashy` GitHub account (write access, not admin). **It is not a human peer review, carries no
> personal signature, and does not record that the Project Owner personally inspected anything.** The human peer
> review required by `PLAN/ASSIGNMENTS.md` has **not** taken place for r9 or r10 changes; nothing here replaces it.
> Every decision below is the AI's, taken under the delegated authority, and is recorded so a human can confirm,
> amend or reverse it.

## 1. What was reviewed

The whole repository at `62de9fe` (end of r9): engine source, schema and migrations, security provisioning,
deletion and leak scanning, queue and clock, evaluation harness, CI/CD workflows, the V2 plan, decision records,
status registry and documentation; plus every open pull request and every W1 issue.

## 2. Evidence examined

Attacks and checks run against PostgreSQL 16.15 under the restricted runtime role; the full test suite (database,
FalkorDB, plain install); recorded evaluation reports re-generated from the frozen model; CI run logs; the
generated status audit; actionlint; the frozen research records. Specific evidence per finding is in
`PRE_W1_INDEPENDENT_AUDIT.md` and `SECURITY_AND_CORRECTNESS_REVIEW.md`.

## 3. Findings and decisions

| Decision | Content | Supersedes or amends |
|---|---|---|
| `V2-D54` | Tenant integrity enforced by the database: composite tenant keys, pinned function `search_path`, exact runtime privileges; server-generated keys at the API | amends the v1 schema's single-column keys and r7 grants |
| `V2-D55` | `RL-072`, `RL-090`, `RL-092` done before W1 rather than deferred; defects they exposed fixed (undeletable (C)-archived memory, audit/log `memory_key` leak, scanner channel-1 replacement, migration-1-only measurement) | reverses the r9 deferral of these tasks |
| `V2-D56` | W1 bound by executable requirements; `RL-091` satisfied by the ledger mechanism; status claims corrected | — |
| Cleanup | six stale or contradictory files deleted; root README rewritten | — |
| Governance | seven unauthorized W1 PRs left unmerged and unmodified, with notes | — |

Earlier decisions re-evaluated and **kept**: `V2-D45` and `V2-D53` (isolation; own-namespace writes),
`V2-D48` (channels), `V2-D49` (migration decisions D6-D13), `V2-D51` (lock order). `V2-D49` is extended by
`MIGRATION_RISK.md` D14 rather than changed.

## 4. Risks eliminated

Cross-tenant references, id-existence oracle and cross-tenant deletion denial (S-01); trigger shadowing (S-02);
audit/receipt rewriting by the runtime (S-03); undeletable consolidated memories (C-01); subject/predicate
retention in audit and logs after deletion (C-02, C-03); a channel-1 measurement that could report clean without
scanning storage (C-04, C-05); engine tests and H4 measured on an unshipped schema (C-06); a gate threshold bug
(C-07); contradictory proposal and architecture documents (P-01); unvalidated workflow files (P-03).

## 5. Remaining limitations

- Owned residual risks T-27 to T-30 and S-08 (`SECURITY_AND_CORRECTNESS_REVIEW.md` §6), none blocking W1.
- No human peer review yet (above).
- The repository has **no branch protection** on `main` (verified: the protection endpoint returns 404); the `elewashy`
  account has write access, not admin, so it cannot enable it. Every r10 merge
  nonetheless waited for all checks on the exact head commit (`--match-head-commit`).
- Seven W1 PRs await an authorization decision.

## 6. For the Project Owner to decide

1. Confirm, amend or reverse `V2-D54`-`V2-D56` and the deletions.
2. Arrange the human peer review required by `PLAN/ASSIGNMENTS.md`.
3. Consider enabling branch protection on `main` (required checks: `static`, `verify`, `default-install`,
   `checks`, `workflows`).
4. Decide when to authorize W1, and whether the open W1 PRs (#101-#104, #106-#108) are its starting point.
