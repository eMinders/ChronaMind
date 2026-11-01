# Tenant isolation (`RL-071`, Invariant 7)

Row-level security policies already exist in `schema/001_full_schema.sql`: `ENABLE` and `FORCE ROW
LEVEL SECURITY` on all twelve user-scoped tables (eleven v1 tables and `scope_grant`), each with an `*_isolation` policy on
`current_setting('app.user_id')`. That is only half of Invariant 7. The disposable test database
connects as a superuser, which bypasses RLS, so a green suite proved nothing about isolation. This
package supplies the other half: a restricted runtime role and a service-boundary guard.

| File | Responsibility |
|---|---|
| `rls.sql` | The restricted role (`NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT`), exact per-table least-privilege grants (`provisioning.TABLE_GRANTS`, `V2-D54`: audit events, deletion receipts, turns, sessions, users and evidence are insert-only for the runtime; read-only on the shared `predicate_schema`), and the verification queries |
| `provisioning.py` | Substitutes validated identifiers into `rls.sql`, runs it, then **asserts** forced RLS on every user table and that the role holds no bypass or escalation attribute |
| `scoping.py` | `tenant_scope`: the guard a service layer wraps around engine calls |

## Use

```python
from custom_memory_engine.security import provision_runtime_role, tenant_scope

# Once, as a privileged role (role creation is DDL and is deliberately not part of the migration):
await provision_runtime_role(admin_connection, "cme_engine", "cme_runtime", login_role="cme_service")

# Per request, on an idle connection:
async with tenant_scope(connection, authenticated_user_id, role="cme_runtime", requested_user_id=payload_user):
    ...  # engine calls; app.user_id is set and the role is restricted for this transaction only
```

`tenant_scope`:

* validates the identity **before any statement runs**: missing, blank, non-string, over-long or padded
  ids raise `TenantScopeError` (`22023`); a `requested_user_id` that differs from the authenticated one
  raises `InsufficientPrivilege` (`42501`, `tenant_scope_mismatch`);
* owns the transaction and uses `SET LOCAL ROLE` plus `set_config('app.user_id', ..., true)`, so a pooled
  connection cannot carry a tenant into its next checkout; leaving the block, normally or by exception,
  restores the original role and clears the tenant;
* refuses to run inside an existing transaction (`idle_connection_required`) instead of silently
  changing the scope of work the caller already did.

It is consistent with, not a replacement for, the per-call tenant checks in `engine/deletion.py`,
`engine/transactions.py`, `clock/` and `queue/`, which still require a matching `app.user_id`.

## Evidence

`tests/integration/test_rls_isolation.py` connects through the provisioned role and shows: forced RLS on
all twelve tables; same-user reads succeed; cross-user `SELECT` returns nothing, `UPDATE`/`DELETE` touch
zero rows (or are refused outright on append-only tables) and `INSERT` is rejected; temporal reads and dense, lexical and fused retrieval never return
another tenant's rows; and the Suite `I` development split yields **0 cross-user exposures across 60
probes** (20 scope-mismatch probes rejected), with a positive control showing the same canaries exist for
a connection that bypasses RLS.

`tests/integration/test_tenant_integrity_db.py` (migration 4) shows that another tenant's row cannot be
referenced (indistinguishable from a missing row), cannot pin a user's deletion, that a temporary table cannot
shadow an engine table inside a trigger, and that audit and deletion receipts cannot be rewritten; each has a
control on the pre-migration-4 schema where the attack succeeds. Two catalog rules bind every future
migration: tenant foreign keys carry `user_id`, and every engine function pins its `search_path`.

## Limits

This is a disposable-database role. Production tenant provisioning and credential management are out of
scope. Path-based scopes (`E-011`) will replace the `user_id` predicate; the role, grants and guard are
designed to carry over, and the threat model for that change is `PLAN/V2/THREAT_MODEL.md`.
