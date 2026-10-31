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
|survivors(x)| = |S(x)|       → alive                       # nothing it depends on was touched
|survivors(x)| ≥ t            → re-derive on survivors(x); x is now stale (its closure hash changed)
otherwise                     → retire(reason = 'source_deleted')
```

## 3. Argument

**Lemma 1 (well-founded).** If `G` is acyclic, visiting nodes in an order where every source precedes its
dependents classifies each node using only final fates of its sources. *Proof:* by induction along the order;
`fate(x)` depends only on `{fate(s) : s ∈ S(x)}`, all already final. ∎

**Lemma 2 (staleness is transitive without a walk).** `stale(a) ⟺ verified ∧ closure_hash(a.sources_now) ≠
a.closed_over_hash`. Deleting or retiring any node in `a`'s closure changes that hash, so every ancestor
becomes stale by construction, at any depth (section 9.2). ∎

**Theorem (O1 and O2).** After Phase A in sources-first order and Phase B in the reverse order, O1 and O2 hold.
*Proof sketch.* Take a survivor `x`. It is `alive` or `re-derived`, so by the rule every source it kept had a
final fate in `{alive, re-derived}` (Lemma 1); it therefore holds no edge to a deleted or retired node (O1).
If `x` was re-derived, its closure changed, so by Lemma 2 it is stale, hence not `verified ∧ ¬stale` (O2). If
`x` was `alive`, no source in its closure changed, so no deleted node is in its closure. ∎

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

## 6. Relationship to `MAX_LINEAGE_DEPTH = 3`

In v1 the constant is a **correctness bound**: it decides which dependents are reached, and
`test_deletion_closure_stops_at_depth_three` encodes that. Under V2 it must become a **performance
parameter**: it may bound a single pass, but only if everything beyond it is provably unreachable or the work
continues asynchronously behind a registered completion barrier (an `in_progress` state on the deletion
request), never a silent cap (`ARCHITECTURE_V2.md` section 10 rule 1). Because staleness is derived from the
closure hash (Lemma 2), a depth-9 abstraction becomes stale without a walk reaching it; what depth still
controls is the *re-derive/retire/delete* decision, which does need the walk. So the bound can be a tuning
knob for latency, but a deletion that returns before the walk finishes must not report completion.

## 7. Open questions for the owner

1. **Retired-by-deletion artifacts keep their content.** The `retire` branch keeps the row, and a retired
   artifact "remains visible to `audit_as_of`" and "remains in the deletion closure" (section 2.3). An
   abstraction retired because its support fell below threshold may still *contain* statements derived in
   part from deleted material. If so, the storage channel and channel 9 (abstraction statements) of the
   scanner will find them. Decide whether retire-by-deletion tombstones the content (keeping id, scope, type
   and reason) or whether this is an accepted, documented exception to the deletion boundary.
2. **Non-summary dependents with a surviving source** keep their stored text (section 4). Decide whether
   `consolidation`/`corroboration` dependents must also be re-derived, or whether their text is declared
   independent of any single source.
3. **Wording.** Section 10 of the architecture should distinguish the classify order (sources first) from the
   apply order (dependents first); the current "closure order (deepest first)" is correct only for the second.

## 8. Acceptance traceability (`MA-014`)

| Criterion | Where |
|---|---|
| Ordering rule stated as an invariant with a proof sketch and a counterexample analysis | sections 1, 2, 3, 5 |
| Each way the ordering could be violated has a named test | section 5 (F1-F13) |
| Relationship to `MAX_LINEAGE_DEPTH = 3` explicit: performance parameter, not correctness bound | section 6 |
