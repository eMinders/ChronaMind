"""Executable reference model of the four-channel contract (MA-013, ``PLAN/V2/CHANNELS.md``).

Channel membership is a pure function of ``(kind, lifecycle, verification, stale, open_contradiction)``.
This model enumerates every *legal* tuple and checks the contract exhaustively: each tuple belongs to
exactly one of the four answer channels or to the audit-only set, and the forbidden memberships of
``ARCHITECTURE_V2.md`` sections 2.2 and 9.1 never occur. The fifth output, ``none``, means "rendered in no
answer channel" (``retired``; still visible to ``audit_as_of``). ``E-017`` must implement the same function
against the database; ``E-035`` fuzzes the assembled output.

The five channel predicates below are written independently from the statements in section 9.1 so that
"exactly one" is a real property of the specification and not a tautology of a single if-chain.
"""

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import product
from typing import Literal

import pytest

from custom_memory_engine.model import axes

Kind = Literal["episode", "claim", "procedure", "abstraction"]
Lifecycle = Literal["observed", "asserted", "inferred", "contested", "retired"]
Verification = Literal["none", "unverified", "verified", "refuted"]
Channel = Literal["facts", "hypotheses", "procedures", "conflicts", "none"]

KINDS: tuple[Kind, ...] = ("episode", "claim", "procedure", "abstraction")
LIFECYCLES: tuple[Lifecycle, ...] = ("observed", "asserted", "inferred", "contested", "retired")
VERIFICATIONS: tuple[Verification, ...] = ("none", "unverified", "verified", "refuted")
GROUNDED: tuple[Lifecycle, ...] = ("observed", "asserted")

# Which lifecycle values each kind may hold (ARCHITECTURE_V2.md 2.1-2.3). An abstraction is never
# observed or asserted: `inferred -> asserted` does not exist.
LEGAL_LIFECYCLE: dict[Kind, tuple[Lifecycle, ...]] = {
    "episode": ("observed", "inferred", "contested", "retired"),
    "claim": ("observed", "asserted", "inferred", "contested", "retired"),
    "procedure": ("asserted", "inferred", "contested", "retired"),
    "abstraction": ("inferred", "contested", "retired"),
}


@dataclass(frozen=True)
class Item:
    """The five inputs to channel membership."""

    kind: Kind
    lifecycle: Lifecycle
    verification: Verification
    stale: bool
    open_contradiction: bool


def is_legal(item: Item) -> bool:
    """A tuple the engine can actually hold."""
    if item.lifecycle not in LEGAL_LIFECYCLE[item.kind]:
        return False
    if item.lifecycle in GROUNDED:
        # Grounded artifacts carry no verification; only they can have an open contradiction.
        return item.verification == "none" and not item.stale
    if item.open_contradiction:
        return False
    if item.lifecycle == "inferred":
        # `refuted` always retires the artifact (inferred -> retired), so it is never inferred.
        return item.verification in ("unverified", "verified") and (not item.stale or item.verification == "verified")
    # contested / retired may carry the history of their verification; staleness needs `verified`.
    return not item.stale or item.verification == "verified"


def legal_items() -> Iterator[Item]:
    for kind, lifecycle, verification, stale, contradiction in product(
        KINDS, LIFECYCLES, VERIFICATIONS, (False, True), (False, True)
    ):
        item = Item(kind, lifecycle, verification, stale, contradiction)
        if is_legal(item):
            yield item


def fresh_verified_abstraction(i: Item) -> bool:
    return i.kind == "abstraction" and i.lifecycle == "inferred" and i.verification == "verified" and not i.stale


# Independent channel predicates, one per statement of ARCHITECTURE_V2.md 9.1.
def in_facts(i: Item) -> bool:
    grounded_settled = i.lifecycle in GROUNDED and i.kind != "procedure" and not i.open_contradiction
    return grounded_settled or fresh_verified_abstraction(i)


def in_hypotheses(i: Item) -> bool:
    return i.lifecycle == "inferred" and not fresh_verified_abstraction(i)


def in_procedures(i: Item) -> bool:
    return i.kind == "procedure" and i.lifecycle in GROUNDED and not i.open_contradiction


def in_conflicts(i: Item) -> bool:
    return i.lifecycle == "contested" or (i.lifecycle in GROUNDED and i.open_contradiction)


def in_none(i: Item) -> bool:
    return i.lifecycle == "retired"


