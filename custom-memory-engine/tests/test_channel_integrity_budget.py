"""Part B of E-035: budgets may omit whole items, never reroute them or conceal the omission."""

import os
import random
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, cast

import pytest

import tests.test_channel_membership_model as spec
from custom_memory_engine.engine.channels import (
    ArtifactView,
    Assembly,
    BoundedFact,
    ChannelBudgets,
    ChannelCoverage,
    ConflictItem,
    ConflictSide,
    GroundedFact,
    Hypothesis,
    ProcedureItem,
    Refusal,
    assemble,
)
from tests.test_channel_integrity import (
    BOUND,
    CALLER,
    FIXED_SEEDS,
    KEY,
    READ_SET,
    check_invariants,
    make_view,
    replay_note,
    stale_for,
)

type AnswerChannel = Literal["facts", "hypotheses", "procedures", "conflicts"]
type AnswerItem = GroundedFact | BoundedFact | ConflictItem | Hypothesis | ProcedureItem

CHANNELS: tuple[AnswerChannel, ...] = ("facts", "hypotheses", "procedures", "conflicts")
ROOMY = ChannelBudgets(100_000, 100_000, 100_000, 100_000)
ZERO = ChannelBudgets(0, 0, 0, 0, total_tokens=0)
BOUNDED_PREFIX = (
    "in 34 of 40 observed cases (confidence >= 0.91, coverage 0.72; "
    "cooccurrence@3; verified on split-7; expires 2027-06-01T00:00:00+00:00): "
)


@dataclass(frozen=True, slots=True)
class Case:
    views: tuple[ArtifactView, ...]
    stale_ids: frozenset[str] = frozenset()

    def run(self, budgets: ChannelBudgets) -> Assembly:
        return assemble(
            self.views,
            caller_user_id=CALLER,
            read_set=READ_SET,
            key=KEY,
            staleness=stale_for(self.stale_ids),
            budgets=budgets,
        )


def _items(assembly: Assembly, channel: AnswerChannel) -> tuple[AnswerItem, ...]:
    return cast("tuple[AnswerItem, ...]", getattr(assembly, channel))


def _route(view: ArtifactView, stale_ids: frozenset[str]) -> spec.Channel | None:
    axes = spec.Item(
        view.kind,
        view.lifecycle,
        view.verification,
        view.verification == "verified" and view.artifact_id in stale_ids,
        view.open_contradiction,
    )
    if not spec.is_legal(axes):
        return None
    routes = [name for name, predicate in spec.PREDICATES.items() if predicate(axes)]
    assert len(routes) == 1, (view, routes)
    return cast("spec.Channel", routes[0])


def _refusal(view: ArtifactView, channel: spec.Channel | None) -> Refusal | None:
    if channel is None:
        return Refusal(view.artifact_id, "illegal_artifact_tuple")
    if channel == "none":
        return None
    if not view.sources:
        return Refusal(view.artifact_id, "provenance_unmaterialisable")
    if channel == "facts" and view.kind == "abstraction" and view.bound is None:
        return Refusal(view.artifact_id, "bound_missing")
    if channel == "conflicts" and view.open_contradiction and not view.counterparts:
        return Refusal(view.artifact_id, "conflict_counterpart_missing")
    return None


def _assert_original_content(item: AnswerItem, view: ArtifactView, stale_ids: frozenset[str]) -> None:
    assert item.provenance, f"{view.artifact_id}: rendered item lacks provenance"
    if isinstance(item, BoundedFact):
        assert item.statement == view.text and item.bound == view.bound, "bounded: wrong statement or bound"
        assert item.text == BOUNDED_PREFIX + view.text, "bounded: incomplete bound or text"
    elif isinstance(item, Hypothesis):
        label = "unverified"
        if view.verification == "verified":
            label = "verified, needs re-verification" if view.artifact_id in stale_ids else "verified"
        assert item.label == label, "hypotheses: wrong verification label"
        assert (item.text, item.support_summary, item.bound) == (view.text, view.support_summary, view.bound)
    elif isinstance(item, ConflictItem):
        assert item.sides == (ConflictSide(view.artifact_id, view.lifecycle, view.text), *view.counterparts)
    else:
        assert item.text == view.text


