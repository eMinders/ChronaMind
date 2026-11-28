# Deletion-Dependency Ordering

**Task:** `MA-014` · **Status:** design argument plus an executable reference model; the database
implementation belongs to `E-037` and `E-038`. **References:** `ARCHITECTURE_V2.md` sections 9.2 and 10,
`V2-D09`, `V2-D24`, `INVARIANT_LEDGER.md` (`I8`, `I13`), `THREAT_MODEL.md`.

Section 10 of the architecture requires that **`verified ∧ deleted-source` be unreachable after any
committing transaction**. That is an ordering property: it holds only if every dependent is judged after the
things it depends on and every row is removed after the rows that point at it. This document states the rule
precisely, argues it, lists every way it could be violated, and names the test that would catch each.

## 1. The invariants

Let the derivation graph `G` have a node per artifact and an edge `x → s` when `s` is a source of `x`
(`memory_derivation(derived_version_id = x, source_version_id = s)`). Let `D` be the explicit deletion seeds.

| ID | Statement | Source |
|---|---|---|
| **O1** | After commit, no surviving artifact has a provenance edge to a deleted or retired artifact. | `I13`, section 10 rule 2 |
| **O2** | After commit, no artifact is `verified ∧ ¬stale` while any artifact in its provenance closure was deleted. Staleness is derived from the closure hash, never stored. | `I13`, section 9.2, `V2-D24` |
| **O3** | The closure is complete: every artifact transitively derived from `D` is reconciled. Depth is a performance parameter, not a correctness bound. | `I8`, section 10 rule 1 |
| **O4** | Physical removal never leaves a dangling edge, and no edge needed for classification is removed before classification finishes. | v1 foreign keys (below) |

**Preconditions of a deletion transaction** (stated so a violation is attributable): **P1** the derivation graph
is acyclic (F9 enforces it at write time); **P2** the transaction holds the serialisation locks for every scope
its closure touches before its first write (section 6.1; in v1, the one per-user lock); **P3** no verification of
an artifact in the closure commits concurrently with a stale view (F5). O1-O4 are the post-conditions.

The v1 referential structure makes O4 concrete:
`memory_derivation.derived_version_id … ON DELETE CASCADE`, but
`memory_derivation.source_version_id REFERENCES memory_version (id)` with **no** cascade. A source row cannot
be deleted while an edge still names it, so edges must be removed first (the v1 code does exactly that, with a
comment calling the key "intentionally restrictive").

## 2. The rule

Two different orders are involved. The architecture text, "for each dependent in closure order (deepest
first)", describes one of them and so can be read as the other. They must be kept apart.

| Phase | Order | Why |
|---|---|---|
| **A. Classify** each dependent as `delete`, `re-derive` or `retire` | **sources before dependents** (ascending topological order from the seeds) | A dependent's fate is a function of the *final* fates of its sources. It cannot be computed before they are known. |
| **B. Apply** the decisions | **dependents before sources** (most-derived first), edges before rows | Foreign keys require every edge to a row to go before the row (O4). |

Classification, for a dependent `x` with sources `S(x)` and verification threshold `t`:

```text
survivors(x) = { s ∈ S(x) : fate(s) ∈ {alive, re-derived} }
x ∈ D                         → delete
survivors(x) = ∅              → delete                      # every source is gone
every s ∈ S(x) is alive       → alive                       # nothing it depends on changed
|survivors(x)| ≥ t            → re-derive on survivors(x); x is now stale (its closure hash changed)
                                # includes |survivors(x)| = |S(x)| with some source re-derived: x's
                                # content came from content that changed, so it is regenerated too
otherwise                     → retire(reason = 'source_deleted')
```

## 3. Argument

**Lemma 1 (well-founded).** If `G` is acyclic, visiting nodes in an order where every source precedes its
dependents classifies each node using only final fates of its sources. *Proof:* by induction along the order;
`fate(x)` depends only on `{fate(s) : s ∈ S(x)}`, all already final. ∎

**Lemma 2 (staleness is transitive without a walk).** `stale(a) ⟺ verified ∧ closure_hash(a.sources_now) ≠
a.closed_over_hash`. Deleting or retiring any node in `a`'s closure changes that hash, so every ancestor
becomes stale by construction, at any depth (section 9.2). ∎

**Lemma 3 (alive is transitive).** If `fate(x) = alive`, every node in `x`'s transitive closure is `alive`.
*Proof:* by induction along the sources-first order. A grounded node has an empty closure. A derived `x` is
`alive` only if every direct source is `alive` (the rule), and by the induction hypothesis each of those has an
all-`alive` closure. ∎

