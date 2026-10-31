# Four-Channel Contract

**Task:** `MA-013` · **Status: frozen (r9, `V2-D48`).** Every proposal in section 5 is decided; a change needs a
new `V2-Dnn`. Specification plus an executable reference model; the database
implementation belongs to `E-017`, the fuzz to `E-035`. **References:** `ARCHITECTURE_V2.md` sections 2.2, 2.3,
7, 9.1-9.3, `V2-D24`, `V2-D28`, `INVARIANT_LEDGER.md` (`I1′`, `I14`), `THREAT_MODEL.md` (T-10).

The original draft listed three channels and never said where a **contradiction** goes, or whether a
**verified abstraction** is a fact or a hypothesis. This document freezes the contract that `E-017` is
written against: which channel an artifact belongs to, and what a rendered item must carry.

## 1. Membership is a pure function

Channel membership depends on five inputs and nothing else: not on rank, score, budget, caller, time of
day or the query.

| Input | Values | Notes |
|---|---|---|
| `kind` | `episode` · `claim` · `procedure` · `abstraction` | |
| `lifecycle` | `observed` · `asserted` · `inferred` · `contested` · `retired` | closed table in section 2.3 of the architecture |
| `verification` | `none` · `unverified` · `verified` · `refuted` | `none` for grounded artifacts; `refuted` always retires (`inferred → retired`) |
| `stale` | boolean, **derived** (section 9.2) | never stored |
| `open_contradiction` | boolean, **derived** | see 2.2 |

Output: one of `facts`, `hypotheses`, `procedures`, `conflicts`, or `none`.

`none` is not a fifth answer channel; it means "rendered in no channel" (the name `MA-013`'s issue and `E-017`
use). It is where `retired` artifacts go: excluded from every answer channel, visible to `audit_as_of`, still
inside the deletion closure (section 2.3 of the architecture). "Maps to exactly one output" is therefore exactly
one of the four answer channels, or `none`, which is the only reading under which `retired` is total.

```text
channel(kind, lifecycle, verification, stale, open_contradiction):
    if lifecycle == retired:                                    → none
    if lifecycle == contested or open_contradiction:            → conflicts
    if lifecycle == inferred:
        if kind == abstraction and verification == verified and not stale:
                                                                → facts        # rendered with its bound
        else:                                                   → hypotheses
    if kind == procedure:                                       → procedures
    else:                                                       → facts        # observed / asserted
```

### 1.1 The legal tuple space

Not every combination can exist. An abstraction is never `observed` or `asserted` (`inferred → asserted`
does not exist); an episode is never `asserted`; a procedure is never `observed`; grounded artifacts carry no
verification; only grounded artifacts can have an open contradiction; `stale` requires `verified`. Over
these rules there are **60 legal tuples** (15 episode, 17 claim, 15 procedure, 13 abstraction), which map as:

| Channel | Legal tuples |
|---|---:|
| `facts` | 4 |
| `hypotheses` | 11 |
| `procedures` | 1 |
| `conflicts` | 24 |
| `none` | 20 |

`conflicts` and `none` are large only because `contested` and `retired` artifacts may carry the history
of their verification; their routing does not depend on it.

**Legality is enforced by the lifecycle table on `main` (`V2-D38`, `V2-D48`).** `model.axes.TRANSITIONS` is
kind-blind; before r9 it reached exactly **four** pairs this contract declares illegal: `abstraction/asserted`,
`abstraction/observed`, `episode/asserted` and `procedure/observed` (`abstraction/asserted` via `inferred →
contested → asserted`, which would turn an inference into a fact by adjudication, against `I1′`). Decision C-P1
added `axes.LEGAL_LIFECYCLE`; `require_transition(source, target, kind)` and `validate_axes` reject any
lifecycle illegal for the kind. `test_the_lifecycle_table_reaches_exactly_the_legal_tuple_space` shows the
reachable pairs now **equal** the 16 legal ones, and `test_without_kind_conditioning_four_illegal_pairs_would_be_reachable`
(in `tests/test_model_axes.py`) keeps the mutation visible. Every output of the v1 backfill is legal. `E-016`
enforces the same table in the database.

