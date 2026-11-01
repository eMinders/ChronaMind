"""Four-channel membership and assembly (E-017). The reference model is the specification's executable form."""

from dataclasses import replace
from datetime import UTC, datetime
from itertools import product

import pytest

import tests.test_channel_membership_model as reference
from custom_memory_engine.engine import channels
from custom_memory_engine.engine.channels import (
    ArtifactView,
    Bound,
    BoundedFact,
    ChannelError,
    ConflictItem,
    ConflictSide,
    StalenessInputs,
    assemble,
    channel_of,
    never_stale,
    opens_contradiction,
    render_bounded_text,
    stale_of,
)
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.scope.provenance import OpaqueReference, ProvenanceKey, ProvenanceSource, VisibleReference

KEY = ProvenanceKey(b"k" * 32)
CALLER = "u1"
READ_SET = resolve_read_set(CALLER, [Selector.of("/u/u1/")], [])
EXPIRES = datetime(2027, 6, 1, tzinfo=UTC)
BOUND = Bound(34, 40, 0.91, 0.72, "cooccurrence", "3", "split-7", EXPIRES)
OWN_SOURCE = ProvenanceSource("src-own", "/u/u1/", "private", "episode", "consolidation")
FOREIGN_SOURCE = ProvenanceSource("src-foreign", "/u/other-tenant/p/secret-project/", "private", "episode", "summary")


def view(kind="claim", lifecycle="asserted", verification="none", **overrides):  # noqa: ANN001, ANN003, ANN201
    base = {
        "artifact_id": "a1",
        "kind": kind,
        "lifecycle": lifecycle,
        "verification": verification,
        "text": "the user prefers tea",
        "sources": (OWN_SOURCE,),
    }
    base.update(overrides)
    return ArtifactView(**base)


def run(views, staleness=never_stale):  # noqa: ANN001, ANN201
    return assemble(views, caller_user_id=CALLER, read_set=READ_SET, key=KEY, staleness=staleness)


# --- fidelity and totality against the reference model (requirements 1 and 2) ------------------------


def test_the_implementation_equals_the_reference_model_on_every_legal_tuple() -> None:
    assert len(reference.ITEMS) == 60
    for item in reference.ITEMS:
        got = channel_of(item.kind, item.lifecycle, item.verification, item.stale, item.open_contradiction)
        assert got == reference.channel_of(item), reference.label(item)


def test_legality_agrees_with_the_reference_model_on_the_whole_product_space() -> None:
    """Illegal tuples must raise in both implementations, and the set of illegal tuples must be identical."""
    space = product(reference.KINDS, reference.LIFECYCLES, reference.VERIFICATIONS, (False, True), (False, True))
    seen = 0
    for kind, lifecycle, verification, stale, contradicted in space:
        item = reference.Item(kind, lifecycle, verification, stale, contradicted)
        seen += 1
        if reference.is_legal(item):
            assert channels.is_legal_tuple(kind, lifecycle, verification, stale, contradicted)
        else:
            assert not channels.is_legal_tuple(kind, lifecycle, verification, stale, contradicted)
            with pytest.raises(ChannelError, match="illegal_artifact_tuple"):
                channel_of(kind, lifecycle, verification, stale, contradicted)
    assert seen == 4 * 5 * 4 * 2 * 2


@pytest.mark.parametrize("item", reference.ITEMS, ids=reference.label)
def test_every_legal_tuple_yields_exactly_one_output(item: reference.Item) -> None:
    out = channel_of(item.kind, item.lifecycle, item.verification, item.stale, item.open_contradiction)
    assert out in {"facts", "hypotheses", "procedures", "conflicts", "none"}


def test_the_membership_function_takes_no_rank_score_or_budget() -> None:
    assert list(channel_of.__annotations__) == [
        "kind",
        "lifecycle",
        "verification",
        "stale",
        "open_contradiction",
        "return",
    ]


# --- no factification and contested isolation (requirements 3 and 4) --------------------------------


def test_only_a_fresh_verified_abstraction_presents_inferred_content_as_a_fact() -> None:
    for item in reference.ITEMS:
        out = channel_of(item.kind, item.lifecycle, item.verification, item.stale, item.open_contradiction)
        if item.lifecycle == "inferred" and out == "facts":
            assert (item.kind, item.verification, item.stale) == ("abstraction", "verified", False)


def test_contested_and_contradicted_items_appear_only_in_conflicts() -> None:
    for item in reference.ITEMS:
        out = channel_of(item.kind, item.lifecycle, item.verification, item.stale, item.open_contradiction)
        if item.lifecycle == "contested" or (item.lifecycle in channels.GROUNDED and item.open_contradiction):
            assert out == "conflicts"
        if out == "facts":
            assert item.lifecycle != "contested" and not item.open_contradiction