**Theorem (O1 and O2).** After Phase A in sources-first order and Phase B in the reverse order, O1 and O2 hold.
*Proof sketch.* Take a survivor `x`. It is `alive` or `re-derived`, so by the rule every source it kept had a
final fate in `{alive, re-derived}` (Lemma 1); it therefore holds no edge to a deleted or retired node (O1).
If `x` was re-derived, its closure changed, so by Lemma 2 it is stale, hence not `verified ∧ ¬stale` (O2). If
`x` was `alive`, Lemma 3 says nothing in its closure changed, so no deleted or retired node is in it and its
closure hash is unchanged. ∎

*Correction (r8 review).* An earlier draft classified `x` as `alive` whenever its direct sources survived, and
stated "no source in its closure changed". That was false: with `2 ← {0, 1}`, `3 ← {2}` and `0` deleted, node 2 is
re-derived and node 3 stayed `alive` with deleted node 0 in its closure. O1 and O2 still held through Lemma 2, but
node 3 kept **stored content derived from node 2's pre-deletion content**, i.e. from deleted material. The rule now
re-derives every dependent of a re-derived source. The exhaustive model shows the difference: the earlier rule
left 698 `alive` nodes with a changed node in their closure over all DAGs of up to five nodes; the corrected rule
leaves 0 (`test_an_alive_node_has_no_deleted_or_retired_node_anywhere_in_its_closure`).

**What the theorem needs.** `G` must be acyclic. Derived rows are created after their sources and
`memory_version` content is immutable, so cycles cannot arise through the engine's own writes, but nothing in
the schema forbids one. Section 5 (F9) names the check.

### 3.1 The argument is checked, not only stated

`tests/test_deletion_order_model.py` is a pure reference model of this rule. It enumerates **every**
derivation DAG of up to five nodes, every non-empty seed set and thresholds 1 and 2:

| Nodes | Cases | Sources-first violations | Dependents-first violations |
|---:|---:|---:|---:|
| 3 | 112 | **0** | 3 (2.7%) |
| 4 | 1,920 | **0** | 160 (8.3%) |
| 5 | 63,488 | **0** | 10,838 (17.1%) |

The smallest counterexample for dependents-first classification: node `1` is derived from node `0`, node `2`
is derived from `0` and `1`, and `0` is deleted. Node `1` loses its only source and is deleted, but node `2`,
classified first, judges `1` still alive and stays attached to it. The model is of the rule, not of the engine; `E-037` must implement the same rule
against PostgreSQL.

## 4. What v1 does today, and where V2 differs

Facts taken from `engine/deletion.py` and `tests/integration/test_retrieval_deletion_db.py` at `490904d`.

| Aspect | v1 | V2 requirement | Consequence |
|---|---|---|---|
| Classification order | Ascending `(depth, id)`, with an `available` set of already-surviving dependents | Sources before dependents | **Consistent with the rule.** The architecture's phrase "deepest first" is the *apply* order, not the classification order; the text should say so. |
| Closure | Recursive walk bounded at `MAX_LINEAGE_DEPTH = 3` | Unbounded, with cycle detection | The bound is a correctness limit in v1 (see next row). |
| Dependents beyond the bound | Not visited; the edge from such a dependent to a deleted source is removed, and the dependent is otherwise left as it was | Reconciled | **`test_deletion_closure_stops_at_depth_three` asserts that `depth-4` survives deletion of its transitive source.** That is the opposite of `I8`/`O3`. `E-037` must record it as a superseded v1 invariant (the Tier-A wall rule in `BASELINE.md`), not fix it silently. |
| Which dependents are re-derived | Only rows whose every edge is of type `summary` and that have more than one source | Every dependent that keeps a surviving source | Other dependents (for example `consolidation`) keep their stored text and receive `re_derivation` edges. Whether that text still carries deleted material is not checked. |
| Edge restoration order | Summaries are rebuilt **before** surviving dependents' edges are restored (fixed in `d087eb7`) | Same | This is an ordering defect that already happened once; see F4. |
| Retire | Not implemented | `retire(reason = 'source_deleted')` | New; see the open question in 6. |

## 5. Ways the ordering could be violated, and the test that would catch each

Test names are requirements for `E-037`/`E-038` (file `tests/integration/test_deletion_through_cognition_db.py`
unless noted). A test marked *existing* is already in the repository.