### 1.2 The contract is executed, not only written

`tests/test_channel_membership_model.py` enumerates all 60 legal tuples and checks, **with five channel
predicates written independently from the statements of section 9.1** (so that "exactly one" is a property of
the specification and not a tautology of one `if` chain):

- each legal tuple satisfies exactly one predicate, and `channel()` returns that one;
- `contested` content appears only in `conflicts`, never in `facts`;
- a settled item with an open contradiction is not a fact;
- nothing grounded is a hypothesis, and nothing inferred is a procedure;
- only a *fresh, verified abstraction* presents inferred content as a fact;
- a stale verified abstraction is demoted to `hypotheses`;
- `retired` content is in no answer channel;
- illegal tuples are rejected (`illegal_artifact_tuple`), not routed.

## 2. Decisions the specification left implicit

Each of these is a choice this document had to make to make the function total. They are stated here so they
can be challenged. All are decided by `V2-D48` (r9).

### 2.1 A verified *inferred claim or procedure* is a hypothesis

Section 9.1 puts only *verified abstractions* in `facts`, and `I1′` forbids rendering an inferred artifact as
knowledge. Neither mentions a verified inferred claim. Rule: **only an abstraction can present a verified
result as a bounded fact**; a verified inferred claim, episode or procedure stays in `hypotheses`.

### 2.2 What counts as an *open contradiction*, and who may create one

Section 9.1 moves an `asserted` artifact with "an open `contradicts` association" out of `facts` and into
`conflicts`. The public API lets Tier B call `link` (section 8.1). Read literally, **a Tier B service could
demote any grounded fact out of `facts` by linking a `contradicts` edge to it**, which is the same power as
adjudicating a conflict, which the architecture denies to Tier B on purpose.

**Decided (`V2-D48`, C-P4):** an `open_contradiction` exists only where a `contradicts` association is `open`, **both
endpoints are grounded** (`observed` or `asserted`), and the edge was created by the grounding policy or by a
user. A `contradicts` edge from or to an inferred artifact acts on the **inferred** artifact (it can move it
`inferred → contested`, which requires grounded counter-evidence, section 2.3) and never on a grounded one. A
Tier B `contradicts` edge is stored as an association (`origin='tier_b'`) with no channel effect. The rule is on
the API matrix (`link`: `grounded_endpoint_protected`, `API_V2.md` 4.5) and in the reference model
(`opens_contradiction`, `test_a_tier_b_contradicts_edge_never_demotes_a_grounded_fact`).

### 2.3 Staleness has five causes, not one

`stale` is derived (`V2-D24`). Section 9.2 gives the closure-hash mismatch and notes "also stale if support
dropped below the verification threshold, if any source was retired, or if the method version changed".
Section 7 adds `expires_at` ("abstractions decay"), and `USER_MODEL.md` requires facet decay to move an item
to `stale`. The contract uses one boolean that is the **OR of all five**:

1. closure hash differs from `closed_over_hash`;
2. support fell below the verification threshold;
3. any source was retired;
4. the method version changed;
5. `now > expires_at`.

Without (5) an expired preference would still render as a verified fact.

### 2.4 Candidates and rejected writes are not artifacts

The admission flow in section 2.2 ends in `rejected, with reason code`, and v1 stores `candidate`,
`conflict_pending` and `rejected` rows. The closed lifecycle table has no such values. They are gate
decisions, not artifacts, and have no channel. How v1 staging rows are migrated is decided by `V2-D38` (`candidate`
and `rejected` → `retired` with a reason code; `MIGRATION_RISK.md` D2).

### 2.6 Episodes