def _roomy_result(case: Case) -> Assembly:
    """Prove the oracle baseline is complete and correctly routed before trusting its item prefixes."""
    result = case.run(ROOMY)
    violations = check_invariants(case.views, result, stale_for(case.stale_ids))
    assert not violations, violations

    expected: dict[AnswerChannel, list[ArtifactView]] = {channel: [] for channel in CHANNELS}
    refusals: dict[AnswerChannel, list[Refusal]] = {channel: [] for channel in CHANNELS}
    all_refusals: list[Refusal] = []
    excluded = 0
    for view in case.views:
        route = _route(view, case.stale_ids)
        refusal = _refusal(view, route)
        if refusal is not None:
            all_refusals.append(refusal)
        if route == "none":
            excluded += 1
        elif route is not None:
            if refusal is None:
                expected[route].append(view)
            else:
                refusals[route].append(refusal)

    assert result.refused == tuple(all_refusals), "roomy: inaccurate global refusals"
    assert result.excluded == excluded, "roomy: only retired artifacts may be excluded"

    for channel in CHANNELS:
        items = _items(result, channel)
        assert tuple(item.artifact_id for item in items) == tuple(view.artifact_id for view in expected[channel]), (
            f"{channel}: roomy result does not match frozen routing"
        )
        assert result.coverage.channel(channel).refused == tuple(refusals[channel])
        for item, view in zip(items, expected[channel], strict=True):
            _assert_original_content(item, view, case.stale_ids)

    _assert_budgeted(result, result, ROOMY)
    return result


def _tokens(item: AnswerItem) -> int:
    """Count non-whitespace runs in the public answer fields, without the production counter."""
    if isinstance(item, Hypothesis):
        text = f"{item.label}: {item.text} {item.support_summary}"
    elif isinstance(item, ConflictItem):
        text = " ".join(f"{side.lifecycle}: {side.text}" for side in item.sides)
    else:
        text = item.text
    return len(re.findall(r"\S+", text))


def _assert_budgeted(result: Assembly, roomy: Assembly, budget: ChannelBudgets) -> None:
    """Check outcome, ceilings and missing-item accounting without running a second selector."""
    assert result.refused == roomy.refused, "global refusals changed under budget"
    assert result.excluded == roomy.excluded, "retired exclusions changed under budget"

    used_by_channel: dict[AnswerChannel, int] = {}
    next_cost: dict[AnswerChannel, int] = {}
    for channel in CHANNELS:
        full = _items(roomy, channel)
        returned = _items(result, channel)
        count = len(returned)
        cap = getattr(budget, channel)
        assert tuple(item.artifact_id for item in returned) == tuple(item.artifact_id for item in full[:count]), (
            f"{channel}: output is not a stable ID prefix"
        )
        assert returned == full[:count], f"{channel}: returned item changed under budget"
        used = sum(map(_tokens, returned))
        assert used <= cap, f"{channel}: token ceiling exceeded"
        used_by_channel[channel] = used
        omitted = full[count:]
        if omitted:
            next_cost[channel] = _tokens(omitted[0])

        refused = roomy.coverage.channel(channel).refused
        assert result.coverage.channel(channel) == ChannelCoverage(
            candidates=len(full) + len(refused),
            returned=count,
            refused=refused,
            truncated=len(omitted),
            token_budget=cap,
            tokens_used=used,
            tokens_truncated=sum(map(_tokens, omitted)),
        ), f"{channel}: coverage does not account for returned, refused and truncated items"

    total_used = sum(used_by_channel.values())
    assert budget.total_tokens is None or total_used <= budget.total_tokens, "total token ceiling exceeded"
    for channel, cost in next_cost.items():
        assert used_by_channel[channel] + cost > getattr(budget, channel) or (
            budget.total_tokens is not None and total_used + cost > budget.total_tokens
        ), f"{channel}: next complete item fits but was omitted"


def _view(seq: int, text: str, *, kind: str = "claim", lifecycle: str = "asserted") -> ArtifactView:
    return replace(make_view(seq=seq, kind=kind, lifecycle=lifecycle), text=text, support_summary="")


def _contending_channels() -> Case:
    # Explicit token costs: facts 2 + 2, hypothesis 2, procedure 1, conflict 4.
    return Case(
        (
            _view(1, "first fact"),
            _view(2, "second fact"),
            replace(_view(3, "guess", lifecycle="inferred"), verification="unverified"),
            _view(4, "step", kind="procedure"),
            replace(
                _view(5, "brief"),
                open_contradiction=True,
                counterparts=(ConflictSide("peer", "asserted", "reply"),),
            ),
        )
    )


