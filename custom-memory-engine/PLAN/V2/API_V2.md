# Public API Surface (v2.0, frozen)

**Task:** `MA-012` (with `MA-015`) · **Status: frozen (r9).** Canonical since r7 (`V2-D39`, which resolves the
`V2-D07` five-call contradiction and retires `retract`); completed by `V2-D46` (the section 5 gaps) and
`V2-D47` (tiers and admin). Executable form: `custom_memory_engine.api.contract`, held equal to this document and
to `ARCHITECTURE_V2.md` section 8.1 by `tests/test_api_contract.py` and `tests/test_tier_permissions.py`. A change
to the surface needs a new `V2-Dnn`.
**Extended by:** `MA-015` (the tier matrix check and `tests/test_tier_permissions.py`), `MA-011` (the scope
selector table, section 6). **References:** `ARCHITECTURE_V2.md` sections 5 and 8, `V2-D07`, `V2-D39`,
`V2-D45`-`V2-D48`,
`CHARTER.md` section 1, `THREAT_MODEL.md`, `CHANNELS.md`.

`ARCHITECTURE_V2.md` section 8 states the principle: **every operation a tier needs appears in one list, and
nothing outside it exists.** This document is that list. It freezes, for each operation, a signature, the tiers
that may call it, and an error contract.

**What is normative and what is proposed.** The architecture fixes the operation *names*, their *capability
class*, the *tier permissions* and the *Tier B rules*. Those are carried here unchanged. It gives no signatures
or error codes: those are this document's proposal and become binding when it is approved. Section 5 lists the
operations other V2 documents rely on that section 8 does not contain; they are **not** in the frozen surface
until a decision record adds them.

## 1. Conventions

| Topic | Rule |
|---|---|
| **Caller context** | Every call receives `ctx: CallerContext` carrying `principal`, `tier` (`B` or `C`), `write_scope` and `read_scopes` (at most 16 selectors, `ARCHITECTURE_V2.md` 5.1). The **tier comes from the authenticated credential, never from a request field.** |
| **Time** | No operation accepts a belief-time (`recorded_*`) value; it is set by the transaction clock only (`I4`). Event time is accepted where meaningful. |
| **Idempotency** | Write operations accept `idempotency_key: str`; a replay returns the original result and never a second effect (`I9`). |
| **Identifiers** | `ArtifactId`, `AssociationId`, `EntityId` and `ScopePath` are opaque strings the engine creates. Callers never choose them. |
| **Scope** | A write lands in exactly one scope the caller may write; a read uses the union of selectors intersected with the caller's access. Nothing is implicit (`ARCHITECTURE_V2.md` 5, rules 1-2). |
| **Retries** | Only `retryable_conflict` (SQLSTATE `40001`/`40P01`) is retried, by whole transaction, with bounded backoff, as in v1. |

### 1.1 Error contract

Errors carry a **static code**. A message never contains an identifier, a path, a scope or content.

| Code | Meaning | SQLSTATE class |
|---|---|---|
| `validation_error` | Malformed or out-of-range input. Carries a static sub-code, e.g. `nonblank_user_id_required`. | `22023` |
| `forbidden` | Not permitted: wrong tier, or the target is absent **or** unreadable. **The two are indistinguishable by design** (`THREAT_MODEL.md` T-11, T-18). | `42501` |
| `precondition_failed` | The state does not allow the operation (e.g. `adjudicate` on a non-`contested` artifact, an illegal lifecycle transition). | `23xxx` / `P0001` |
| `rejected` | The admission policy declined the write, with a reason code from the gate or the soundness policy. The decision is recorded. | n/a (a result, not an exception, for `assert`/`infer`) |
| `resource_limit` | A declared bound was exceeded (selector count, budget, page size). Never a silent truncation, and never discloses tenant data (`T-16`). | `54000` |
| `retryable_conflict` | Serialisation failure or deadlock. | `40001` / `40P01` |