An `observed` episode is a fact: it is a record of what a declared source said, with provenance (section 9.1
puts `observed` in `facts`). Episodes are never `asserted`, since nothing about them is adjudicated, and an `inferred`
episode (a reconstructed event) is a hypothesis like any inference.

### 2.5 `procedures` is about eligibility; matching is retrieval

A procedure belongs to `procedures` when it is grounded and settled. Whether its `preconditions` match the
current task is the retrieval planner's filter (`E-031`), not part of membership, so that the function stays
independent of the query.

## 3. What a rendered item must carry

Membership decides *where*; these rules decide *what*. They are testable against the assembled output.

| ID | Rule | Source |
|---|---|---|
| R1 | Every item carries a provenance bundle. Assembly **refuses** an item whose provenance cannot be materialised. | 9.3 |
| R2 | A bounded abstraction in `facts` renders as a **summary with its bound** ("in 34 of 40 observed sessions, …"), not as a bare proposition. The bound is part of the rendered text and of the item's structure, not optional metadata a caller can drop. | 9.1, `I14` |
| R3 | The bound is exactly: support count `k`, opportunity count `o`, `confidence_lower`, `coverage`, `method_id@version`, `verified_on`, `expires_at`. An item missing any of them is not renderable. | 7 |
| R4 | A `hypotheses` item carries its support summary and uncertainty and a visible label: `unverified`, or `verified, needs re-verification` when stale. | `I1′`, 9.2 |
| R5 | A `conflicts` item shows **both sides** with ids and states; it never merges them or picks one. | 9.1, Invariant 6 |
| R6 | Provenance to a scope the caller cannot read is opaque: a per-caller opaque handle with **no** content, scope path, scope kind or source type (**decided, `V2-D37`**; `scope.provenance.render_bundle`). | 9.3, `I17`, `V2-D37` |
| R7 | Each channel has its own token budget; the result reports per-channel consumption (`coverage`) so a caller can see that `hypotheses` crowded out `facts`. A floor for `facts` is reserved before `hypotheses` is filled. | 9.3 |
| R8 | The temporal predicate is a **filter**, never a score term. | 9.3, v1 |

## 4. Property-test sketch

The first group (P1-P5) is covered by the reference model; the second (P6-P9) needs the assembled output of `E-017` and is
`E-035`'s fuzz target. Written as Hypothesis-style properties (the repository does not depend on Hypothesis; the
model enumerates exhaustively instead because the space is small).

```python
# P1-P4 are implemented in tests/test_channel_membership_model.py; P5 holds by construction there
# (channel_of takes no rank, score or budget argument) and is re-checked on the assembled output by E-017
@given(legal_item())
def test_P1_total_and_exclusive(item):        # exactly one predicate holds; channel() returns it
@given(legal_item())
def test_P2_contested_only_in_conflicts(item):
@given(legal_item())
def test_P3_inferred_in_facts_only_if_fresh_verified_abstraction(item):
@given(fresh_verified_abstraction())
def test_P4_stale_demotes(item):              # flipping stale (or expiry) moves facts -> hypotheses
@given(legal_item(), scores(), budgets())
def test_P5_membership_ignores_rank_score_and_budget(item, s, b):   # same tuple, same channel

# P6-P9: need the assembled result (E-017 / E-035)
@given(corpus(), query(), budgets())
def test_P6_every_abstraction_in_facts_renders_with_its_bound(corpus, q, b):
    for item in assemble(corpus, q, b).facts:
        if item.kind == "abstraction":
            assert item.bound is not None and str(item.bound.k) in item.text   # R2, R3
@given(corpus(), query(), budgets())
def test_P7_channel_budgets_and_coverage(corpus, q, b):      # each channel <= its cap; facts floor honoured
@given(corpus(), query(), scope_selectors())
def test_P8_provenance_to_unreadable_scope_is_opaque(corpus, q, sel):    # T-10, V2-D37
    # no opaque reference contains a foreign scope path, scope id, source type or content
    for item in all_items(assemble(corpus, q, sel)):
        for ref in item.provenance:
            assert ref.is_opaque or reader_can_read(sel, ref.scope)
@given(corpus(), deletion())
def test_P9_deleting_a_source_never_promotes_to_facts(corpus, d):         # monotone: facts only shrink
```

