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
| `internal_error` | **Last resort only** (`V2-D58`): the entry point failed in a way none of the codes above describes — an engine defect or an unexpected driver error. It is returned by `Engine.call` and never by an operation's own logic, so it is not listed per operation in section 3. It carries a code and **no** detail, message, argument or traceback: a bug must not become a disclosure channel. Receiving it means the engine is wrong, not that the request was. | n/a |

The table is the **complete** enumeration: `tests/test_api_contract.py` holds it equal to the executable contract (every operation's codes plus `internal_error`), so a code the implementation can return and this table omits fails CI.

## 2. Shared types

```text
SourceRef      { type: 'turn'|'tool'|'file'|'paper'|'event'|'external', ref: str, span?: str, content_hash: str,
                 role?: 'user'|'assistant'|'system' }
ScopeSelector  exact(path) | subtree(path) | with_visibility(path, {project, shared})      # section 6
QueryRequest   { text, intent?, mode: current_state|historical_as_of|historical_window|change|audit_as_of,
                 scope?, target_time?, belief_time?, window_start?, window_end?,
                 selectors: ScopeSelector[], budget?: ChannelBudgets, limit? }
ChannelBudgets { facts: int, hypotheses: int, procedures: int, conflicts: int,
                 total_tokens?: int, facts_floor_tokens?: int }
QueryResult    { facts[], hypotheses[], procedures[], conflicts[], audit[], refused[], is_audit,
                 provenance_bundle, cost: AssemblyCost, coverage: CoverageReport,
                 changes: Change[] }                                  # CHANNELS.md; changes only for mode=change
Change         { outcome: added|removed|changed, previous: id[], current: id[] }   # ids only (V2-D81)
Receipt        { id, effect, recorded_at }     # content-free
PurgeReceipt   { purged_episodes, retained_as_evidence, recorded_at }   # content-free: counts and the clock only
OutcomeStats   { procedure_id, successes, failures, last_used_at, mean_cost }   # content-free: counters, clock, cost
Precondition   { op: 'scope_within', scope: str }                      # the request's scope lies in this subtree
             | { op: 'exists'|'absent', match: { kind?, lifecycle?, facet?, predicate? } }   # 1-4 keys; E-030
                                                 # a procedure's preconditions are a conjunction; [] = always applicable
```

`ChannelBudgets` values are whitespace-token ceilings, using the same deterministic proxy as
`engine.render.default_token_counter` (not a model-accurate tokenizer). Each channel defaults to 2048 tokens;
`total_tokens` is unbounded unless supplied. The default facts floor is 256 tokens, reduced to the facts and total
ceilings when either is smaller. A channel that cannot fit its next item is truncated at that stable point and reports
the number of omitted items and tokens; it does not silently shorten content. With a shared total ceiling, assembly
reserves the facts floor first, then fills hypotheses, procedures, conflicts, and remaining facts.

`coverage` counts candidates **after temporal/read filtering, expansion, and procedure applicability**, but before
channel token budgets. For each channel it reports candidates, returned items, refused items with static reasons,
truncated items, token ceiling, tokens used, and tokens omitted. `cost` is assembly-only: candidate rows considered,
items rendered, tokens evaluated, and tokens rendered. `E-036` owns aggregation of costs across the other stages.
Every artifact in an answer channel carries its own provenance bundle; `provenance_bundle` is the flattened,
channel-ordered `(artifact_id, bundle)` view of those same items. Audit rows never enter it.

## 3. Operations and tiers (the tier matrix, `MA-015`)

`✓` = permitted, `—` = not permitted (calling it is `forbidden`). **32 operations, eleven capability classes**:
the 23 of `ARCHITECTURE_V2.md` 8.1 as approved (`V2-D39`), the six added by `V2-D46` (`dissent`, `create_entity`,
`register_method`, `create_scope`, `grant_access`, `revoke_access`), the two of `V2-D105` (`lift`, `constraints`), and
`set_visibility` (`V2-D113`). 19 are open to Tier B.

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
| 30 | Read | `constraints` | ✓ | ✓ | ✓ | — |
| 4 | Read | `refine` | ✓ | ✓ | ✓ | `targets_readable` |
| 5 | Read | `get` | ✓ | ✓ | ✓ | `targets_readable` |
| 6 | Read | `provenance` | ✓ | ✓ | ✓ | `targets_readable` |
| 7 | Write (grounded) | `assert` | ✓ | ✓ | ✓ | `evidence_required`, `scope_writable`, `targets_readable` |
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
| 31 | Adjudicate | `lift` | — | ✓ | ✓ | `targets_readable` |
| 19 | Maintain | `purge_working` | — | — | ✓ | `scope_writable` |
| 20 | Maintain | `expire` | — | — | ✓ | `scope_writable` |
| 21 | Maintain | `rebuild_index` | — | — | ✓ | — |
| 22 | Maintain | `register_method` | — | — | ✓ | — |
| 23 | Delete | `delete` | — | ✓ | ✓ | `scope_writable` |
| 24 | Export | `export` | — | ✓ | ✓ | — |
| 25 | Scope | `create_scope` | — | ✓ | ✓ | `namespace_owner_only` |
| 26 | Scope | `grant_access` | — | ✓ | ✓ | `namespace_owner_only` |
| 27 | Scope | `revoke_access` | — | ✓ | ✓ | `namespace_owner_only` |
| 32 | Scope | `set_visibility` | — | ✓ | ✓ | `namespace_owner_only` |
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
| TB2 | **Tier B cannot adjudicate** (`adjudicate`, `dissent`, `lift`). A reflection service must not silence a conflict or a user's dissent. | `test_tb2_tier_b_cannot_adjudicate` |
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

**`ingest_episode(ctx, scope, source: SourceRef, content: str, event_time?, idempotency_key, visibility?) → Receipt`**
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
per-channel coverage. The temporal predicate is a filter, never a score term.

**The reference instant ρ is the engine's own clock and is never caller-supplied** (`I4`). `reference_time` is
therefore not an input: a request that sends one is refused (`validation_error/reference_time_is_the_engine_clock`),
because a caller-chosen ρ would read a belief the engine never held at that instant. The engine takes ρ from
`transaction_timestamp()`, the clock `recorded_range` is written against, so "now" means the same thing on both
sides of the comparison.

`mode` selects the predicate (`TECHNICAL_SPECIFICATION.md` 3.5):
- `current_state` (default when `mode` is omitted): `status='active' AND recorded_range @> ρ AND event_range @> ρ`;
- `historical_as_of`: adds `target_time`, must satisfy `target_time ≤ ρ`; `event_range @> target_time`;
- `historical_window`: adds `window_start`/`window_end`; `event_range && [window_start, window_end)`, and
  `window_end ≤ ρ`. Both bounds are required and the window must be non-empty;
- `audit_as_of`: adds `target_time` and `belief_time`; restricted to `belief_time ≤ clock_now()`, admits
  `status IN ('active','archived')`, and is **Tier C only**. Its rows are returned in `audit[]` and `is_audit=true`,
  **never** in an answer channel: `TECHNICAL_SPECIFICATION.md` 3.5 forbids using them as answer context, and
  `channel_of` maps a retired artifact to `none`, so a channel-routed audit would drop exactly the rows it exists to
  show. Tier B calling it is `forbidden`;
- `change` (`V2-D81`): adds `window_start`/`window_end` under the window form's names and refusals, and diffs two
  `historical_as_of` views under ρ — *previous* at `window_start`, *current* at `window_end`. Both are answer-context
  reads (`status='active'`, read set in SQL), so `change` never reaches a retired or archived row. Rows are keyed on
  `(subject_key, predicate_key)` for a single-valued predicate and on `memory_key` otherwise; per key the outcome is
  `added`, `removed` or `changed`, and an unchanged key is omitted. `changes[]` carries version ids only; the channels
  are assembled from the union of the two views. `change` never expands associations, and a view exceeding `limit`
  is refused (`resource_limit/change_view_exceeds_limit`) rather than diffed.

The read set and temporal predicate are applied together before the candidate limit. Association expansion starts
from these candidates, applies the same predicates to newly reached artifacts and may add supplemental rows beyond
`limit`. Each hop also requires the association's own scope and visibility to match the read set. An association
stored as private at the personal root is eligible only when the request includes a selector for that root.
The default budget is one hop, and the plan records the budget it ran (`V2-D79`). At the boundary, expansion returns
the rows found so far. **Reached rows are themselves paged**: a response carries at most one page of them, so a whole
response is at most twice `limit` — the candidates plus one page of reached rows — whatever the graph's out-degree.
The page is the nearest-hop prefix of the union order (hop, then artifact id), so truncation is deterministic and is
not an error (`V2-D79`; `resource_limit` stays reserved for a declared bound like the selector count). Each artifact
carries a
`hop_distance` (zero for a candidate, otherwise the shortest eligible association distance); reached artifacts
enter the same channel-routing path as candidates. Audit applies the requested belief time to the artifact rows
and uses the stored association topology. *Errors:* `validation_error` (missing or malformed parameter,
`target > ρ`, empty window, unknown mode, a caller-supplied `reference_time`), `forbidden` (`audit_as_of`
as Tier B, or a scope outside the read set),
`resource_limit` (more than 16 selectors, or a `change` view exceeding `limit`).

**`refine(ctx, result_id, instruction: str) → QueryResult`**
Drill-down on a prior result within the same read set; cannot widen it (`V2-D83`). A result carries an opaque
`result_id` (`res_` + 128 random bits) in a bounded, process-local, 15-minute handle map; the map is not a table
(a result is answer-context material, not a memory), so a restart or a different worker answers `forbidden`
exactly like an unknown handle — never with a wider result. The handle records the **read set of the call that
produced the result**, with its intent and channel budgets, and a refine executes under that set: a credential that
has widened since changes nothing, and one that has narrowed is refused (`forbidden`), never silently redirected. The
refine re-enters the planner with the recorded intent, re-assembles with the recorded budgets, cuts association
expansion (following an edge would reach an artifact the result never
contained), and fetches only the result's own candidate ids, re-read from the database: a row deleted since the
original query is absent, not resurrected.

A refine applies **one** rule-based narrowing operator per call, matched exhaustively and never by a model:

| Instruction | Keeps |
|---|---|
| `artifact <id>` | that artifact only (one token) |
| `channel <facts\|hypotheses\|procedures\|conflicts>` | items `channel_of` routes to that channel |
| `mention <text>` | items whose content contains the text (case-insensitive) |
| `scope <path>` | items at or inside the canonical path |

Narrowing filters the candidate rows **before** assembly, so coverage and cost describe the narrowed answer
honestly, channel membership is `channel_of`'s unchanged answer, and each kept abstraction keeps its label, support
summary and bound. A chain is bounded at four refines: the handle records its depth and the fifth answers
`validation_error/refine_depth_exceeded`. *Errors:* `forbidden` (unknown, foreign, expired, or a handle whose
recorded read set the caller no longer holds), `validation_error` (`instruction_required`,
`instruction_unrecognised`, `refine_depth_exceeded`).

**`get(ctx, artifact_id) → Artifact`** and **`provenance(ctx, artifact_id, depth?) → ProvenanceView`**
Return one artifact or its provenance. `get` renders the artifact through the **same** channel assembler `query` uses
(`engine/channels.py::assemble`, one non-truncating budget), so every rendered-item rule holds on this route too: a
fresh verified abstraction carries its complete bound in `content` **and** `bound`, a hypothesis carries its label and
support summary, a `conflicts` item carries both sides, and every item carries its provenance bundle. An artifact the
assembler cannot render — a missing bound, a missing conflict counterpart, a `retired` row (rendered in no channel), or
a source that cannot be materialised at all — answers `forbidden`, the same answer as absent, so an unrenderable
artifact is never distinguishable from one the caller may not read (`T-11`, `T-18`). `get` does not apply a temporal
predicate; it is a lookup by id, and an artifact still staged at `conflict_pending` is readable by id even though
`current_state` does not yet deliver it. Provenance depth defaults to 1 and is bounded at 8; traversal stops at the
requested depth, and `depth_limited` is true when a readable source remains beyond that boundary. A source in a scope
the caller cannot read appears as an opaque reference only and is never traversed (`CHANNELS.md` R6,
`THREAT_MODEL.md` T-10). *Errors:* `forbidden` for absent **and** unreadable alike; `validation_error` for a depth
outside 1–8.

`engine/render.py::render_context` remains available for `TemporalVersion` callers. Its active-status filtering,
current-mode ended-version exclusion, validity-interval/role labels, rank-order preservation, and token accounting
are unchanged. The channel assembler complements it: temporal filtering happens before assembly and each routed
channel gets its own reported budget; it does not retire or silently replace `render_context`.

| `render.py` behavior | E-033 mapping |
|---|---|
| Drop non-active versions | Query candidate predicates admit only active rows; audit rows remain isolated in `audit[]`. |
| In current mode, omit ended event ranges | The `current_state` temporal predicate excludes them before assembly. |
| Preserve caller rank order and stop at first non-fitting item | Assembly preserves deterministic input order within channels and truncates at the first non-fitting item; procedure ranking still runs after routing. |
| Include validity interval and temporal role labels | Preserved unchanged by `render_context`; the channel API remains structured artifacts and does not replace that temporal-version rendering surface. |
| Count tokens and report truncated items/tokens | The same whitespace proxy is reused; channel coverage reports per-channel tokens and omitted items/tokens. |

### 4.3 Write (grounded)

**`assert(ctx, scope, kind, content|predicate, evidence: SourceRef[], event_range, idempotency_key, subject_entity?, preconditions?: Precondition[], steps?: str[], assertion_mode?, cardinality?, semantic_slot?, um_facet?, visibility?) → Receipt | rejected`**
A grounded statement through the grounding policy. **`evidence` must be non-empty for Tier B and Tier C
alike** (TB3). An optional `subject_entity` names an entity in the same scope as the claim; the claim's subject and
entity remain scope-local. Result lifecycle: `asserted` or `contested`. *Errors:* `validation_error` (no evidence, bad
range), `rejected` (instruction screen, assertion mode, conflict), `forbidden` (unreadable or different-scope entity).
**Procedure shape (`E-030`, migration 21).** For `kind='procedure'`, the optional `preconditions` and `steps` are
stored with the procedure, once, and never updated (`ARCHITECTURE_V2.md` §6); `ingest_artifact` takes the same two
fields inside `candidate`. Passing either for any other kind is `validation_error`
(`procedure_shape_requires_a_procedure`), and a malformed one is `validation_error` with the parser's code; neither is
`forbidden`, and the error contract above is otherwise unchanged. `get` and `query` return both on a procedure
artifact. The engine never executes a step; `engine/procedure_matching.py` answers applicability only.

**Semantic write options (`W3.5`, `V2-D87`).** Four optional arguments carry what semantic extraction produces, and each
is a closed or normalized value the engine validates before any statement runs:

* `cardinality` (`single` | `multi` | `event`, default `multi`) decides `single_valued`. Only a single-valued claim can
  supersede another and only it is exclusive within its slot, so this is the argument that switches the conflict
  pipeline on. Omitting it keeps the pre-W3.5 multi-valued shape, so an existing caller's writes are unchanged.
* `semantic_slot` is the open grouping key (migration 24) that says what the claim is about without a catalog. It is
  normalized once (`semantic.slots.normalize_slot`: NFKC, casefold, separator collapse, edge punctuation stripped) and
  the schema enforces the same rule. Two single-valued claims in different slots are two facts and neither supersedes
  the other.
* `um_facet` tags an artifact as part of the evolving user model (`preference` | `goal` | `constraint` | `context` |
  `decision_pattern` | `meta_preference`). Before `W3.5` the column was read and never written.
* `assertion_mode` is the caller's pragmatics. `asserted` may become active; `planned` routes to `plans_to`;
  `hedged`, `hypothetical` and `third_party` are **held** (`contested`, routed to `conflicts`) rather than rejected;
  `quoted` and `joking` are rejected because they assert nothing.

**Non-turn evidence (`V2-D87`).** `evidence` may cite `external`, `tool`, `file` or `event` sources, each with its
`content_hash`; the row stores the declared type, its reference and that hash. A claim whose every source is
non-turn **and declares no role** is forced to `assertion_mode = third_party` whatever the caller asked for —
presenting retrieved text as fact is precisely what the channel router exists to prevent — and the deterministic
authority ladder
(`semantic.authority`: `USER > SYSTEM > ASSISTANT > THIRD_PARTY`, derived from `source_type`/`evidence_role`) is what
stops such a claim superseding one resting on the user's own words. A refused supersession is *recorded*
(`memory_conflict.reason_code = 'authority_quarantine'`), never silently dropped.

**`role` on a non-turn source (`V2-D96`, `V2-D93`).** An application that keeps its own transcript — the ChronaMind app's
session log is the one deployment this engine has — cites its own record of who spoke, not a `turn` row the engine never
saw. Without `role` on such a source every claim it makes would be quarantined as third-party, which would make the
engine unusable for that deployment; with it, the claim is classified exactly as a `turn` source would be. A **`turn`
source may not carry `role`**: there the role is a column of the row being cited, and a caller-supplied copy would be a
second source of truth. An undeclared `role` is `validation_error: source_role_invalid`; a `role` on a `turn` source is
`validation_error: source_role_not_allowed_for_turn`.

**`retract_own(ctx, artifact_id, reason) → Receipt`**
Moves an artifact to `retired` (recorded-time close). Tier B: only artifacts **its own method produced**. Tier C:
its own assertions. It is not deletion: the retired artifact stays auditable. *Errors:* `forbidden` (not yours,
or absent), `precondition_failed` (already retired).

### 4.4 Write (inferred) — Tier B only

**`infer(ctx, scope, method: {id, version, params_hash}, sources: ArtifactId[], statement, predicate?, ledger?: {support, counter, undetermined, opportunity}, idempotency_key) → Receipt | rejected`**
Submits to the **soundness policy**. Requires at least one readable source and a registered method. The opportunity
ledger (`STATISTICS.md`) is **derived, not an input** (`I15`, `V2-D114`): the engine computes `(s, k, u, o)` from the
named sources and records `refutation_attempts` as the number of refutation probes it executed. A caller-supplied
`ledger` is optional and only checked against the derivation: a malformed or unbalanced one is `validation_error`, and
one that differs from the derived counts is `rejected: ledger_mismatch`; it never replaces the derived counts. Creates
`lifecycle='inferred'`; **no parameter yields `asserted`**. *Errors:* `rejected` (below the confidence floor, coverage
gate, a ledger that cannot be derived — `insufficient_evidence`, `contradictory_derivation`, `no_supporting_evidence` —
or `ledger_mismatch`), `validation_error`, `forbidden` (unreadable source or scope).

**`verify(ctx, artifact_id, verification: VerificationRecord) → Receipt`**
Posts a held-out verification result. Accepted only for an artifact whose method lineage the caller owns, with
a split disjoint from the support set and pinned `support_artifact_ids` / `held_out_artifact_ids` (`verification_artifact`,
migration 31) readable by the caller. Verified outcomes also require `soundness.admission.assess` admission (with
`negative_control_clean`, `tau_confidence`, and `tau_coverage`). *Errors:* `precondition_failed` (not inferred,
unpinned or overlapping artifacts, split not disjoint, or admission bound unmet), `forbidden` (unreadable lineage or
unreadable pinned artifact).

**`refute(ctx, artifact_id, counter_evidence: SourceRef[], reason) → Receipt`**
Moves an inferred artifact to `contested` (grounded counter-evidence) or `retired` (refuted). Evidence is
required. It is the only Tier B way to change an inferred artifact's lifecycle. *Errors:* `precondition_failed`,
`validation_error` (no evidence), `forbidden`.

### 4.5 Structure

**`link(ctx, from, to, type, weight?, source_version_ids?, visibility?) → AssociationId`** · **`unlink(ctx, association_id) → Receipt`**
Association edges (`related_to`, `contradicts`, `refines`, `specialises`, `instance_of`, `caused_by`,
`precedes`, `part_of`, `corroborated_by`, `supersedes`). Associations are **not provenance** and do not cascade.
Both endpoints must be readable and the caller must be authorised on the endpoint scopes. **`contradicts`
(`V2-D48`, C-P4):** only an edge between two grounded endpoints created by the grounding policy or a Tier C
caller opens a contradiction (both endpoints then render in `conflicts`). A Tier B `contradicts` edge is stored
as an association with `origin='tier_b'` and has **no** channel effect; a Tier B caller with counter-evidence
against its own inference uses `refute`. *Errors:* `forbidden`, `validation_error` (unknown type).

**`create_entity(ctx, scope, entity_type, label, idempotency_key, aliases?, source_version_ids?, visibility?) → EntityId`** (`V2-D46`)
Creates a principal-scoped, opaque entity (`ARCHITECTURE_V2.md` 5.2, `V2-D25`) in a scope the caller may write.
The `EntityId` is engine-generated; `label` is content and follows the scope's read rules. Optional `aliases`,
`source_version_ids`, and `visibility` (default `'private'`) record structural provenance (`artifact_provenance`) and
scope sharing at creation time. A replay with the same key returns the same id. *Errors:* `forbidden` (scope not
writable or unreadable `source_version_ids`), `validation_error`.

**`link_entity(ctx, entity_a, entity_b, relation, source_version_ids?) → Receipt`**
Records an explicit relation between two scope-local entities. It requires authorisation on **both** scopes;
otherwise it fails with the same `forbidden` as a missing entity (`THREAT_MODEL.md` T-09). The link does not
merge their ids or move either entity's attributes.

**`record_outcome(ctx, procedure_id, outcome: success|failure, cost?, episode_ref?) → OutcomeStats`**
Retrospective only; updates `outcome_stats`. There is no execution. *Errors:* `precondition_failed` (not a
procedure), `forbidden`.

**There is no `idempotency_key`, and that is deliberate** (`V2-D75`): the operation increments a counter, which has no
identity to deduplicate on, so replaying the same call records a second outcome. Making it idempotent would need a key
the signature does not have, and would silently swallow a legitimate second attempt.

`cost` is an opaque non-negative real: `API_V2.md` declares no currency or unit, so the engine stores a **sum and a
count** and reports `mean_cost = sum / count`. A running mean would make the persisted value depend on the order the
outcomes arrived in. `mean_cost` is `null` until a cost is recorded; a recorded cost of `0.0` is a real mean, not the
absence of one. `episode_ref` is accepted and recorded as a content-free audit flag (`episode_referenced`), never as a
locator: linking an outcome to the episode that demonstrated it is `E-030`'s, together with `preconditions` and
`steps`.

**The counters only grow.** Migration 20's `guard_outcome_stats` refuses a decrement, so "an outcome is a recorded
event" is a property of the schema rather than of one handler's care. The `procedures` channel is then ordered by
`(successes + 1) / (successes + failures + 2)` descending, `last_used_at` descending, id ascending
(`engine/procedure_ranking.py`, `V2-D75`); ranking reorders **within** the channel and never moves an item between
channels.

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

**`lift(ctx, constraint_id, evidence_artifact_id, note?) → Receipt`** (`V2-D105`)
The undo `I16` describes: a denied element reopens, but only on **new grounded evidence**. `evidence_artifact_id` names an
`observed` or `asserted` artifact of the caller's — an `inferred` one is refused (`evidence_not_grounded`), because a
constraint on inference cannot be lifted by more inference without making the constraint vacuous. The lift is a
record, not a permission: `lifted_at`, `lifted_by` and `lift_evidence_version_id` move together (the schema's
`negative_constraint_lift_is_complete`), and **deleting the evidence withdraws the lift**, so the dissent stands again.
One lift per constraint (`constraint_not_active` on a second attempt), Tier C only (TB2). The caller names the
constraint rather than the artifact because the constraint survives both the retirement and the deletion of the belief
that provoked it. *Errors:* `precondition_failed` (`constraint_not_active`, `evidence_not_readable`,
`evidence_not_grounded`), `validation_error`, `forbidden`.

### 4.6.1 Reading back the dissent

**`constraints(ctx, include_lifted?) → ConstraintList`** (`V2-D105`)
The caller's own negative constraints, active first. Without it a constraint's id is unknowable and a dissent is a
permanent block with no way to name it. Returns identifiers only — the constraint id, the scope path the caller owns,
the predicate key, the method *family* (a registered name), both instants, and the lift's evidence id — and never
content. Available to Tier B, because a service acting for the user must be able to see why its inference was refused.

### 4.7 Maintain — Tier C only

**`purge_working(ctx, scope) → PurgeReceipt`** — deletes `retention='working'` episodes of one scope, audited; durable
claims and abstractions are untouched (retention floors). The receipt carries the two counts and the belief time and
nothing else: a purge touches no caller-named artifact, so there is no artifact id to return and an id would be the one
thing §1.1 forbids. A repeated purge returns `(0, retained)` and is not an error; the counts also appear, content-free,
in the scope event (an `audit_event` of type `working_purged`). An episode that a surviving row cites as evidence is
retained, not promoted or demoted: rule PW-1 in `engine/purge.py`, logged as `E-026-F1` for a decision record. **`expire(ctx, scope, as_of?) → ExpiryReceipt`** — applies retention expiry: an abstraction whose
`profile.abstraction_expiry` window has elapsed gets `abstraction_stats.expires_at` stamped, which is what derives the
`expired` stale cause (`V2-D24`), and one content-free scope event (`retention_expired`) records two counts. Nothing is
deleted — the contract's `deletes` flag was corrected to `false` (`V2-D97`) — and the numbers come only from the
profile, so a deployment without a policy expires nothing (`V2-D14`). `as_of` may not be in the future (the engine's own
clock is the observation, `V2-D73`). *Errors:* `forbidden`, `validation_error`, `precondition_failed`.
**`rebuild_index(ctx, index: fts|vector|closure_hash) → JobId`** — queues an index rebuild; the worker runs
`REINDEX ... CONCURRENTLY` for `fts`/`vector` (outside any transaction) and reseals `closed_over_hash` for
`closure_hash` (`engine/index_maintenance.py`). *Errors:* `forbidden`, `validation_error`.
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

The request also records what it erased besides its target — the evidence turns of the versions it deleted and the
idempotency identities of the writes whose results it deleted — as keyed digests (`V2-D125`), which is what makes the
deletion survive the restore of an older backup. Two consequences a caller sees: a **resend** under an idempotency key
whose result was deleted answers with that erased result and writes nothing (before `V2-D125` it was a first write once
a restore had removed the record), and a turn that evidenced a deleted version is never extracted again, so a
re-extraction cannot re-derive the claim. The turn is erased when nothing surviving cites it; otherwise it stays and is
flagged.

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

**`set_visibility(ctx, artifact_id, visibility) → Receipt`** — (`V2-D113`, `I7`) sets `visibility`
(`private | project | shared`) of one artifact (`memory_version`, `entity` or `association`) the caller owns. It is an
ordinary update under row-level security: the own-user policy decides whether the caller may write the row, so a
grantee that can read a shared artifact can never change it. The artifact's children (evidence, derivation,
embedding, ledger, verification, outcome counters, procedure shape) carry no visibility of their own: they are
readable exactly when their parent is (migration 32). Visibility is per version: a later version of the same belief
starts `private` unless written with `visibility`. Lowering to `private` closes every grantee read on the next call.
One content-free `visibility_changed` audit row (artifact kind and new value, never the id) is written. *Errors:*
`forbidden` (another tenant's id and an unknown id are one answer), `validation_error` (`visibility_invalid`).
`visibility` may also be given at insertion to `ingest_episode`, `assert`, `create_entity` and `link`.

### 4.11 Observe

**`stats(ctx, artifact_id) → Stats`** returns counts, coverage, refutation attempts, verification status and the
*ids* of support and counter items. Item **content** is returned only if the caller could read it through
`query`. **`cost(ctx, window) → CostReport`** returns per-stage latency (`latency_ms`), candidate counts
(`candidates_in`, `candidates_out`, with documented unit per stage), context-budget token counts
(`tokens_considered`, `tokens_rendered`) and charged usage (`charged_calls`, `input_tokens`, `output_tokens`,
`embedding_tokens`, `cost_usd`, all zero when zero charged calls occurred) for the caller's own retrieval activity
within `window` (`None` for the retained window, positive seconds, `timedelta`, or `{start, end}` aware instants).
Neither returns another scope's content or counts. *Errors:* `forbidden`, `validation_error` (`window_invalid`).

## 5. Gaps found in review, and how each was closed (`V2-D46`)

The r6 review found seven things other V2 documents relied on that 8.1 did not contain. Each is now decided.
Nothing outside sections 3-4 exists.

| # | Gap | Decision |
|---|---|---|
| 5.1 | A Tier C way to reject an inference (`USER_MODEL.md` §3, `UM-012`, `V2-D33`) | New operation **`dissent`** (Adjudicate, Tier C): `inferred → retired`, `reason='user_dissent'`, negative constraint. `refute` stays Tier B and evidenced |
| 5.2 | `move` (`ARCHITECTURE_V2.md` 5 rule 5) | **Not offered by the engine** (`V2-D113`). A faithful move re-cites the original's evidence in the target scope, and the contract deliberately returns evidence as ids only (`V2-D37`), so no client composition of public operations can carry it; a composition that re-asserts content without it would launder a grounded claim into a weaker one. The need `I7` names — a collaborator reading a memory — is met without a copy by `set_visibility` plus a grant, which also keeps one deletion surface instead of two. Scope identity stays immutable (migration 32 `guard_scope_identity`). A server-side `move` primitive is tracked as #258 |
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

**Write rule (`V2-D53`).** A write (and therefore a `delete`, whose condition is `scope_writable`) is authorized
only inside the caller's own namespace `/u/<caller>/`; otherwise `forbidden`, before any row is constructed
(`scope.access.authorize_write`). A grant confers **read** membership only (`V2-D36`): a grantee can neither write
nor delete in the owner's namespace. Every authorized write is readable back by its writer (§5 rule 1).

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
| `move` | not offered | section 5.2 |
| `revise` | composition | section 5.7 |
| `ground` | internal function | grounding policy (`E-015`), called by `assert`/`ingest_artifact` |
| `assess_soundness` | internal function | soundness policy (`E-015`), called by `infer` |
| `ledger` | internal function | CP-bound helper (`SD-002`, `STATISTICS.md`) |
| `retract` | retired | the `V2-D07` five-call name; never an operation (`V2-D39`), use `retract_own` |