## 2. Shared types

```text
SourceRef      { type: 'turn'|'tool'|'file'|'paper'|'event'|'external', ref: str, span?: str, content_hash: str }
ScopeSelector  exact(path) | subtree(path) | with_visibility(path, {project, shared})      # section 6
QueryRequest   { text, intent?, mode: current|historical_as_of|historical_window|change|audit_as_of,
                 target_time?, belief_time?, selectors: ScopeSelector[], budget: ChannelBudgets, limit? }
QueryResult    { facts[], hypotheses[], procedures[], conflicts[], provenance_bundle, cost, coverage }   # CHANNELS.md
Receipt        { id, effect, recorded_at }     # content-free
```

## 3. Operations and tiers (the tier matrix, `MA-015`)

`✓` = permitted, `—` = not permitted (calling it is `forbidden`). **29 operations, eleven capability classes**:
the 23 of `ARCHITECTURE_V2.md` 8.1 as approved (`V2-D39`) plus the six added by `V2-D46` (`dissent`,
`create_entity`, `register_method`, `create_scope`, `grant_access`, `revoke_access`). 18 are open to Tier B.

**Tiers (`V2-D47`).** There are two tiers, taken from the authenticated credential. **Admin is a role within
Tier C**, not a third tier: an admin-only operation (`Admin` ✓, `Tier C` —) needs a Tier C credential that holds
the admin role; an admin may call every Tier C operation. Tier B never holds the admin role.