def _must_refuse_or_omit() -> Case:
    return Case(
        (
            make_view(seq=6, kind="abstraction", lifecycle="asserted"),
            make_view(seq=7, sources=()),
            make_view(seq=8, kind="abstraction", lifecycle="inferred", verification="verified"),
            make_view(seq=9, open_contradiction=True),
            make_view(seq=10, lifecycle="retired"),
            make_view(seq=11, kind="abstraction", lifecycle="inferred", verification="verified", bound=BOUND),
            make_view(seq=12, kind="abstraction", lifecycle="inferred", verification="verified", bound=BOUND),
            make_view(seq=13, lifecycle="contested"),
        ),
        frozenset({"a12"}),
    )


@pytest.mark.parametrize(
    ("floor", "total", "expected_ids"),
    [
        (0, 7, ((), ("a3",), ("a4",), ("a5",))),
        (1, 7, (("a1", "a2"), ("a3",), ("a4",), ())),
        (2, 7, (("a1", "a2"), ("a3",), ("a4",), ())),
        (2, 9, (("a1",), ("a3",), ("a4",), ("a5",))),
        # Reserving both two-token facts before other channels leaves no room for the four-token conflict.
        (4, 9, (("a1", "a2"), ("a3",), ("a4",), ())),
        (2, 4, (("a1",), ("a3",), (), ())),
        (2, 5, (("a1",), ("a3",), ("a4",), ())),
    ],
)
def test_shared_budget_fills_the_facts_reserve_then_channels_in_contract_order(
    floor: int, total: int, expected_ids: tuple[tuple[str, ...], ...]
) -> None:
    case = _contending_channels()
    budget = ChannelBudgets(total_tokens=total, facts_floor_tokens=floor)
    result = case.run(budget)
    _assert_budgeted(result, _roomy_result(case), budget)
    _assert_expected_allocation(result, expected_ids)


def _assert_expected_allocation(result: Assembly, expected_ids: Sequence[tuple[str, ...]]) -> None:
    for channel, expected in zip(CHANNELS, expected_ids, strict=True):
        assert tuple(item.artifact_id for item in _items(result, channel)) == expected, (
            f"{channel}: shared budget did not honor the facts reserve and channel order"
        )


def test_a_large_next_item_cannot_be_skipped_for_a_smaller_one() -> None:
    case = Case((_view(1, "too many words"), _view(2, "short")))
    budget = ChannelBudgets(facts=2, total_tokens=2, facts_floor_tokens=1)
    result = case.run(budget)
    _assert_budgeted(result, _roomy_result(case), budget)
    assert result.facts == ()
    assert (result.coverage.facts.truncated, result.coverage.facts.tokens_truncated) == (2, 4)


@pytest.mark.parametrize("delta", [0, -1], ids=["exact-fit", "one-below"])
def test_an_abstraction_has_its_full_bound_or_is_omitted_whole(delta: int) -> None:
    view = make_view(seq=1, kind="abstraction", lifecycle="inferred", verification="verified", bound=BOUND)
    expected_text = BOUNDED_PREFIX + view.text
    case = Case((view,))
    tokens = len(expected_text.split())
    budget = ChannelBudgets(facts=tokens + delta)
    result = case.run(budget)
    _assert_budgeted(result, _roomy_result(case), budget)
    if delta == 0:
        (item,) = result.facts
        assert isinstance(item, BoundedFact)
        assert (item.bound, item.text) == (BOUND, expected_text)
    else:
        assert result.facts == ()
        assert (result.coverage.facts.truncated, result.coverage.facts.tokens_truncated) == (1, tokens)


def test_zero_budget_counts_omissions_in_the_original_channels_and_keeps_actual_refusals() -> None:
    negative = _must_refuse_or_omit()
    case = Case((*_contending_channels().views, *negative.views), negative.stale_ids)
    result = case.run(ZERO)
    _assert_budgeted(result, _roomy_result(case), ZERO)
    assert tuple(_items(result, channel) for channel in CHANNELS) == ((), (), (), ())
    assert tuple(result.coverage.channel(channel).truncated for channel in CHANNELS) == (3, 2, 1, 2)
    assert result.excluded == 1
    assert result.refused == (
        Refusal("a6", "illegal_artifact_tuple"),
        Refusal("a7", "provenance_unmaterialisable"),
        Refusal("a8", "bound_missing"),
        Refusal("a9", "conflict_counterpart_missing"),
    )