def test_assembling_every_legal_tuple_never_puts_an_inferred_guess_in_facts() -> None:
    views = []
    for i, item in enumerate(reference.ITEMS):
        views.append(
            view(
                item.kind,
                item.lifecycle,
                item.verification,
                artifact_id=f"a{i}",
                open_contradiction=item.open_contradiction,
                bound=BOUND if item.kind == "abstraction" else None,
                counterparts=(ConflictSide("other", "asserted", "the user prefers coffee"),),
            )
        )
    stale_ids = {f"a{i}" for i, item in enumerate(reference.ITEMS) if item.stale}
    result = run(views, staleness=lambda v: v.artifact_id in stale_ids)
    assert not result.refused
    by_id = {f"a{i}": item for i, item in enumerate(reference.ITEMS)}
    for fact in result.facts:
        item = by_id[fact.artifact_id]
        assert item.lifecycle in channels.GROUNDED or (item.kind, item.verification) == ("abstraction", "verified")
    for hypothesis in result.hypotheses:
        assert by_id[hypothesis.artifact_id].lifecycle == "inferred"
    assert result.excluded == sum(1 for item in reference.ITEMS if item.lifecycle == "retired")
    rendered = len(result.facts) + len(result.hypotheses) + len(result.procedures) + len(result.conflicts)
    assert rendered + result.excluded == len(reference.ITEMS)


def test_a_settled_item_with_an_open_contradiction_renders_as_a_conflict_showing_both_sides() -> None:
    result = run(
        [
            view(
                open_contradiction=True,
                counterparts=(ConflictSide("a2", "asserted", "the user prefers coffee"),),
            )
        ]
    )
    assert not result.facts
    (conflict,) = result.conflicts
    assert conflict.reason == "open_contradiction"
    assert [side.artifact_id for side in conflict.sides] == ["a1", "a2"]


def test_an_open_contradiction_without_a_counterpart_is_refused_not_shown_as_a_fact() -> None:
    result = run([view(open_contradiction=True)])
    assert not result.facts and not result.conflicts
    assert [(r.artifact_id, r.reason) for r in result.refused] == [("a1", "conflict_counterpart_missing")]


def test_a_conflict_item_cannot_be_built_with_one_side_when_a_contradiction_is_open() -> None:
    with pytest.raises(ChannelError, match="conflict_must_show_both_sides"):
        ConflictItem("a1", "open_contradiction", (ConflictSide("a1", "asserted", "x"),), ())


# --- the contradiction rule (V2-D48 C-P4) ----------------------------------------------------------


def test_the_contradiction_rule_equals_the_reference_model() -> None:
    for origin, a, b in product(("grounding_policy", "tier_c", "tier_b"), reference.LIFECYCLES, reference.LIFECYCLES):
        assert opens_contradiction(origin, (a, b)) == reference.opens_contradiction(origin, (a, b))  # type: ignore[arg-type]


def test_a_tier_b_edge_never_opens_a_contradiction() -> None:
    assert not any(opens_contradiction("tier_b", (a, b)) for a in reference.LIFECYCLES for b in reference.LIFECYCLES)


# --- bound rendering (requirement 5, I14) -------------------------------------------------------------


def test_a_verified_abstraction_renders_with_its_bound_in_the_text() -> None:
    result = run([view("abstraction", "inferred", "verified", bound=BOUND, text="tea is preferred in the afternoon")])
    (fact,) = result.facts
    assert isinstance(fact, BoundedFact)
    for fragment in ("34 of 40", "0.91", "0.72", "cooccurrence@3", "split-7", EXPIRES.isoformat()):
        assert fragment in fact.text, fragment
    assert fact.text == render_bounded_text("tea is preferred in the afternoon", BOUND)


def test_a_fresh_verified_abstraction_without_a_bound_is_refused_not_rendered_bare() -> None:
    result = run([view("abstraction", "inferred", "verified", bound=None)])
    assert not result.facts
    assert [(r.artifact_id, r.reason) for r in result.refused] == [("a1", "bound_missing")]


def test_no_code_path_can_construct_an_abstraction_fact_without_its_bound() -> None:
    provenance = ()
    with pytest.raises(TypeError):
        BoundedFact("a1", "statement", text="statement", provenance=provenance)  # type: ignore[call-arg]
    with pytest.raises(ChannelError, match="bounded_fact_requires_a_bound"):
        BoundedFact("a1", "statement", None, "statement", provenance)  # type: ignore[arg-type]
    with pytest.raises(ChannelError, match="bound_must_be_part_of_the_rendered_text"):
        BoundedFact("a1", "statement", BOUND, "statement", provenance)
    with pytest.raises(ChannelError, match="bound_must_be_part_of_the_rendered_text"):
        BoundedFact("a1", "statement", BOUND, "in 34 of 40 cases: statement", provenance)
    good = BoundedFact.create("a1", "statement", BOUND, provenance)
    assert good.bound is BOUND