| ID | Violation | Observable symptom | Test |
|---|---|---|---|
| F1 | A dependent is classified before its sources' fates are known | A survivor stays attached to a source that is then deleted or retired | `test_dependent_sees_final_fate_of_its_sources`; model: `tests/test_deletion_order_model.py::test_the_smallest_counterexample_for_dependents_first_classification` (*existing*) |
| F2 | Closure cut off at a depth bound | A deep dependent stays `verified ∧ ¬stale` over deleted data | `test_chain_longer_than_any_bound_is_fully_reconciled` (a chain at least two longer than the largest configured bound); model: `test_the_v1_depth_bound_does_not_reach_dependents_beyond_it` (*existing*) |
| F3 | Edges removed before dependents are classified | Classification cannot see the lineage it needs | `test_classification_reads_edges_before_they_are_removed` |
| F4 | Surviving edges restored before a rebuilt summary is recreated | Lineage from a rebuilt summary to its own dependents is lost, so a later deletion misses them | `test_dependent_of_a_rebuilt_summary_keeps_lineage_and_is_deleted_later` (*existing*, `tests/integration/test_retrieval_deletion_db.py`) |
| F5 | A verification that began before the deletion commits after it | A new `verified` artifact over deleted sources appears after the deletion | `test_verification_started_before_deletion_cannot_commit` (extends the v1 stale-epoch guard to `verify`); v1 analogue for jobs: `test_stale_worker_epoch_is_rejected` (*existing*) |
| F6 | A closure-hash cache is not invalidated by the deletion | `stale` is computed from an old hash and reads `false` | `test_closure_hash_cache_is_invalidated_inside_the_deletion_transaction` (`E-038`) |
| F7 | A re-derivation or retire fails midway | Partial deletion visible | `test_deletion_failure_during_rebuild_rolls_back_all_writes` (*existing*); extend to the retire path |
| F8 | The threshold comparison is off by one | An artifact at exactly `t` surviving sources is retired, or one below `t` is re-derived | `test_support_exactly_at_threshold_is_rederived_and_one_below_is_retired` |
| F9 | A cycle in the derivation graph | Classification has no valid order; recursion never ends | `test_cyclic_derivation_is_rejected_at_write_time` plus a closure test that terminates on an injected cycle (a recursive query using `CYCLE` or a visited set) |
| F10 | An association survives pointing at a deleted artifact | The identifier or its metadata outlives the content (`ASSOCIATION` does not cascade, `E-014`) | `test_deleting_an_artifact_removes_its_associations_but_not_the_associated_artifacts` |
| F11 | An export snapshot made before the deletion is served after it | Channel 11 leak | `test_export_snapshot_taken_before_deletion_is_not_served_after` |
| F12 | A re-derived artifact keeps the old `verification_status = verified` while rendered in `facts` | `verified ∧ ¬stale` over a changed closure | `test_rederived_dependent_renders_in_hypotheses_until_reverified` |
| F13 | Two deletions race on overlapping closures | Lost update or deadlock | `test_concurrent_deletions_with_overlapping_closures_serialize` (per-user advisory lock, as in v1) |

### 5.1 Scenarios for the falsification tests

`a ← b` means `a` is derived from `b`. "Delete" lists the seeds; outcomes use the rule of section 2 with `t = 1`
unless stated.