def _seeded_case(seed: int) -> tuple[Case, ChannelBudgets]:
    rng = random.Random(seed)
    baseline = _must_refuse_or_omit()
    views = [*_contending_channels().views, *baseline.views]
    stale_ids = set(baseline.stale_ids)
    for seq in range(100, 148):
        axes = rng.choice(spec.ITEMS)
        view = make_view(
            seq=seq,
            kind=axes.kind,
            lifecycle=axes.lifecycle,
            verification=axes.verification,
            open_contradiction=axes.open_contradiction,
            bound=BOUND if axes.kind == "abstraction" else None,
            counterparts=(ConflictSide(f"peer-{seq}", "asserted", "alternate reading"),)
            if axes.open_contradiction
            else (),
        )
        words = [f"term{seq}"] * rng.randint(1, 8)
        views.append(replace(view, text=rng.choice((" ", "\t", "\n", "\u00a0")).join(words)))
        if axes.stale:
            stale_ids.add(view.artifact_id)
    rng.shuffle(views)
    facts, hypotheses, procedures, conflicts = (rng.randrange(50) for _ in CHANNELS)
    total = rng.choice((None, rng.randrange(65)))
    floor = rng.randint(0, min(facts, total if total is not None else facts))
    return Case(tuple(views), frozenset(stale_ids)), ChannelBudgets(
        facts, hypotheses, procedures, conflicts, total, floor
    )


def _sweep(seed: int) -> None:
    case, budget = _seeded_case(seed)
    try:
        _assert_budgeted(case.run(budget), _roomy_result(case), budget)
    except Exception as exc:
        exc.add_note(f"{budget=}\n" + replay_note(seed, case.views, case.stale_ids))
        raise


@pytest.mark.parametrize("seed", FIXED_SEEDS)
def test_budget_and_coverage_hold_on_fixed_seeds(seed: int) -> None:
    _sweep(seed)


def test_budget_and_coverage_hold_on_a_replayable_random_seed() -> None:
    override = os.environ.get("CME_FUZZ_SEED")
    seed = int(override) if override else int.from_bytes(os.urandom(4), "big")
    print(f"channel budget fuzz seed: {seed} (re-run with CME_FUZZ_SEED={seed})")
    _sweep(seed)


