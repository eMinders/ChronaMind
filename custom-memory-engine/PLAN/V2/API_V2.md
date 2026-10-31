# Public API Surface (v2.0 draft for freeze)

**Task:** `MA-012` · **Status:** canonical API reference since r7 (**`V2-D39`**, which also resolves the `V2-D07`
five-call contradiction and retires `retract`). Executable form: `custom_memory_engine.api.contract`, held equal
to this document and to `ARCHITECTURE_V2.md` section 8.1 by `tests/test_api_contract.py`. The operations listed
in section 5 are **not** on the surface until a `V2-Dnn` approves them.
**Extended by:** `MA-015` (the tier matrix check and `tests/test_tier_permissions.py`), `MA-011` (the scope
selector table, section 6). **References:** `ARCHITECTURE_V2.md` sections 5 and 8, `V2-D07`, `V2-D39`,
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

## 3. Operations and tiers

`✓` = permitted, `—` = not permitted (calling it is `forbidden`). 23 operations, ten capability classes,
exactly as in `ARCHITECTURE_V2.md` 8.1. This table is the **tiers matrix**; `MA-015` turns it into
`tests/test_tier_permissions.py`.

| # | Class | Operation | Tier B | Tier C |
|---|---|---|:-:|:-:|
| 1 | Ingest | `ingest_episode` | ✓ | ✓ |
| 2 | Ingest | `ingest_artifact` | ✓ | ✓ |
| 3 | Read | `query` | ✓ | ✓ |
| 4 | Read | `refine` | ✓ | ✓ |
| 5 | Read | `get` | ✓ | ✓ |
| 6 | Read | `provenance` | ✓ | ✓ |
| 7 | Write (grounded) | `assert` | ✓ | ✓ |
| 8 | Write (grounded) | `retract_own` | ✓ | ✓ |
| 9 | Write (inferred) | `infer` | ✓ | — |
| 10 | Write (inferred) | `verify` | ✓ | — |
| 11 | Write (inferred) | `refute` | ✓ | — |
| 12 | Structure | `link` | ✓ | ✓ |
| 13 | Structure | `unlink` | ✓ | ✓ |
| 14 | Structure | `link_entity` | ✓ | ✓ |
| 15 | Structure | `record_outcome` | ✓ | ✓ |
| 16 | Adjudicate | `adjudicate` | **—** | ✓ |
| 17 | Maintain | `purge_working` | — | ✓ |
| 18 | Maintain | `expire` | — | ✓ |
| 19 | Maintain | `rebuild_index` | — | ✓ |
| 20 | Delete | `delete` | **—** | ✓ |
| 21 | Export | `export` | — | ✓ |
| 22 | Observe | `stats` | ✓ | ✓ |
| 23 | Observe | `cost` | ✓ | ✓ |

"Administrative" operations (rows 17-19) are a role *within* Tier C, not a third tier. A deployment may further
restrict them; it may never grant them to Tier B.

### 3.1 The Tier B rules (each is a requirement for `MA-015`'s test)

| ID | Rule | Test (`tests/test_tier_permissions.py`, `MA-015`) |
|---|---|---|
| TB1 | **Tier B cannot delete.** It may only `retract_own` an artifact whose method it produced. | `test_tier_b_cannot_delete` |
| TB2 | **Tier B cannot adjudicate.** A reflection service must not silence a conflict. | `test_tier_b_cannot_adjudicate` |
| TB3 | Tier B cannot write a grounded claim without source evidence. | `test_tier_b_assert_requires_evidence` |
| TB4 | No Tier B operation moves anything to `asserted`; `inferred → asserted` does not exist. | `test_no_operation_promotes_inferred_to_asserted` |
| TB5 | `verify` and `refute` accept only the artifact's own method lineage. | `test_tier_b_verify_and_refute_own_lineage_only` |
| TB6 | Tier B can create `inferred` artifacts and refute its own. | `test_tier_b_can_infer_and_refute_own` |
| TB7 | Tier B cannot write to a scope it cannot read. | `test_tier_b_cannot_write_unreadable_scope` |
| TB8 | Every operation is assigned to a tier set; none is unassigned. | `test_every_operation_is_assigned` |

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
Both endpoints must be readable and the caller must be authorised on the endpoint scopes. **Open question
(`CHANNELS.md` 2.2):** a `contradicts` edge created by Tier B must not demote a grounded artifact out of
`facts`. *Errors:* `forbidden`, `validation_error` (unknown type).

**`link_entity(ctx, entity_a, entity_b, relation) → Receipt`**
Links two entities and requires authorisation on **both** scopes; otherwise it fails with the same `forbidden`
as a missing entity (`THREAT_MODEL.md` T-09).

**`record_outcome(ctx, procedure_id, outcome: success|failure, cost?, episode_ref?) → OutcomeStats`**
Retrospective only; updates `outcome_stats`. There is no execution. *Errors:* `precondition_failed` (not a
procedure), `forbidden`.

### 4.6 Adjudicate — Tier C only

**`adjudicate(ctx, artifact_id, decision: confirm|deny, note?) → Receipt`**
Resolves a `contested` artifact: `confirm → asserted`, `deny → retired`, with an adjudication record. *Errors:*
`precondition_failed` (artifact not `contested`), `forbidden` (including every Tier B call, TB2).