| Test | Graph | Delete | Expected outcome per node |
|---|---|---|---|
| F1 `test_dependent_sees_final_fate_of_its_sources` | `1 ← 0`, `2 ← {0, 1}` | `0` | 0 deleted, 1 deleted (no source left), 2 deleted (no source left) |
| F2 `test_chain_longer_than_any_bound_is_fully_reconciled` | chain `v1 ← v0 … v9 ← v8` (length 10 > 3) | `v0` | v0 … v9 all deleted |
| Depth-9 staleness `test_depth_nine_abstraction_becomes_stale_without_a_walk` | `v1 ← {v0, w}`, `v2 ← v1` … `v9 ← v8`, `v9` a verified abstraction | `v0` | v1 re-derived on `w`; v2 … v9 re-derived; v9's closure hash differs, so `stale = true` and it renders in `hypotheses`, not `facts` |
| Diamond `test_diamond_dependent_reconciled_once_with_final_fates` | `1 ← 0`, `2 ← 0`, `3 ← {1, 2}` | `0` | 1 and 2 deleted; 3 deleted (no source left); 3 visited once |
| Shared source `test_dependent_with_one_surviving_and_one_deleted_source` | `2 ← {0, 1}` | `0`, with `t = 1` and `t = 2` | `t = 1`: 2 re-derived on `{1}`; `t = 2`: 2 retired (`source_deleted`) |
| Transitive re-derivation `test_dependent_of_a_rederived_source_is_regenerated` | `2 ← {0, 1}`, `3 ← 2` | `0` | 2 re-derived on `{1}`; 3 re-derived (its source's content changed), never `alive` |
| F4 (existing) `test_dependent_of_a_rebuilt_summary_keeps_lineage_and_is_deleted_later` | summary `S ← {a, b}`, `D ← S` | `a`, then later `S` | `S` rebuilt on `{b}` with `D ← S` kept; the later deletion of `S` deletes `D` |
| F8 `test_support_exactly_at_threshold_is_rederived_and_one_below_is_retired` | `x ← {s1 … s(t+1)}` | one source, then two | exactly `t` survivors: re-derived; `t - 1 ≥ 1`: retired; `0`: deleted; parameterised over `t` |
| F9 `test_cyclic_derivation_is_rejected_at_write_time` | insert `b ← a` then `a ← b` | — | the second insert is rejected; an injected cycle does not hang the closure |
| F10 `test_deleting_an_artifact_removes_its_associations_but_not_the_associated_artifacts` | `x —contradicts— y` (association, not provenance) | `x` | x deleted; the association row deleted; y untouched |
| F13 `test_concurrent_deletions_with_overlapping_closures_serialize` | `2 ← {0, 1}` | `0` and `1` in two concurrent transactions | both commit; 2 deleted exactly once; no deadlock |

## 6. Relationship to the two depth constants

There are two depth-3 constants, and they are a matched pair:

| Constant | Where | Side | Role in v1 |
|---|---|---|---|
| `MAX_LINEAGE_DEPTH = 3` | `deletion/closure.py`, used by `engine/deletion.py` | read (deletion walk) | decides which dependents a deletion reaches |
| `MAX_DERIVATION_DEPTH = 3` | `consolidation/consolidator.py` | write (consolidation) | refuses to create a consolidation deeper than 3 (`derivation_depth_exceeded`), "so a consolidation the deletion walk cannot see is never created" |

In v1 the deletion bound is complete only because every *consolidation* respects the write cap. The pairing is
enforced by a code comment, not by the database: any other writer of `memory_derivation` (re-derivation edges,
direct inserts) can create a deeper chain, which is exactly what `test_deletion_closure_stops_at_depth_three`
constructs. Two consequences for V2:

1. **Order of change.** The write cap must not be raised or removed before `E-037`'s unbounded closure exists;
   otherwise consolidation would create dependents that deletion cannot reach (`I8` violated silently).
2. **After `E-037`.** The read side becomes a performance parameter (below). The write side may then remain as
   a *cost* limit on consolidation depth, but it no longer protects correctness, and nothing may depend on it.

For `MAX_LINEAGE_DEPTH`: in v1 the constant is a **correctness bound**: it decides which dependents are reached, and
`test_deletion_closure_stops_at_depth_three` encodes that. Under V2 it must become a **performance
parameter**: it may bound a single pass, but only if everything beyond it is provably unreachable or the work
continues asynchronously behind a registered completion barrier (an `in_progress` state on the deletion
request), never a silent cap (`ARCHITECTURE_V2.md` section 10 rule 1). Because staleness is derived from the
closure hash (Lemma 2), a depth-9 abstraction becomes stale without a walk reaching it; what depth still
controls is the *re-derive/retire/delete* decision, which does need the walk. So the bound can be a tuning
knob for latency, but a deletion that returns before the walk finishes must not report completion.

### 6.1 Concurrency and lock order (#57 section 3.6)

**v1.** Deletion takes `pg_advisory_xact_lock(hashtext(user_id))`, shared with the queue worker, so two
deletions for one user serialise, and deletions for different users never touch each other's rows: every
derivation edge stays inside one tenant (RLS + `user_id` on both endpoints). No cross-lock ordering is needed, so
v1 cannot deadlock on these locks.

**V2.** Serialisation is per scope (`I9`), and a derivation may cross scopes: an abstraction in scope A derived
from a source in scope B. Deleting a seed in B must reconcile the dependent in A, so it needs **both** scope
locks. If another transaction deletes in A while its closure reaches B, taking locks in discovery order
(own scope first) deadlocks: T1 holds B and waits for A, T2 holds A and waits for B.

**Rule (decided, `V2-D51`; implemented as `scope.locks`):** the key is the scope's owner namespace
`/u/<user>/` in W1 (`MIGRATION_RISK.md` D10, v1-equivalent); a finer per-scope key would not change the rule.
1. Compute the closure **without** scope locks (a read under the snapshot), and collect its scope set `S`.
2. Acquire every lock in `S` in **one global order** (byte order of the scope path) before any write.
3. Recompute the closure under the locks. If its scope set grew beyond `S`, roll back and retry with the
   larger set (advisory transaction locks cannot be released early). Bound the retries and fail closed with a
   static error.
4. A cycle of waits is then impossible: every transaction takes locks in the same total order. PostgreSQL's
   deadlock detector (`40P01`) remains the backstop; the retry must be explicit (tenacity), never silent.

Verified now on the lock model (`tests/test_scope_locks.py`, exhaustive over every interleaving):
`test_opposite_closures_in_discovery_order_can_deadlock` and `test_discovery_order_deadlocks_for_some_three_way_cycle`
reproduce the deadlock; `test_the_same_closures_in_global_order_cannot_deadlock` and
`test_no_combination_of_closures_in_global_order_can_deadlock` show the rule removes it;
`test_closure_growth_under_the_locks_requires_a_retry` covers step 3. Database tests owned by `E-037`:
`test_cross_scope_deletions_in_opposite_directions_do_not_deadlock` (two scopes A and B with `A:x ← B:s` and
`B:y ← A:t`; delete `s` and `t` concurrently; both commit, `x` and `y` are reconciled) and
`test_closure_growth_under_lock_triggers_a_retry_not_a_partial_deletion`.

**Threshold independence (#57 section 3.7).** The argument holds for every threshold `t ≥ 1`: Lemmas 1-3 and the
Theorem never use its value. The model checks `t ∈ {1, 2}` exhaustively and
`test_support_exactly_at_threshold_is_rederived_and_one_below_is_retired` checks `t ∈ {1, 2, 3}`. The
database tests must parameterise `τ` the same way, because `V2-D30` leaves its value open.

## 7. Decisions (r9; formerly open questions)

| # | Question | Decision | Record | Verified by |
|---|---|---|---|---|
| 1 | Do retired-by-deletion artifacts keep their content? | **No: tombstone.** Id, scope, kind, lifecycle `retired`, reason `source_deleted` and the audit row stay; content, statements, rendered bounds and vectors are removed in the deletion transaction. The deletion boundary (`I13`, scanner channels 1-11) outranks audit convenience; `audit_as_of` still shows that the artifact existed and why it was retired | `V2-D50` | `E-037` scanner suite (channels 9-11); model: `retired` has no content-bearing output |
| 2 | Non-summary dependents with a surviving source | **Re-derived, whatever their derivation type.** No text is declared source-independent. A dependent that has no re-derivation builder is retired (and so tombstoned) | `V2-D50` | `E-037`; v1 behaviour (summary-only) recorded as superseded in section 4 |
| 3 | Lock order for cross-scope closures | **Adopted** (section 6.1), key = owner namespace in W1 | `V2-D51` | `tests/test_scope_locks.py` |
| 4 | Transitive re-derivation | **Adopted**: a dependent is `alive` only if every source is alive | `V2-D50` | `tests/test_deletion_order_model.py` (old rule 698 counterexamples, new 0) |
| 5 | Wording of architecture section 10 | **Corrected**: classify sources-first, apply dependents-first | `V2-D50` | `ARCHITECTURE_V2.md` section 10 note |

## 8. Acceptance traceability (`MA-014`)

| Criterion (#57 section 6) | Where |
|---|---|
| Invariant stated formally (pre/post-conditions per transaction) | section 1 (P1-P3, O1-O4) |
| Proof sketch covers depth, cycles, diamonds and concurrent deletions | section 3 (Lemmas 1-3, Theorem; P1 for cycles), 5.1 (diamond), 6.1 (concurrency) |
| Every item in #57 section 3 has a named falsification test with its scenario | sections 5 and 5.1: depth (F2, depth-9), cycles (F9), diamonds and shared sources, rebuild-then-edge (F4), staleness (depth-9), concurrency (F13, 6.1), threshold (F8, parameterised) |
| Relationship to both depth constants explicit | section 6 |
| Lock order stated or open decision recorded | section 6.1 (decided, `V2-D51`; `scope.locks`) |