@pytest.mark.parametrize("error_type", [AssertionError, RuntimeError])
def test_budget_fuzz_replay_includes_inputs_and_caps(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    def reject_replay(*_args: object, **_kwargs: object) -> None:
        raise error_type("synthetic replay violation")

    monkeypatch.setattr(sys.modules[__name__], "assemble", reject_replay)
    with pytest.raises(error_type, match="synthetic replay violation") as first:
        _sweep(42)
    with pytest.raises(error_type, match="synthetic replay violation") as repeat:
        _sweep(42)

    assert first.value.__notes__ == repeat.value.__notes__
    (note,) = first.value.__notes__
    for marker in ("CME_FUZZ_SEED=42", "ChannelBudgets(", "ArtifactView(", "stale_ids="):
        assert marker in note


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("wrong_fact", "facts: output is not a stable ID prefix"),
        ("invented", "facts: output is not a stable ID prefix"),
        ("duplicate", "facts: output is not a stable ID prefix"),
        ("stale_as_fact", "facts: output is not a stable ID prefix"),
        ("conflict_as_hypothesis", "hypotheses: output is not a stable ID prefix"),
        ("stale_label", "hypotheses: returned item changed under budget"),
        ("missing_bound", "facts: returned item changed under budget"),
        ("truncation_as_refusal", "global refusals changed under budget"),
        ("changed_bound", "facts: returned item changed under budget"),
        ("clipped_to_zero", "facts: token ceiling exceeded"),
        ("shared_cap", "total token ceiling exceeded"),
        ("coverage", "facts: coverage does not account"),
        ("missing_fitting_item", "facts: next complete item fits but was omitted"),
    ],
)
def test_budget_controls_detect_deliberate_violations(fault: str, message: str) -> None:
    fixture = _must_refuse_or_omit()
    case = Case((*_contending_channels().views, *fixture.views), fixture.stale_ids)
    roomy = _roomy_result(case)
    budget = ROOMY
    result = roomy
    if fault in ("wrong_fact", "stale_as_fact"):
        inference = roomy.hypotheses[0 if fault == "wrong_fact" else 1]
        false_fact = GroundedFact(inference.artifact_id, inference.kind, inference.text, inference.provenance)
        result = replace(roomy, facts=(false_fact, *roomy.facts[1:]))
    elif fault == "invented":
        result = replace(roomy, facts=(replace(roomy.facts[0], artifact_id="invented"), *roomy.facts[1:]))
    elif fault == "duplicate":
        result = replace(roomy, facts=(roomy.facts[0], *roomy.facts))
    elif fault == "conflict_as_hypothesis":
        conflict = roomy.conflicts[0]
        misplaced = Hypothesis(conflict.artifact_id, "claim", "unverified", "brief", "", conflict.provenance)
        result = replace(roomy, hypotheses=(misplaced, *roomy.hypotheses[1:]))
    elif fault == "stale_label":
        guess, stale = roomy.hypotheses
        result = replace(roomy, hypotheses=(guess, replace(stale, label="verified")))
    elif fault in ("changed_bound", "missing_bound"):
        bounded = roomy.facts[-1]
        assert isinstance(bounded, BoundedFact)
        changed: GroundedFact | BoundedFact
        if fault == "changed_bound":
            false_bound = replace(bounded.bound, support_count=33)
            changed = BoundedFact.create(bounded.artifact_id, bounded.statement, false_bound, bounded.provenance)
        else:
            changed = GroundedFact(bounded.artifact_id, "abstraction", bounded.statement, bounded.provenance)
        result = replace(roomy, facts=(*roomy.facts[:-1], changed))
    elif fault == "truncation_as_refusal":
        budget = ZERO
        clipped = case.run(ZERO)
        result = replace(clipped, refused=(*clipped.refused, Refusal("a1", "provenance_unmaterialisable")))
    elif fault == "clipped_to_zero":
        budget = replace(ROOMY, facts=0)
    elif fault == "shared_cap":
        budget = replace(ROOMY, total_tokens=1)
    elif fault == "coverage":
        # Preserve aggregate truncation, deliberately charge omitted facts to the wrong channel.
        result = case.run(ZERO)
        budget = ZERO
        coverage = result.coverage
        result = replace(
            result,
            coverage=replace(
                coverage,
                facts=replace(coverage.facts, truncated=coverage.facts.truncated - 1),
                hypotheses=replace(coverage.hypotheses, truncated=coverage.hypotheses.truncated + 1),
            ),
        )
    else:
        assert fault == "missing_fitting_item"
        facts_cap = 10
        result = case.run(replace(ROOMY, facts=0))
        budget = replace(ROOMY, facts=facts_cap)
        result = replace(
            result,
            coverage=replace(result.coverage, facts=replace(result.coverage.facts, token_budget=facts_cap)),
        )

    with pytest.raises(AssertionError, match=message) as failure:
        _assert_budgeted(result, roomy, budget)
    print(f"{fault}: {str(failure.value).splitlines()[0]}")


def test_budget_control_detects_an_ignored_facts_reserve() -> None:
    case = _contending_channels()
    result = case.run(ChannelBudgets(total_tokens=7))
    budget = ChannelBudgets(total_tokens=7, facts_floor_tokens=2)
    _assert_budgeted(result, _roomy_result(case), budget)
    with pytest.raises(AssertionError, match="did not honor the facts reserve") as failure:
        _assert_expected_allocation(result, (("a1", "a2"), ("a3",), ("a4",), ()))
    print(f"ignored_facts_reserve: {str(failure.value).splitlines()[0]}")


def test_budget_control_detects_a_reserve_that_stops_after_one_fact() -> None:
    """R7: honoring only the first fact can still produce valid accounting and fit all ceilings."""
    case = _contending_channels()
    budget = ChannelBudgets(total_tokens=9, facts_floor_tokens=4)
    stopped_early = case.run(replace(budget, facts_floor_tokens=2))
    _assert_budgeted(stopped_early, _roomy_result(case), budget)
    with pytest.raises(AssertionError, match="facts: shared budget did not honor the facts reserve") as failure:
        _assert_expected_allocation(stopped_early, (("a1", "a2"), ("a3",), ("a4",), ()))
    print(f"stopped_after_one_reserved_fact: {str(failure.value).splitlines()[0]}")