PREDICATES = {
    "facts": in_facts,
    "hypotheses": in_hypotheses,
    "procedures": in_procedures,
    "conflicts": in_conflicts,
    "none": in_none,
}


def channel_of(item: Item) -> Channel:
    """The membership function; the contract `E-017` must reproduce."""
    if not is_legal(item):
        raise ValueError("illegal_artifact_tuple")
    if item.lifecycle == "retired":
        return "none"
    if item.lifecycle == "contested" or item.open_contradiction:
        return "conflicts"
    if item.lifecycle == "inferred":
        return "facts" if fresh_verified_abstraction(item) else "hypotheses"
    return "procedures" if item.kind == "procedure" else "facts"


ITEMS = list(legal_items())


def label(i: Item) -> str:
    return f"{i.kind}/{i.lifecycle}/{i.verification}/stale={i.stale}/contradicted={i.open_contradiction}"


def test_the_legal_tuple_space_is_non_trivial() -> None:
    assert len(ITEMS) > 30
    assert {i.kind for i in ITEMS} == set(KINDS)
    assert {i.lifecycle for i in ITEMS} == set(LIFECYCLES)


@pytest.mark.parametrize("item", ITEMS, ids=label)
def test_every_legal_tuple_belongs_to_exactly_one_channel(item: Item) -> None:
    holding = [name for name, predicate in PREDICATES.items() if predicate(item)]
    assert len(holding) == 1, holding
    assert channel_of(item) == holding[0]


def test_contested_content_appears_only_in_conflicts() -> None:
    for item in ITEMS:
        if item.lifecycle == "contested":
            assert channel_of(item) == "conflicts"
        if channel_of(item) == "facts":
            assert item.lifecycle != "contested"


def test_a_settled_item_with_an_open_contradiction_is_not_a_fact() -> None:
    contradicted = [i for i in ITEMS if i.lifecycle in GROUNDED and i.open_contradiction]
    assert contradicted
    assert all(channel_of(i) == "conflicts" for i in contradicted)


def test_nothing_grounded_is_ever_a_hypothesis_and_nothing_inferred_is_a_procedure() -> None:
    for item in ITEMS:
        channel = channel_of(item)
        if channel == "hypotheses":
            assert item.lifecycle == "inferred"
        if channel == "procedures":
            assert item.lifecycle in GROUNDED and item.kind == "procedure"


def test_only_fresh_verified_abstractions_present_inferred_content_as_facts() -> None:
    for item in ITEMS:
        if item.lifecycle == "inferred" and channel_of(item) == "facts":
            assert (item.kind, item.verification, item.stale) == ("abstraction", "verified", False)


def test_a_stale_verified_abstraction_is_demoted_to_hypotheses() -> None:
    stale = Item("abstraction", "inferred", "verified", True, False)
    fresh = Item("abstraction", "inferred", "verified", False, False)
    assert channel_of(stale) == "hypotheses"
    assert channel_of(fresh) == "facts"


def test_retired_content_is_in_no_answer_channel() -> None:
    retired = [i for i in ITEMS if i.lifecycle == "retired"]
    assert retired
    assert all(channel_of(i) == "none" for i in retired)


def test_a_verified_inferred_claim_or_procedure_is_still_a_hypothesis() -> None:
    """Only abstractions may present a verified result as a bounded fact (section 9.1)."""
    assert channel_of(Item("claim", "inferred", "verified", False, False)) == "hypotheses"
    assert channel_of(Item("procedure", "inferred", "verified", False, False)) == "hypotheses"


@pytest.mark.parametrize(
    "item",
    [
        Item("abstraction", "asserted", "none", False, False),
        Item("abstraction", "observed", "none", False, False),
        Item("episode", "asserted", "none", False, False),
        Item("procedure", "observed", "none", False, False),
        Item("claim", "inferred", "refuted", False, False),
        Item("claim", "inferred", "none", False, False),
        Item("claim", "asserted", "verified", False, False),
        Item("claim", "retired", "none", False, True),
    ],
    ids=label,
)
def test_illegal_tuples_are_rejected_rather_than_routed(item: Item) -> None:
    with pytest.raises(ValueError, match="illegal_artifact_tuple"):
        channel_of(item)


def test_every_channel_is_reachable() -> None:
    assert set(Counter(channel_of(i) for i in ITEMS)) == {
        "facts",
        "hypotheses",
        "procedures",
        "conflicts",
        "none",
    }


# --- Consistency with the lifecycle table on main (V2-D38, V2-D48, ``model.axes``) ------------------------