def test_every_assembled_abstraction_fact_carries_a_bound() -> None:
    views = [view("abstraction", "inferred", "verified", artifact_id=f"x{n}", bound=BOUND) for n in range(5)]
    for fact in run(views).facts:
        assert isinstance(fact, BoundedFact) and isinstance(fact.bound, Bound)
        assert fact.bound.summary() in fact.text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"support_count": 41},
        {"support_count": -1},
        {"opportunity_count": 0, "support_count": 0},
        {"confidence_lower": 1.5},
        {"coverage": -0.1},
        {"method_id": " "},
        {"verified_on": ""},
        {"expires_at": datetime(2027, 6, 1)},
    ],
)
def test_a_malformed_bound_cannot_be_constructed(kwargs: dict[str, object]) -> None:
    with pytest.raises(ChannelError):
        replace(BOUND, **kwargs)


# --- staleness is computed and injected (requirement 6) ------------------------------------------------


def test_assembly_has_no_stored_stale_flag() -> None:
    assert "stale" not in {f for f in ArtifactView.__dataclass_fields__}


def test_the_injected_staleness_function_drives_the_channel() -> None:
    abstraction = view("abstraction", "inferred", "verified", bound=BOUND)
    fresh = run([abstraction], staleness=lambda _v: False)
    stale = run([abstraction], staleness=lambda _v: True)
    assert len(fresh.facts) == 1 and not fresh.hypotheses
    assert not stale.facts
    (hypothesis,) = stale.hypotheses
    assert hypothesis.label == "verified, needs re-verification"


def test_staleness_is_not_consulted_for_unverified_artifacts() -> None:
    calls = []
    result = run([view("abstraction", "inferred", "unverified")], staleness=lambda v: calls.append(v) or True)
    assert calls == []
    assert result.hypotheses[0].label == "unverified"


@pytest.mark.parametrize("cause", range(5))
def test_each_staleness_cause_alone_demotes_a_verified_abstraction(cause: int) -> None:
    flags = [False] * 5
    assert not stale_of(StalenessInputs(*flags))
    flags[cause] = True
    inputs = StalenessInputs(*flags)
    assert stale_of(inputs)
    abstraction = view("abstraction", "inferred", "verified", bound=BOUND)
    assert run([abstraction], staleness=lambda _v, i=inputs: stale_of(i)).hypotheses


def test_the_stale_of_function_equals_the_reference_model() -> None:
    for flags in product((False, True), repeat=5):
        assert stale_of(StalenessInputs(*flags)) == reference.stale_of(*flags)


# --- provenance opacity (requirement 7, I17) ---------------------------------------------------------


def test_provenance_into_an_unreadable_scope_is_opaque_and_leaks_no_foreign_identity() -> None:
    result = run([view(sources=(OWN_SOURCE, FOREIGN_SOURCE))])
    (fact,) = result.facts
    own, foreign = fact.provenance
    assert isinstance(own, VisibleReference) and own.source_id == "src-own"
    assert isinstance(foreign, OpaqueReference)
    rendered = repr(result)
    for secret in ("src-foreign", "other-tenant", "secret-project", "/u/other"):
        assert secret not in rendered, secret


def test_opaque_handles_differ_between_callers() -> None:
    other = assemble(
        [view(sources=(FOREIGN_SOURCE,))],
        caller_user_id="u9",
        read_set=resolve_read_set("u9", [Selector.of("/u/u9/")], []),
        key=KEY,
        staleness=never_stale,
    )
    mine = run([view(sources=(FOREIGN_SOURCE,))])
    assert mine.facts[0].provenance[0].opaque_ref != other.facts[0].provenance[0].opaque_ref  # type: ignore[union-attr]


def test_an_item_whose_provenance_cannot_be_materialised_is_refused() -> None:
    result = run([view(sources=())])
    assert not result.facts
    assert [(r.artifact_id, r.reason) for r in result.refused] == [("a1", "provenance_unmaterialisable")]


# --- other channels and fail-closed behaviour ---------------------------------------------------------


def test_procedures_hold_only_grounded_procedures() -> None:
    result = run(
        [
            view("procedure", "asserted", artifact_id="p1", text="run the tests first"),
            view("procedure", "inferred", "unverified", artifact_id="p2", text="a guessed recipe"),
        ]
    )
    assert [p.artifact_id for p in result.procedures] == ["p1"]
    assert [h.artifact_id for h in result.hypotheses] == ["p2"]


def test_a_verified_inferred_claim_is_still_a_hypothesis() -> None:
    result = run([view("claim", "inferred", "verified")])
    assert not result.facts
    assert result.hypotheses[0].label == "verified"


def test_retired_artifacts_are_counted_and_never_rendered() -> None:
    result = run([view(lifecycle="retired", text="old secret value")])
    assert result.excluded == 1
    assert "old secret value" not in repr(result)


def test_an_illegal_tuple_is_refused_and_does_not_abort_the_rest() -> None:
    result = run([view("abstraction", "asserted", artifact_id="bad"), view(artifact_id="good")])
    assert [r.reason for r in result.refused] == ["illegal_artifact_tuple"]
    assert [f.artifact_id for f in result.facts] == ["good"]