## 5. Decisions (`V2-D48`, r9)

All six proposals were accepted under Project Owner authority (`APPROVAL.md` Amendment 2), each because the
alternative weakened `I1′`, `I14` or Tier B's inability to adjudicate.

| ID | Decision | Basis | Verification |
|---|---|---|---|
| C-P1 | Lifecycle legality is **kind-conditioned**: an abstraction is never `asserted`/`observed`, an episode never `asserted`, a procedure never `observed`; `adjudicate(confirm)` on a contested abstraction or episode is `precondition_failed` | `I1′`, `I14`; #67 requires `inferred → asserted` unrepresentable, and `inferred → contested → asserted` reached it | `tests/test_model_axes.py` (reachable = legal per kind; mutation shows the four pairs), `test_the_lifecycle_table_reaches_exactly_the_legal_tuple_space` |
| C-P2 | A verified *inferred claim or procedure* stays in `hypotheses`; only an abstraction renders a verified result as a bounded fact (2.1) | 9.1, `I1′` | `test_a_verified_inferred_claim_or_procedure_is_still_a_hypothesis` |
| C-P3 | `stale` is the OR of five causes, including `now > expires_at` (2.3) | 9.2, section 7, `USER_MODEL.md` | `test_each_staleness_cause_alone_demotes_a_verified_abstraction` (each cause alone); property P4 |
| C-P4 | Only a `contradicts` edge between two grounded endpoints, created by the grounding policy or Tier C, opens a contradiction; a Tier B edge has no channel effect (2.2). **Security:** closes the path by which any Tier B service could move any fact out of `facts` | Tier B may not adjudicate (TB2) | `test_a_tier_b_contradicts_edge_never_demotes_a_grounded_fact`; `API_V2.md` `link` condition `grounded_endpoint_protected` (`tests/test_tier_permissions.py`) |
| C-P5 | `refuted` always retires the artifact (`inferred → retired`); `inferred ∧ refuted` is illegal (section 1) | 2.3 transition table | `test_illegal_tuples_are_rejected_rather_than_routed` |
| C-P6 | An asserted artifact with an open contradiction is in `conflicts`, not `facts`; `ARCHITECTURE_V2.md` 9.1 now says so | Invariant 6 | `test_a_settled_item_with_an_open_contradiction_is_not_a_fact` |

Decided earlier and cited here: opaque provenance (`V2-D37`, R6); staging rows (`V2-D38`, section 2.4).

Answers to #56's open questions: Q1 inferred procedures → `hypotheses` (C-P2); Q2 verified inferred claims →
`hypotheses` (C-P2); Q3 refuted → always `retired` (C-P5); Q4 observed episodes → `facts` (section 2.6);
Q5 opaque provenance → `V2-D37` (R6).

## 6. Acceptance traceability (`MA-013`)

| Criterion | Where |
|---|---|
| Membership is total: every legal tuple maps to exactly one output | 1, 1.1, 1.2; `test_every_legal_tuple_belongs_to_exactly_one_channel` (60 cases); consistency with `V2-D38`/`V2-D48`: `test_every_v1_backfill_output_is_a_legal_tuple`, `test_the_lifecycle_table_reaches_exactly_the_legal_tuple_space` |
| Contested content appears only in `conflicts` (consistent with Invariant 6) | 1, `test_contested_content_appears_only_in_conflicts` |
| A bounded abstraction in `facts` carries its support statement as part of the rendered item | R2, R3; property P6 |
| Deliverable: property-test sketch | 4 |