def _reachable_through_axes() -> set[tuple[str, str]]:
    """(kind, lifecycle) pairs reachable from creation under ``axes.require_transition(..., kind)``."""
    pairs: set[tuple[str, str]] = set()
    for kind in axes.KINDS:

        def ok(source: str | None, target: str, kind: str = kind) -> bool:
            try:
                axes.require_transition(source, target, kind)  # type: ignore[arg-type]
            except axes.AxisError:
                return False
            return True

        seen = {t for t in axes.LIFECYCLES if ok(None, t)}
        frontier = set(seen)
        while frontier:
            frontier = {t for s in frontier for t in axes.LIFECYCLES if ok(s, t)} - seen
            seen |= frontier
        pairs |= {(kind, lifecycle) for lifecycle in seen}
    return pairs


def test_the_model_uses_exactly_the_axes_vocabulary_and_legality() -> None:
    assert set(KINDS) == set(axes.KINDS)
    assert set(LIFECYCLES) == set(axes.LIFECYCLES)
    assert {k: frozenset(v) for k, v in LEGAL_LIFECYCLE.items()} == axes.LEGAL_LIFECYCLE


def test_every_v1_backfill_output_is_a_legal_tuple() -> None:
    legal = {(k, lc) for k in KINDS for lc in LEGAL_LIFECYCLE[k]}
    for status, layer, derived in product(axes.V1_STATUSES, axes.V1_LAYERS, (False, True)):
        mapped = axes.map_v1_version(status, layer, derived)
        assert (mapped.kind, mapped.lifecycle) in legal, (status, layer, derived)


def test_the_lifecycle_table_reaches_exactly_the_legal_tuple_space() -> None:
    """C-P1 (V2-D48): no illegal (kind, lifecycle) pair is reachable, and every legal pair is."""
    legal = {(k, lc) for k in KINDS for lc in LEGAL_LIFECYCLE[k]}
    assert _reachable_through_axes() == legal


# --- C-P4 (V2-D48): who may open a contradiction --------------------------------------------------------

Origin = Literal["grounding_policy", "tier_c", "tier_b"]


def opens_contradiction(origin: Origin, endpoint_lifecycles: tuple[Lifecycle, Lifecycle]) -> bool:
    """A ``contradicts`` edge sets ``open_contradiction`` only if both endpoints are grounded and it was
    created by the grounding policy or by a Tier C caller. A Tier B edge is stored as an association and
    has **no** channel effect: Tier B may not adjudicate (TB2), and demoting a fact is adjudication.
    """
    return origin in ("grounding_policy", "tier_c") and all(lc in GROUNDED for lc in endpoint_lifecycles)


def test_a_tier_b_contradicts_edge_never_demotes_a_grounded_fact() -> None:
    fact = Item("claim", "asserted", "none", False, False)
    assert channel_of(fact) == "facts"
    for other in LIFECYCLES:
        demoted = Item("claim", "asserted", "none", False, opens_contradiction("tier_b", ("asserted", other)))
        assert channel_of(demoted) == "facts"


def test_grounding_policy_or_user_contradiction_between_grounded_items_moves_both_to_conflicts() -> None:
    for origin in ("grounding_policy", "tier_c"):
        flag = opens_contradiction(origin, ("asserted", "observed"))  # type: ignore[arg-type]
        assert channel_of(Item("claim", "asserted", "none", False, flag)) == "conflicts"


def test_a_contradiction_against_an_inference_is_not_an_open_contradiction() -> None:
    """Counter-evidence against an inference goes through ``refute`` (inferred -> contested), not an edge."""
    assert not opens_contradiction("tier_c", ("asserted", "inferred"))


# --- C-P3 (V2-D48): staleness is the OR of five causes ---------------------------------------------------


def stale_of(
    closure_changed: bool, support_below_threshold: bool, source_retired: bool, method_changed: bool, expired: bool
) -> bool:
    """``CHANNELS.md`` 2.3. ``E-037``/``E-038`` compute the inputs; any one of them makes the item stale."""
    return closure_changed or support_below_threshold or source_retired or method_changed or expired


@pytest.mark.parametrize("cause", range(5))
def test_each_staleness_cause_alone_demotes_a_verified_abstraction(cause: int) -> None:
    flags = [False] * 5
    assert channel_of(Item("abstraction", "inferred", "verified", stale_of(*flags), False)) == "facts"
    flags[cause] = True
    assert channel_of(Item("abstraction", "inferred", "verified", stale_of(*flags), False)) == "hypotheses"