### 4.7 Maintain — Tier C only

**`purge_working(ctx, scope) → Receipt`** — deletes `retention='working'` episodes of one scope, audited; durable
claims and abstractions are untouched (retention floors). **`expire(ctx, scope, as_of?) → Receipt`** — applies
retention expiry; recorded as a scope event, not a deletion of knowledge. **`rebuild_index(ctx, index:
fts|vector|closure_hash) → JobId`** — queues an index rebuild. *Errors:* `forbidden`, `validation_error`.

### 4.8 Delete — Tier C only

**`delete(ctx, scope: memory|topic|all, target_ref, deletion_epoch?) → DeletionReceipt`**
Transactional lineage deletion (`DELETION_ORDER.md`). The receipt is content-free: counts only. A replay with
the same `deletion_epoch` returns the original receipt (v1 behaviour). *Errors:* `forbidden` (including every
Tier B call, TB1), `validation_error`, `retryable_conflict`.

### 4.9 Export — Tier C only

**`export(ctx, selectors: ScopeSelector[]) → ExportSnapshot`**
User-facing data export of the caller's read set; excludes content whose deletion is pending. A snapshot made
before a deletion must not be served after it (channel 11). *Errors:* `forbidden`, `resource_limit`.

### 4.10 Observe

**`stats(ctx, artifact_id) → Stats`** returns counts, coverage, refutation attempts, verification status and the
*ids* of support and counter items. Item **content** is returned only if the caller could read it through
`query`. **`cost(ctx, window) → CostReport`** returns tokens, latency and counts for the caller's own activity.
Neither returns another scope's content or counts. *Errors:* `forbidden`.

## 5. Referenced elsewhere but absent from the frozen surface

These are used or implied by other V2 documents and have **no operation** in section 8.1. They are listed so the
rule "no roadmap task references an operation absent from this document" can be enforced. None is added to the
surface until a `V2-Dnn` record approves it.

| # | Gap | Where it appears | Why it matters | Proposal |
|---|---|---|---|---|
| 5.1 | **A Tier C way to reject an inference** | `USER_MODEL.md` section 3 ("Contest": the inference goes `refuted` with `reason='user_dissent'`), `UM-012`, `V2-D33`, `A-011` | The user's dissent is the safety-critical path. `refute` is Tier B only and `adjudicate` handles `contested` artifacts only, so **a user in the application tier has no operation that rejects a hypothesis.** | Allow `refute` for Tier C with `reason='user_dissent'` and no evidence requirement, writing the negative constraint; or extend `adjudicate` to inferred artifacts. |
| 5.2 | `move` | `ARCHITECTURE_V2.md` 5, rule 5 ("move is copy + retract") | Named as an operation but not listed | Define as a composition of `assert`/`ingest_artifact` into the new scope plus `retract_own`, with a `supersedes` association; not a new primitive. |
| 5.3 | `ingest_artifact` payload | section 8.1 | Named without a definition (see 4.1) | Confirm the proposed meaning. |
| 5.4 | **Creating an entity** | `ARCHITECTURE_V2.md` 5.2 ("entity ids are created only through the public API") | Only `link_entity` is listed, so there is no way to create the thing being linked | Add `create_entity` to the Structure class. |
| 5.5 | **Registering an inference method** | section 4 (`METHOD` is an append-only registry), `infer` requires "a registered inference method" | Nothing writes the registry | An administrative `register_method`, Tier C, append-only. |
| 5.6 | **Scope creation and access grants** | section 5 ("the caller is authorised for it") | How a caller comes to be authorised is undefined; it determines the whole read and write set | State that scope administration is outside the engine API (identity layer), or add operations. |
| 5.7 | Revise (narrow or widen an inferred element) | `USER_MODEL.md` section 3 | Described as "creates a new artifact, retires the old, links `refines`" | A composition of `infer`, `refute`/`retract_own` and `link`; document it rather than add a primitive. |

## 6. Scope selectors (completed by `MA-011`)

Carried from `ARCHITECTURE_V2.md` 5 so this file is self-contained. `MA-011` owns the precise semantics and the
failing-first isolation test.

| Selector | Meaning | Notes |
|---|---|---|
| `exact(path)` | rows whose scope equals `path` | `path` always ends in `/` |
| `subtree(path)` | rows whose scope path starts with `path` | prefix matching must be exact (`starts_with`), not `LIKE`: see `THREAT_MODEL.md` T-02 |
| `with_visibility(path, {project, shared})` | `subtree`, restricted to the named visibilities | a `private` row is returned only when the selector equals the row's own scope |

The effective read set is the union of selectors **intersected with the caller's access**. Ancestors are never
implicit.

## 7. Acceptance traceability (`MA-012`)

| Criterion | Where |
|---|---|
| Every operation in the architecture appears exactly once with signature, tier permissions and error contract | section 3 (23 rows, once each), section 4, section 1.1 |
| "Tier B cannot delete or adjudicate" is stated and traceable to a test | 3.1 (TB1, TB2 and the test names `MA-015` must create) |
| No roadmap task references an operation absent from this document (checked by `MA-018`) | section 5 lists every gap; `MA-018`'s checker does not yet inspect operation names (follow-up) |