**Conditions** apply to every tier that may call the operation; each code is defined in
`custom_memory_engine.api.contract.Condition` and must be enforced by the implementing task:
`evidence_required` (non-empty source evidence), `own_lineage_only` (Tier B: only artifacts its own method
lineage produced; Tier C: its own assertions), `scope_writable` (`scope.access.authorize_write`),
`targets_readable` (every named artifact, association, entity or result is in the caller's read set; otherwise
`forbidden`, indistinguishable from absent), `registered_method` (the method is in the `METHOD` registry),
`namespace_owner_only` (the scope is inside the caller's own `/u/<caller>/`), `grounded_endpoint_protected`
(a Tier B `contradicts` edge never demotes a grounded artifact, `V2-D48`).

| # | Class | Operation | Tier B | Tier C | Admin | Conditions |
|---|---|---|:-:|:-:|:-:|---|
| 1 | Ingest | `ingest_episode` | ✓ | ✓ | ✓ | `scope_writable` |
| 2 | Ingest | `ingest_artifact` | ✓ | ✓ | ✓ | `evidence_required`, `scope_writable` |
| 3 | Read | `query` | ✓ | ✓ | ✓ | — |
| 4 | Read | `refine` | ✓ | ✓ | ✓ | `targets_readable` |
| 5 | Read | `get` | ✓ | ✓ | ✓ | `targets_readable` |
| 6 | Read | `provenance` | ✓ | ✓ | ✓ | `targets_readable` |
| 7 | Write (grounded) | `assert` | ✓ | ✓ | ✓ | `evidence_required`, `scope_writable` |
| 8 | Write (grounded) | `retract_own` | ✓ | ✓ | ✓ | `own_lineage_only`, `targets_readable` |
| 9 | Write (inferred) | `infer` | ✓ | — | — | `registered_method`, `scope_writable`, `targets_readable` |
| 10 | Write (inferred) | `verify` | ✓ | — | — | `own_lineage_only`, `targets_readable` |
| 11 | Write (inferred) | `refute` | ✓ | — | — | `evidence_required`, `own_lineage_only`, `targets_readable` |
| 12 | Structure | `link` | ✓ | ✓ | ✓ | `grounded_endpoint_protected`, `targets_readable` |
| 13 | Structure | `unlink` | ✓ | ✓ | ✓ | `targets_readable` |
| 14 | Structure | `create_entity` | ✓ | ✓ | ✓ | `scope_writable` |
| 15 | Structure | `link_entity` | ✓ | ✓ | ✓ | `targets_readable` |
| 16 | Structure | `record_outcome` | ✓ | ✓ | ✓ | `targets_readable` |
| 17 | Adjudicate | `adjudicate` | — | ✓ | ✓ | `targets_readable` |
| 18 | Adjudicate | `dissent` | — | ✓ | ✓ | `targets_readable` |
| 19 | Maintain | `purge_working` | — | — | ✓ | `scope_writable` |
| 20 | Maintain | `expire` | — | — | ✓ | `scope_writable` |
| 21 | Maintain | `rebuild_index` | — | — | ✓ | — |
| 22 | Maintain | `register_method` | — | — | ✓ | — |
| 23 | Delete | `delete` | — | ✓ | ✓ | `scope_writable` |
| 24 | Export | `export` | — | ✓ | ✓ | — |
| 25 | Scope | `create_scope` | — | ✓ | ✓ | `namespace_owner_only` |
| 26 | Scope | `grant_access` | — | ✓ | ✓ | `namespace_owner_only` |
| 27 | Scope | `revoke_access` | — | ✓ | ✓ | `namespace_owner_only` |
| 28 | Observe | `stats` | ✓ | ✓ | ✓ | `targets_readable` |
| 29 | Observe | `cost` | ✓ | ✓ | ✓ | — |

### 3.1 The structural rules (`MA-015`)

Each rule is a static code returned by `api.contract.contract_violations`. `tests/test_tier_permissions.py`
checks it on the real matrix **and** shows it failing on a deliberately broken copy. These tests check the
**specification**, not runtime enforcement: the implementing tasks (`E-014`-`E-017`, `A-017`) enforce it at
runtime by calling `api.contract.authorize` and honouring each condition.

| ID | Rule | Test |
|---|---|---|
| TB1 | **Tier B cannot delete.** It may only `retract_own` an artifact of its own method lineage. | `test_tb1_tier_b_cannot_delete` |
| TB2 | **Tier B cannot adjudicate** (`adjudicate`, `dissent`). A reflection service must not silence a conflict or a user's dissent. | `test_tb2_tier_b_cannot_adjudicate` |
| TB3 | Every operation that creates `asserted` requires source evidence (Tier B and Tier C alike). | `test_tb3_grounded_writes_require_evidence` |
| TB4 | Nothing moves to `asserted` except Tier C adjudication of a `contested` item; `inferred → asserted` does not exist. | `test_tb4_only_tier_c_adjudication_promotes_to_asserted` |
| TB5 | A Tier B lifecycle change (`retract_own`, `verify`, `refute`) applies to its own method lineage only. | `test_tb5_tier_b_lifecycle_changes_are_own_lineage_only` |
| TB6 | Tier B can `infer` and `refute`. | `test_tb6_tier_b_can_infer_and_refute` |
| TB7 | A Tier B operation that creates an artifact requires a writable scope. | `test_tb7_tier_b_creation_requires_writable_scope` |
| TB8 | Every operation is assigned to at least one tier, and the matrix equals this document and `ARCHITECTURE_V2.md` 8.1. | `test_tb8_every_operation_is_assigned_and_documented` |
| TB9 | Tier B has no Maintain, Export or Scope operation: it cannot change access, bulk-read or administer. | `test_tb9_tier_b_has_no_maintain_export_or_scope_operation` |
| TB10 | A Tier B operation that names existing artifacts requires them to be readable. | `test_tb10_tier_b_targets_must_be_readable` |
| AD1 | Admin-only operations are Tier C operations, and need the admin role. | `test_ad1_admin_only_operations_need_tier_c_and_the_role` |

## 4. Operation reference

For each operation: **signature**, **rules**, **errors** (in addition to the universal `forbidden` for a tier
that is not permitted, and `validation_error`).

### 4.1 Ingest

**`ingest_episode(ctx, scope, source: SourceRef, content: str, event_time?, idempotency_key) → Receipt`**
Creates a `kind='episode'`, `lifecycle='observed'` artifact with `observed_at` from the clock. The source may be
`external`; external content is outside the deletion boundary and is recorded as such. *Errors:* `validation_error`
(empty content, bad source), `forbidden` (scope not writable).

**`ingest_artifact(ctx, scope, kind: claim|procedure, candidate, evidence: SourceRef[], idempotency_key) → Receipt | rejected`**
Submits an externally formed candidate (for example extractor output) to the **grounding policy**. Outcome:
`asserted`, `contested`, or `rejected` with a gate reason code; the decision is recorded. *The architecture names
this operation without defining its payload; this is the proposed meaning (see 5.3).* *Errors:* as above, plus
`rejected`.

### 4.2 Read

**`query(ctx, request: QueryRequest) → QueryResult`**
Staged retrieval (`ARCHITECTURE_V2.md` 9). Returns the four channels, a provenance bundle, per-stage cost and
per-channel coverage. The temporal predicate is a filter. `mode='audit_as_of'` requires `belief_time ≤ clock_now()`
and is **Tier C only** and **never returns answer-context material**, as in v1 (`TECHNICAL_SPECIFICATION.md`
3.5); Tier B calling it is `forbidden`. *Errors:* `validation_error` (future target time, `t > ρ`),
`resource_limit` (more than 16 selectors, budget).

**`refine(ctx, result_id, instruction: str) → QueryResult`**
Drill-down on a prior result within the same read set; cannot widen it. *Errors:* `forbidden` (unknown or
foreign `result_id`), `validation_error`.

**`get(ctx, artifact_id) → Artifact`** and **`provenance(ctx, artifact_id, depth?) → ProvenanceView`**
Return one artifact or its provenance. A source in a scope the caller cannot read appears as an opaque reference
only (`CHANNELS.md` R6, `THREAT_MODEL.md` T-10). *Errors:* `forbidden` for absent **and** unreadable alike.

### 4.3 Write (grounded)

**`assert(ctx, scope, kind, content|predicate, evidence: SourceRef[], event_range, idempotency_key) → Receipt | rejected`**
A grounded statement through the grounding policy. **`evidence` must be non-empty for Tier B and Tier C
alike** (TB3). Result lifecycle: `asserted` or `contested`. *Errors:* `validation_error` (no evidence, bad
range), `rejected` (instruction screen, assertion mode, conflict), `forbidden`.

**`retract_own(ctx, artifact_id, reason) → Receipt`**
Moves an artifact to `retired` (recorded-time close). Tier B: only artifacts **its own method produced**. Tier C:
its own assertions. It is not deletion: the retired artifact stays auditable. *Errors:* `forbidden` (not yours,
or absent), `precondition_failed` (already retired).

### 4.4 Write (inferred) — Tier B only

**`infer(ctx, scope, method: {id, version, params_hash}, sources: ArtifactId[], statement, predicate?, ledger: {support, counter, undetermined, opportunity}, idempotency_key) → Receipt | rejected`**
Submits to the **soundness policy**. Requires at least one readable source, a registered method, and a
complete opportunity ledger (`STATISTICS.md`). Creates `lifecycle='inferred'`; **no parameter yields
`asserted`**. *Errors:* `rejected` (below the confidence floor, coverage gate, missing ledger),
`validation_error`, `forbidden` (unreadable source or scope).

**`verify(ctx, artifact_id, verification: VerificationRecord) → Receipt`**
Posts a held-out verification result. Accepted only for an artifact whose method lineage the caller owns, with
a split disjoint from the support set. *Errors:* `precondition_failed` (not inferred, or split not disjoint),
`forbidden`.

**`refute(ctx, artifact_id, counter_evidence: SourceRef[], reason) → Receipt`**
Moves an inferred artifact to `contested` (grounded counter-evidence) or `retired` (refuted). Evidence is
required. It is the only Tier B way to change an inferred artifact's lifecycle. *Errors:* `precondition_failed`,
`validation_error` (no evidence), `forbidden`.

### 4.5 Structure

**`link(ctx, from, to, type, weight?) → AssociationId`** · **`unlink(ctx, association_id) → Receipt`**
Association edges (`related_to`, `contradicts`, `refines`, `specialises`, `instance_of`, `caused_by`,
`precedes`, `part_of`, `corroborated_by`, `supersedes`). Associations are **not provenance** and do not cascade.
Both endpoints must be readable and the caller must be authorised on the endpoint scopes. **`contradicts`
(`V2-D48`, C-P4):** only an edge between two grounded endpoints created by the grounding policy or a Tier C
caller opens a contradiction (both endpoints then render in `conflicts`). A Tier B `contradicts` edge is stored
as an association with `origin='tier_b'` and has **no** channel effect; a Tier B caller with counter-evidence
against its own inference uses `refute`. *Errors:* `forbidden`, `validation_error` (unknown type).

**`create_entity(ctx, scope, entity_type, label, idempotency_key) → EntityId`** (`V2-D46`)
Creates a principal-scoped, opaque entity (`ARCHITECTURE_V2.md` 5.2, `V2-D25`) in a scope the caller may write.
The `EntityId` is engine-generated; `label` is content and follows the scope's read rules. A replay with the same
key returns the same id. *Errors:* `forbidden` (scope not writable), `validation_error`.

**`link_entity(ctx, entity_a, entity_b, relation) → Receipt`**
Links two entities and requires authorisation on **both** scopes; otherwise it fails with the same `forbidden`
as a missing entity (`THREAT_MODEL.md` T-09).

**`record_outcome(ctx, procedure_id, outcome: success|failure, cost?, episode_ref?) → OutcomeStats`**
Retrospective only; updates `outcome_stats`. There is no execution. *Errors:* `precondition_failed` (not a
procedure), `forbidden`.

### 4.6 Adjudicate — Tier C only

**`adjudicate(ctx, artifact_id, decision: confirm|deny, note?) → Receipt`**
Resolves a `contested` artifact: `confirm → asserted`, `deny → retired`, with an adjudication record.
`confirm` is legal only for kinds that may be `asserted` (claim, procedure; `V2-D48`): a contested abstraction or
episode can only be denied. *Errors:* `precondition_failed` (artifact not `contested`, or `confirm` on a kind that
cannot be asserted), `forbidden` (including every Tier B call, TB2).

**`dissent(ctx, artifact_id, note?) → Receipt`** (`V2-D46`)
The user rejects an inference (`USER_MODEL.md` §3 "Contest", `V2-D33`): `inferred → retired` with
`reason='user_dissent'`, and a `NEGATIVE_CONSTRAINT` recorded so the same element cannot be silently re-learned
(`I16`). No evidence is required: the user is the authority over their own model. Tier C only (TB2): a cognition
service must not dissent on the user's behalf. *Errors:* `precondition_failed` (not `inferred`), `forbidden`.

### 4.7 Maintain — Tier C only

**`purge_working(ctx, scope) → Receipt`** — deletes `retention='working'` episodes of one scope, audited; durable
claims and abstractions are untouched (retention floors). **`expire(ctx, scope, as_of?) → Receipt`** — applies
retention expiry; recorded as a scope event, not a deletion of knowledge. **`rebuild_index(ctx, index:
fts|vector|closure_hash) → JobId`** — queues an index rebuild. *Errors:* `forbidden`, `validation_error`.
All Maintain operations are **admin-only** (`V2-D47`).

**`register_method(ctx, name, version, params_schema_hash, description, idempotency_key) → MethodId`** (`V2-D46`)
Appends an inference method to the immutable `METHOD` registry (`I3`); `infer` accepts only registered methods.
`(name, version)` is unique and never updated: re-registering it with the same hash returns the existing id,
with a different hash is `precondition_failed`. Admin-only.

### 4.8 Delete — Tier C only

**`delete(ctx, scope: memory|topic|all, target_ref, deletion_epoch?) → DeletionReceipt`**
Transactional lineage deletion (`DELETION_ORDER.md`). The receipt is content-free: counts only. A replay with
the same `deletion_epoch` returns the original receipt (v1 behaviour). *Errors:* `forbidden` (including every
Tier B call, TB1), `validation_error`, `retryable_conflict`.

### 4.9 Export — Tier C only

**`export(ctx, selectors: ScopeSelector[]) → ExportSnapshot`**
User-facing data export of the caller's read set; excludes content whose deletion is pending. A snapshot made
before a deletion must not be served after it (channel 11). *Errors:* `forbidden`, `resource_limit`.

### 4.10 Scope — Tier C only (`V2-D46`)

Scope administration is part of the engine API, because it decides every read set (`ARCHITECTURE_V2.md` 8:
"nothing outside it exists"). Tier B has none of it (TB9).

**`create_scope(ctx, parent, segment, kind, idempotency_key) → ScopePath`** — creates `parent + segment + '/'`
with `kind ∈ {project, session, task, shared}` inside the caller's own namespace (`personal` scopes are the
`/u/<user>/` roots, created with the user). The segment follows the `API_V2.md` §6 grammar. *Errors:*
`validation_error` (grammar), `forbidden` (outside the caller's namespace).

**`grant_access(ctx, scope, principal, idempotency_key) → GrantId`** — a `scope_grant` row (`V2-D36`): `principal`
may read the `project`/`shared` rows of the subtree, never `private` ones. Only the namespace owner may grant.
An unknown principal is `forbidden`, the same as an unauthorised scope. **`revoke_access(ctx, grant_id) →
Receipt`** — sets `revoked_at` once; grants are append-only. *Errors:* `forbidden`, `precondition_failed`
(already revoked).

### 4.11 Observe

**`stats(ctx, artifact_id) → Stats`** returns counts, coverage, refutation attempts, verification status and the
*ids* of support and counter items. Item **content** is returned only if the caller could read it through
`query`. **`cost(ctx, window) → CostReport`** returns tokens, latency and counts for the caller's own activity.
Neither returns another scope's content or counts. *Errors:* `forbidden`.

## 5. Gaps found in review, and how each was closed (`V2-D46`)

The r6 review found seven things other V2 documents relied on that 8.1 did not contain. Each is now decided.
Nothing outside sections 3-4 exists.

| # | Gap | Decision |
|---|---|---|
| 5.1 | A Tier C way to reject an inference (`USER_MODEL.md` §3, `UM-012`, `V2-D33`) | New operation **`dissent`** (Adjudicate, Tier C): `inferred → retired`, `reason='user_dissent'`, negative constraint. `refute` stays Tier B and evidenced |
| 5.2 | `move` (`ARCHITECTURE_V2.md` 5 rule 5) | **Composition, not a primitive:** `assert`/`ingest_artifact` into the target scope with the original's sources as evidence, `link(new, old, supersedes)`, then `retract_own(old)`, each with an idempotency key. A partial move leaves both copies readable: no loss, no leak |
| 5.3 | `ingest_artifact` payload | **Confirmed** as in 4.1: an externally formed claim or procedure through the grounding policy |
| 5.4 | Creating an entity | New operation **`create_entity`** (Structure, Tier B and C) |
| 5.5 | Registering an inference method | New operation **`register_method`** (Maintain, admin-only, append-only) |
| 5.6 | Scope creation and access grants | New class **Scope**: `create_scope`, `grant_access`, `revoke_access`, Tier C only. Identity (who a principal is) stays outside the engine |
| 5.7 | Revise an inferred element (`USER_MODEL.md` §3) | **Composition:** `infer` the narrowed or widened element, `link(new, old, refines)`, then `refute`/`retract_own(old)` (Tier B) or `dissent(old)` (user) |

## 6. Scope selectors (`MA-011`, decided: `V2-D35`, `V2-D36`, `V2-D45`)

Executable form: `custom_memory_engine.scope.paths` (grammar, matching) and `custom_memory_engine.scope.access`
(read set, write rule, both enforcement layers). Isolation suite: `tests/test_scope_isolation.py` and
`tests/integration/test_scope_isolation.py`, over the shared cases in `tests/scope_isolation_cases.py`, which
`E-011` re-runs on the real schema.

**Grammar.** A path starts and ends with `/`; the root is `/`. A user namespace is `/u/<user>/`, where `<user>`
is any v1 user id that is non-blank, has no surrounding whitespace, no `/`, no control character, is not `.` or
`..`, and is at most 256 characters. Every other segment matches `[a-z0-9][a-z0-9-]*`, at most 128 characters;
depth at most 16. No case folding or Unicode normalisation is applied: anything outside the grammar is
rejected (`validation_error`), never rewritten.

| Selector | Meaning |
|---|---|
| `exact(path)` | rows whose scope equals `path`, at every visibility |
| `subtree(path)` | rows at `path` or below it; matching is `pg_catalog.starts_with` over canonical paths, never `LIKE` (`V2-D35`) |
| `with_visibility(path, {project, shared})` | `subtree`, restricted to the named visibilities |

**Read set.** The union of the selectors, intersected with the caller's ACL (`V2-D36`): own namespace at every
visibility; another namespace only inside an active grant and never `private`. A selector broader than every
grant is denied, not narrowed. Nothing is implicit: ancestors are read only when named. A `private` row is
returned only to a selector whose path equals the row's scope (visibility is judged at the reading selector).

**Limit.** 1 to 16 selectors. Zero or more than 16 is `resource_limit` (`read_prefix_limit_exceeded`), and nothing
is returned; there is no truncation.

**Write rule.** A write into `(scope, visibility)` is authorized only if the caller could read that row back
through `exact(scope)`; otherwise `forbidden`, before any row is constructed (`scope.access.authorize_write`).

**Two independent layers.** The application computes the read set and filters with `read_predicate`. RLS
enforces the ACL alone, from `app.user_id` and `scope_grant` (`rls_read_using`); it never reads a value the
application computed. Each layer is tested alone; the application read set is proven contained in the ACL.

## 7. Acceptance traceability (`MA-012`, `MA-015`)

| Criterion | Where |
|---|---|
| All operations present exactly once, each with signature, tiers and error contract | section 3 (29 rows: the 23 of 8.1 plus `V2-D46`), section 4, section 1.1; `tests/test_api_contract.py` |
| Every Tier B prohibition stated and labelled for `MA-015` | 3.1 (TB1-TB10, AD1), each with a test in `tests/test_tier_permissions.py` |
| No-oracle error rule with exact codes | 1.1 (`forbidden` for absent and unreadable alike; static codes) |
| `V2-D07` contradiction resolved by an approved record, cited | header (`V2-D39`) |
| Every operation name used in `ROADMAP_V2.md` / `ARCHITECTURE_V2.md` exists here | `scripts/check_plan_refs.py` (`check_operations`, using section 3, section 6 and 7.1), run in CI |
| `MA-015`: every operation one row with Tier B, Tier C and admin permissions and conditions | section 3 |

### 7.1 Names the plan uses that are not operations

| Name | Kind | Meaning |
|---|---|---|
| `move` | composition | section 5.2 |
| `revise` | composition | section 5.7 |
| `ground` | internal function | grounding policy (`E-015`), called by `assert`/`ingest_artifact` |
| `assess_soundness` | internal function | soundness policy (`E-015`), called by `infer` |
| `ledger` | internal function | CP-bound helper (`SD-002`, `STATISTICS.md`) |
| `retract` | retired | the `V2-D07` five-call name; never an operation (`V2-D39`), use `retract_own` |
