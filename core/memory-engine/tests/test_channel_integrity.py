"""Channel-integrity fuzz and red-team for the four-channel contract (``E-035`` / 205-A).

``CHANNELS.md`` froze a contract (``V2-D48``): membership is a pure function of ``(kind, lifecycle,
verification, stale, open_contradiction)``; an inferred artifact never renders as fact; a verified abstraction
renders **with its bound**; contested content appears only in ``conflicts``; a stale verified abstraction lands
in ``hypotheses``. ``E-017``'s unit tests cover the combinations someone thought of. This module is the
adversary: it enumerates the whole space, fuzzes it from fixed seeds, and asserts five named invariants on every
result — so the failure mode "a combination nobody enumerated" has somewhere to show up.

A green fuzz proves nothing unless it can fail. Every invariant therefore has a **positive control** that
injects a violation and asserts the check trips, and the enumerated space is re-derived from ``is_legal_tuple``
(the code's stated contract) rather than from what ``assemble`` happens to do.

Fixes are out of scope (``E-035`` W6/W8): a defect found here becomes an ``E-035-F<n>`` finding on #73, and the
assertion stays. Nothing in this module imports the database or a model, so it is pure, fast and reproducible.
"""

from __future__ import annotations

import os
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import product

import pytest

from custom_memory_engine.engine.channels import (
    GROUNDED,
    VERIFICATIONS,
    ArtifactView,
    Assembly,
    Bound,
    BoundedFact,
    ChannelError,
    ConflictSide,
    GroundedFact,
    Staleness,
    assemble,
    channel_of,
    hypothesis_label,
    is_legal_tuple,
    never_stale,
    render_bounded_text,
)
from custom_memory_engine.model.axes import KINDS, LIFECYCLES
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.scope.provenance import ProvenanceKey, ProvenanceSource

KEY = ProvenanceKey(b"k" * 32)
CALLER = "u1"
READ_SET = resolve_read_set(CALLER, [Selector.of("/u/u1/")], [])
EXPIRES = datetime(2027, 6, 1, tzinfo=UTC)
BOUND = Bound(34, 40, 0.91, 0.72, "cooccurrence", "3", "split-7", EXPIRES)
OWN_SOURCE = ProvenanceSource("src-own", "/u/u1/", "private", "episode", "consolidation")
FOREIGN_SOURCE = ProvenanceSource("src-foreign", "/u/other-tenant/p/secret/", "private", "episode", "summary")

REFUSAL_REASONS = frozenset(
    {"illegal_artifact_tuple", "provenance_unmaterialisable", "bound_missing", "conflict_counterpart_missing"}
)

# Fixed seeds: a seed that fails today fails tomorrow (``E-035`` requirement 2 / §9 determinism).
FIXED_SEEDS: tuple[int, ...] = (0, 1, 7, 42, 2718)

# The whole product space, before per-kind and per-lifecycle filters: 4 x 5 x 4 x 2 x 2.
SPACE_SIZE = len(KINDS) * len(LIFECYCLES) * len(VERIFICATIONS) * 2 * 2


@dataclass(frozen=True, slots=True)
class Violation:
    """One broken invariant. ``invariant`` is the stable name a failure should quote."""

    invariant: str
    detail: str


# --- fixtures shared with the invariants ---------------------------------------------------------------


def is_stale(view: ArtifactView, staleness: Staleness) -> bool:
    """Mirror ``assemble``'s reading: staleness is only consulted for verified artifacts (``V2-D24``)."""
    return staleness(view) if view.verification == "verified" else False


def is_fresh_verified_abstraction(view: ArtifactView, staleness: Staleness) -> bool:
    """The single bounded exception to ``I1'`` (``CHANNELS.md`` C-P2)."""
    return (
        view.kind == "abstraction"
        and view.lifecycle == "inferred"
        and view.verification == "verified"
        and not is_stale(view, staleness)
    )


def make_view(  # noqa: PLR0913
    *,
    seq: int,
    kind: str = "claim",
    lifecycle: str = "asserted",
    verification: str = "none",
    open_contradiction: bool = False,
    sources: tuple[ProvenanceSource, ...] = (OWN_SOURCE,),
    bound: Bound | None = None,
    counterparts: tuple[ConflictSide, ...] = (),
) -> ArtifactView:
    """One view per tuple, with a unique id so "rendered exactly once" is a real property."""
    return ArtifactView(
        artifact_id=f"a{seq}",
        kind=kind,  # type: ignore[arg-type]
        lifecycle=lifecycle,  # type: ignore[arg-type]
        verification=verification,  # type: ignore[arg-type]
        text=f"statement about item {seq}",
        sources=sources,
        open_contradiction=open_contradiction,
        bound=bound,
        support_summary=f"{seq} observations",
        counterparts=counterparts,
    )


def stale_for(stale_ids: frozenset[str]) -> Staleness:
    """A staleness function the fuzz controls directly, so staleness needs no deletion history."""

    def staleness(view: ArtifactView) -> bool:
        return view.artifact_id in stale_ids

    return staleness


def run(views: Sequence[ArtifactView], staleness: Staleness = never_stale) -> Assembly:
    return assemble(views, caller_user_id=CALLER, read_set=READ_SET, key=KEY, staleness=staleness)


# --- the shared invariant checker (enumeration and fuzz both call this; §11.2) -------------------------


def check_invariants(
    views: Sequence[ArtifactView], assembly: Assembly, staleness: Staleness = never_stale
) -> tuple[Violation, ...]:
    """Assert the five named invariants on one assembly. Returns every violation, never raises."""
    violations: list[Violation] = []
    by_id = {view.artifact_id: view for view in views}

    facts_ids = {item.artifact_id for item in assembly.facts}
    hypothesis_ids = {item.artifact_id for item in assembly.hypotheses}
    procedure_ids = {item.artifact_id for item in assembly.procedures}
    conflict_ids = {item.artifact_id for item in assembly.conflicts}
    refused_ids = {item.artifact_id for item in assembly.refused}
    rendered_ids = facts_ids | hypothesis_ids | procedure_ids | conflict_ids

    # --- I1p: an inferred artifact is never rendered as a fact -----------------------------------------
    for artifact_id in facts_ids:
        view = by_id.get(artifact_id)
        if view is None:
            violations.append(Violation("I1p", f"{artifact_id} rendered but not an input"))
            continue
        if view.lifecycle in GROUNDED:
            continue
        if not is_fresh_verified_abstraction(view, staleness):
            violations.append(
                Violation(
                    "I1p",
                    f"{artifact_id} ({view.kind}/{view.lifecycle}/{view.verification}) "
                    f"is not a fresh verified abstraction but renders in facts",
                )
            )
    for view in views:
        artifact_id = view.artifact_id
        # Existence is subject to refusal/exclusion; only *membership* is unconditional.
        if artifact_id in refused_ids or artifact_id not in rendered_ids:
            continue
        if is_fresh_verified_abstraction(view, staleness) and artifact_id not in facts_ids:
            violations.append(Violation("I1p", f"{artifact_id} is a fresh verified abstraction but is not in facts"))

    # --- bounded: a verified abstraction always carries its bound (I14) --------------------------------
    for item in assembly.facts:
        if not isinstance(item, BoundedFact):
            continue
        view = by_id.get(item.artifact_id)
        if view is None:
            continue
        if view.bound is None:
            violations.append(Violation("bounded", f"{item.artifact_id} rendered bounded from a boundless view"))
            continue
        if item.bound != view.bound:
            violations.append(Violation("bounded", f"{item.artifact_id} rendered with a substituted bound"))
        expected = render_bounded_text(item.statement, item.bound)
        if item.text != expected:
            violations.append(Violation("bounded", f"{item.artifact_id} text is not the rendered bounded form"))
        if item.bound.summary() not in item.text:
            violations.append(Violation("bounded", f"{item.artifact_id} text omits its bound summary"))
    for artifact_id in facts_ids & {v.artifact_id for v in views if v.kind == "abstraction"}:
        if not any(isinstance(item, BoundedFact) and item.artifact_id == artifact_id for item in assembly.facts):
            violations.append(Violation("bounded", f"{artifact_id} is an abstraction in facts without a Bound"))

    # --- conflicts_only: contested and contradicted artifacts appear only in conflicts ----------------
    for view in views:
        if view.lifecycle != "contested" and not view.open_contradiction:
            continue
        artifact_id = view.artifact_id
        # A contested artifact that was refused is never rendered at all; that is correct, not a leak.
        if artifact_id in refused_ids or artifact_id not in rendered_ids:
            continue
        if artifact_id not in conflict_ids:
            violations.append(Violation("conflicts_only", f"{artifact_id} is contested but not in conflicts"))
        for name, ids in (
            ("facts", facts_ids),
            ("hypotheses", hypothesis_ids),
            ("procedures", procedure_ids),
        ):
            if artifact_id in ids:
                violations.append(Violation("conflicts_only", f"{artifact_id} is contested but renders in {name}"))

    # --- stale_to_hypotheses: a stale verified abstraction is a hypothesis, never a fact --------------
    for view in views:
        if view.lifecycle != "inferred" or not is_stale(view, staleness):
            continue
        artifact_id = view.artifact_id
        if artifact_id in facts_ids:
            violations.append(Violation("stale_to_hypotheses", f"{artifact_id} is stale but renders in facts"))
        if artifact_id not in hypothesis_ids and artifact_id not in refused_ids:
            violations.append(Violation("stale_to_hypotheses", f"{artifact_id} is stale but vanished"))
    for item in assembly.hypotheses:
        view = by_id.get(item.artifact_id)
        if view is None or not is_stale(view, staleness):
            continue
        if item.label != hypothesis_label(view.verification, True):
            violations.append(
                Violation("stale_to_hypotheses", f"{item.artifact_id} label {item.label!r} hides staleness")
            )

    # --- no_loss: rendered exactly once, or refused, or excluded --------------------------------------
    accounted = (
        len(assembly.facts)
        + len(assembly.hypotheses)
        + len(assembly.procedures)
        + len(assembly.conflicts)
        + len(assembly.refused)
        + assembly.excluded
    )
    if accounted != len(views):
        violations.append(Violation("no_loss", f"{accounted} accounted for {len(views)} inputs"))
    total_ids = len(assembly.facts) + len(assembly.hypotheses) + len(assembly.procedures) + len(assembly.conflicts)
    if len(rendered_ids) != total_ids:
        violations.append(
            Violation("no_loss", f"an artifact is rendered more than once ({total_ids} vs {len(rendered_ids)})")
        )
    if rendered_ids & refused_ids:
        violations.append(Violation("no_loss", f"{sorted(rendered_ids & refused_ids)} both rendered and refused"))
    unknown = rendered_ids - set(by_id)
    if unknown:
        violations.append(Violation("no_loss", f"invented ids {sorted(unknown)}"))
    # `excluded` is a count, never content, so the only honest check is that the unaccounted ids number it.
    unaccounted = set(by_id) - rendered_ids - refused_ids
    if len(unaccounted) != assembly.excluded:
        violations.append(Violation("no_loss", f"{len(unaccounted)} unaccounted ids but excluded={assembly.excluded}"))
    # Only `retired` may be in no channel (`channel_of` returns "none" for it and nothing else), so an excluded
    # non-retired artifact is a silent loss that would otherwise balance the count.
    for artifact_id in unaccounted:
        view = by_id[artifact_id]
        if view.lifecycle != "retired":
            violations.append(
                Violation("no_loss", f"{artifact_id} ({view.kind}/{view.lifecycle}) vanished without a retirement")
            )

    # --- refusals carry only the four documented reasons ----------------------------------------------
    for item in assembly.refused:
        if item.reason not in REFUSAL_REASONS:
            violations.append(Violation("refusal_reason", f"{item.artifact_id} refused as {item.reason!r}"))

    return tuple(violations)


def assert_clean(views: Sequence[ArtifactView], staleness: Staleness = never_stale) -> Assembly:
    assembly = run(views, staleness)
    violations = check_invariants(views, assembly, staleness)
    assert not violations, "\n".join(f"{v.invariant}: {v.detail}" for v in violations)
    return assembly


# --- the tuple space, classified (criterion 1) ---------------------------------------------------------


def legal_tuples() -> list[tuple[str, str, str, bool, bool]]:
    return [
        (kind, lifecycle, verification, stale, contradicted)
        for kind, lifecycle, verification, stale, contradicted in product(
            KINDS, LIFECYCLES, VERIFICATIONS, (False, True), (False, True)
        )
        if is_legal_tuple(kind, lifecycle, verification, stale, contradicted)  # type: ignore[arg-type]
    ]


def test_the_tuple_space_is_enumerated_with_legal_and_illegal_classified() -> None:
    """320 combinations; the legal region is a strict minority and every legal one has one channel."""
    legal = legal_tuples()
    illegal = SPACE_SIZE - len(legal)
    assert len(legal) + illegal == SPACE_SIZE == 320
    assert len(legal) == 60, "the legal region changed shape; CHANNELS.md is frozen (V2-D48)"
    assert illegal == 260

    channels = {
        channel_of(kind, lifecycle, verification, stale, contradicted)  # type: ignore[arg-type]
        for kind, lifecycle, verification, stale, contradicted in legal
    }
    assert channels == {"facts", "hypotheses", "procedures", "conflicts", "none"}


def illegal_tuples() -> list[tuple[str, str, str, bool, bool]]:
    return [
        (kind, lifecycle, verification, stale, contradicted)
        for kind, lifecycle, verification, stale, contradicted in product(
            KINDS, LIFECYCLES, VERIFICATIONS, (False, True), (False, True)
        )
        if not is_legal_tuple(kind, lifecycle, verification, stale, contradicted)  # type: ignore[arg-type]
    ]


def test_every_illegal_tuple_is_refused_rather_than_routed() -> None:
    """The illegal region is refused at the boundary and never sorted into a channel (criterion 3)."""
    illegal = illegal_tuples()
    assert len(illegal) == 260
    for kind, lifecycle, verification, stale, contradicted in illegal:
        with pytest.raises(ChannelError, match="illegal_artifact_tuple"):
            channel_of(kind, lifecycle, verification, stale, contradicted)  # type: ignore[arg-type]


def test_every_legal_tuple_assembles_without_violating_an_invariant() -> None:
    """Table-driven enumeration: the explicit half of "enumerate and fuzz" (§7.1)."""
    views: list[ArtifactView] = []
    stale_ids: set[str] = set()
    for seq, (kind, lifecycle, verification, stale, contradicted) in enumerate(legal_tuples()):
        needs_bound = kind == "abstraction"
        views.append(
            make_view(
                seq=seq,
                kind=kind,
                lifecycle=lifecycle,
                verification=verification,
                open_contradiction=contradicted,
                bound=BOUND if needs_bound else None,
                counterparts=(ConflictSide("other", "asserted", "the other side"),) if contradicted else (),
            )
        )
        if stale:
            stale_ids.add(f"a{seq}")

    assert len(views) == 60
    assert_clean(views, stale_for(frozenset(stale_ids)))


# --- determinism and replay (criterion 9) --------------------------------------------------------------


def random_views(rng: random.Random, count: int, *, start: int = 1000) -> list[ArtifactView]:
    """Draw views across the whole space, including tuples the contract calls illegal."""
    views: list[ArtifactView] = []
    for offset in range(count):
        kind = rng.choice(KINDS)
        lifecycle = rng.choice(LIFECYCLES)
        verification = rng.choice(VERIFICATIONS)
        contradicted = rng.random() < 0.35
        has_sources = rng.random() < 0.85
        has_bound = rng.random() < 0.7
        has_counterparts = rng.random() < 0.7
        views.append(
            make_view(
                seq=start + offset,
                kind=kind,
                lifecycle=lifecycle,
                verification=verification,
                open_contradiction=contradicted,
                sources=(OWN_SOURCE,) if has_sources else (),
                bound=BOUND if has_bound else None,
                counterparts=(ConflictSide("other", "asserted", "the other side"),) if has_counterparts else (),
            )
        )
    return views


def replay_note(seed: int, views: Sequence[ArtifactView], stale_ids: frozenset[str]) -> str:
    """Print the seed and synthetic inputs on an invariant or assembly failure."""
    return "\n".join(
        (f"seed={seed} (re-run with CME_FUZZ_SEED={seed})", f"stale_ids={sorted(stale_ids)}", *map(repr, views))
    )


def fuzz(seed: int, count: int = 60) -> None:
    """One seeded sweep. Failure prints its seed and inputs (§5.2, requirement 7.2)."""
    rng = random.Random(seed)
    views = random_views(rng, count, start=seed * 1000)
    stale_ids = frozenset(view.artifact_id for view in views if rng.random() < 0.3)
    staleness = stale_for(stale_ids)
    try:
        assembly = run(views, staleness)
        violations = check_invariants(views, assembly, staleness)
        assert not violations, "\n".join(f"{v.invariant}: {v.detail}" for v in violations)
    except Exception as exc:
        exc.add_note(replay_note(seed, views, stale_ids))
        raise


@pytest.mark.parametrize("seed", FIXED_SEEDS)
def test_fuzz_holds_the_invariants_on_a_fixed_seed(seed: int) -> None:
    fuzz(seed)


def test_fuzz_holds_the_invariants_on_the_randomised_seed() -> None:
    """One fresh seed per run, printed whether it passes or fails, and re-runnable via ``CME_FUZZ_SEED``."""
    override = os.environ.get("CME_FUZZ_SEED")
    seed = int(override) if override else int.from_bytes(os.urandom(4), "big")
    print(f"channel-integrity fuzz seed: {seed} (re-run with CME_FUZZ_SEED={seed})")
    fuzz(seed)


def test_a_failing_case_is_replayable_by_its_seed() -> None:
    """The same seed produces the same views and the same assembly — the gate is not a coin flip."""
    seed = 12345
    first = random_views(random.Random(seed), 30, start=seed * 1000)
    second = random_views(random.Random(seed), 30, start=seed * 1000)
    assert first == second

    left = run(first)
    right = run(second)
    assert left == right


# --- positive controls: every invariant must be able to fail (criterion 7) ------------------------------


def test_control_I1p_fails_when_an_inference_is_smuggled_into_facts() -> None:
    """A hand-built violation: an inferred artifact rendered as a grounded fact must be caught."""
    views = [make_view(seq=1, kind="abstraction", lifecycle="inferred", verification="unverified", bound=BOUND)]
    base = run(views)
    assert base.hypotheses and not base.facts

    provenance = base.hypotheses[0].provenance
    smuggled = GroundedFact("a1", "abstraction", "statement", provenance)
    doctored = replace(base, hypotheses=(), facts=(smuggled,))
    violations = check_invariants(views, doctored)
    assert any(v.invariant == "I1p" for v in violations), violations


def test_control_bounded_fails_when_the_bound_is_substituted() -> None:
    """Structural check: a BoundedFact carrying a different Bound from its view must be caught."""
    views = [make_view(seq=1, kind="abstraction", lifecycle="inferred", verification="verified", bound=BOUND)]
    base = run(views)
    assert base.facts and isinstance(base.facts[0], BoundedFact)

    other = Bound(1, 2, 0.5, 0.5, "cooccurrence", "3", "split-7", EXPIRES)
    swapped = BoundedFact.create("a1", "statement about item 1", other, base.facts[0].provenance)
    violations = check_invariants(views, replace(base, facts=(swapped,)))
    assert any(v.invariant == "bounded" for v in violations), violations


def test_control_bounded_fails_when_the_text_omits_the_bound() -> None:
    """A bounded fact whose text drops the bound is not renderable as a fact (I14)."""
    with pytest.raises(ChannelError, match="bound_must_be_part_of_the_rendered_text"):
        BoundedFact("a1", "statement", BOUND, "statement", ())


def test_control_conflicts_only_fails_when_a_contested_artifact_renders_as_fact() -> None:
    views = [make_view(seq=1, kind="claim", lifecycle="contested", verification="none")]
    base = run(views)
    assert base.conflicts and not base.facts

    provenance = base.conflicts[0].provenance
    leaked = GroundedFact("a1", "claim", "statement", provenance)
    violations = check_invariants(views, replace(base, conflicts=(), facts=(leaked,)))
    assert any(v.invariant == "conflicts_only" for v in violations), violations


def test_control_stale_to_hypotheses_fails_when_a_stale_abstraction_renders_as_fact() -> None:
    views = [make_view(seq=1, kind="abstraction", lifecycle="inferred", verification="verified", bound=BOUND)]
    staleness = stale_for(frozenset({"a1"}))

    stale_run = run(views, staleness)
    assert not stale_run.facts and stale_run.hypotheses, "a stale verified abstraction must not be a fact"

    provenance = stale_run.hypotheses[0].provenance
    as_fact = BoundedFact.create("a1", "statement about item 1", BOUND, provenance)
    violations = check_invariants(views, replace(stale_run, facts=(as_fact,), hypotheses=()), staleness)
    assert any(v.invariant == "stale_to_hypotheses" for v in violations), violations


def test_control_no_loss_fails_when_an_artifact_vanishes() -> None:
    views = [make_view(seq=1), make_view(seq=2)]
    base = run(views)
    assert len(base.facts) == 2

    dropped = replace(base, facts=base.facts[:1])
    violations = check_invariants(views, dropped)
    assert any(v.invariant == "no_loss" for v in violations), violations


def test_control_no_loss_fails_when_a_live_artifact_is_silently_dropped() -> None:
    """The subtle case: dropping a live artifact keeps the *count* balanced, so the count alone must not pass it."""
    views = [make_view(seq=1, kind="claim", lifecycle="asserted"), make_view(seq=2, lifecycle="retired")]
    base = run(views)
    assert len(base.facts) == 1 and base.excluded == 1

    doctored = replace(base, facts=(), excluded=2)
    violations = check_invariants(views, doctored)
    assert any(v.invariant == "no_loss" and "without a retirement" in v.detail for v in violations), violations


def test_control_no_loss_fails_when_an_artifact_is_rendered_twice() -> None:
    views = [make_view(seq=1)]
    base = run(views)
    doubled = replace(base, facts=base.facts + base.facts)
    violations = check_invariants(views, doubled)
    assert any(v.invariant == "no_loss" for v in violations), violations


# --- red-team: try to escape the contract rather than satisfy it (criterion 8) -------------------------


def test_redteam_an_abstraction_claiming_to_be_grounded_is_refused() -> None:
    """Kind/lifecycle smuggling: an abstraction is never observed or asserted (``V2-D48`` C-P1)."""
    attacks = [
        make_view(seq=1, kind="abstraction", lifecycle="asserted", verification="none"),
        make_view(seq=2, kind="abstraction", lifecycle="observed", verification="none"),
        make_view(seq=3, kind="procedure", lifecycle="observed", verification="none"),
        make_view(seq=4, kind="episode", lifecycle="asserted", verification="none"),
    ]
    assembly = run(attacks)
    assert not assembly.facts and not assembly.hypotheses
    assert len(assembly.refused) == 4
    assert {item.reason for item in assembly.refused} == {"illegal_artifact_tuple"}
    assert_clean(attacks)


def test_redteam_an_inference_pretending_to_be_grounded_is_refused() -> None:
    """``verification='none'`` on an inference would make it look like a fact; the tuple is illegal."""
    attacks = [
        make_view(seq=1, kind="claim", lifecycle="inferred", verification="none"),
        make_view(seq=2, kind="claim", lifecycle="inferred", verification="refuted"),
    ]
    assembly = run(attacks)
    assert len(assembly.refused) == 2
    assert {item.reason for item in assembly.refused} == {"illegal_artifact_tuple"}
    assert_clean(attacks)


def test_redteam_staleness_is_never_consulted_for_unverified_artifacts() -> None:
    """A lying staleness function must not be able to promote or demote an unverified artifact."""

    def explosive(view: ArtifactView) -> bool:
        if view.verification != "verified":
            raise AssertionError("staleness consulted for an unverified artifact")
        return False

    views = [
        make_view(seq=1, kind="claim", lifecycle="asserted", verification="none"),
        make_view(seq=2, kind="abstraction", lifecycle="inferred", verification="unverified", bound=BOUND),
        make_view(seq=3, kind="abstraction", lifecycle="inferred", verification="verified", bound=BOUND),
    ]
    assembly = run(views, explosive)
    assert len(assembly.facts) == 2
    assert len(assembly.hypotheses) == 1
    assert_clean(views, explosive)


def test_redteam_a_contradiction_without_a_counterpart_is_refused() -> None:
    """Showing one side of a contradiction and calling it settled would pick a winner silently."""
    attack = [make_view(seq=1, kind="claim", lifecycle="observed", verification="none", open_contradiction=True)]
    assembly = run(attack)
    assert not assembly.conflicts
    assert [item.reason for item in assembly.refused] == ["conflict_counterpart_missing"]
    assert_clean(attack)


def test_redteam_an_artifact_with_unmaterialisable_provenance_is_refused() -> None:
    """No sources means no bundle; rendering it would be a fact with no provenance (``I17``)."""
    attack = [make_view(seq=1, kind="claim", lifecycle="asserted", verification="none", sources=())]
    assembly = run(attack)
    assert not assembly.facts
    assert [item.reason for item in assembly.refused] == ["provenance_unmaterialisable"]
    assert_clean(attack)


def test_redteam_a_foreign_source_stays_opaque_and_still_renders() -> None:
    """A cross-tenant source must not break assembly, and must not leak its path (``I17``/``V2-D37``)."""
    view = make_view(seq=1, kind="claim", lifecycle="asserted", sources=(OWN_SOURCE, FOREIGN_SOURCE))
    assembly = run([view])
    assert len(assembly.facts) == 1
    rendered = assembly.facts[0]
    assert FOREIGN_SOURCE.scope_path not in repr(rendered.provenance)
    assert_clean([view])


def test_redteam_an_abstraction_in_facts_without_a_bound_is_refused() -> None:
    """The one bounded exception to ``I1'`` still requires the bound (``I14``)."""
    attack = [make_view(seq=1, kind="abstraction", lifecycle="inferred", verification="verified", bound=None)]
    assembly = run(attack)
    assert not assembly.facts
    assert [item.reason for item in assembly.refused] == ["bound_missing"]
    assert_clean(attack)


def test_redteam_an_empty_input_assembles_to_an_empty_result() -> None:
    """No views is not a licence to invent output."""
    assembly = run([])
    assert not assembly.facts and not assembly.hypotheses and not assembly.procedures and not assembly.conflicts
    assert not assembly.refused and assembly.excluded == 0
    assert assembly.cost.candidates_considered == assembly.cost.items_rendered == 0
    assert assembly.cost.tokens_considered == assembly.cost.tokens_rendered == 0
    assert all(
        assembly.coverage.channel(name).candidates == 0 for name in ("facts", "hypotheses", "procedures", "conflicts")
    )


def test_every_documented_refusal_reason_is_reachable() -> None:
    """All four refusal reasons exist and can be produced; the fuzz asserts none other appears."""
    produced = set()
    produced |= {item.reason for item in run([make_view(seq=1, kind="abstraction", lifecycle="asserted")]).refused}
    produced |= {item.reason for item in run([make_view(seq=2, sources=())]).refused}
    produced |= {
        item.reason
        for item in run(
            [make_view(seq=3, kind="abstraction", lifecycle="inferred", verification="verified", bound=None)]
        ).refused
    }
    produced |= {
        item.reason
        for item in run([make_view(seq=4, kind="claim", lifecycle="observed", open_contradiction=True)]).refused
    }
    assert produced == REFUSAL_REASONS


# --- totality over the whole space (criterion 6) -------------------------------------------------------


def test_every_combination_is_rendered_exactly_once_or_refused() -> None:
    """The no-loss property over the *entire* product space, not a sample: 320 inputs, 320 accounted for."""
    views = [
        make_view(
            seq=seq,
            kind=kind,
            lifecycle=lifecycle,
            verification=verification,
            open_contradiction=contradicted,
            bound=BOUND if kind == "abstraction" else None,
            counterparts=(ConflictSide("other", "asserted", "the other side"),) if contradicted else (),
        )
        for seq, (kind, lifecycle, verification, _stale, contradicted) in enumerate(
            product(KINDS, LIFECYCLES, VERIFICATIONS, (False,), (False, True))
        )
    ]
    assert len(views) == SPACE_SIZE // 2 == 160
    assert_clean(views)


def test_excluded_counts_retired_artifacts_without_rendering_them() -> None:
    """``retired`` is in no channel and is a count, never content (``V2-D72`` discipline)."""
    retired = [make_view(seq=index, lifecycle="retired") for index in range(5)]
    live = [make_view(seq=100, kind="claim", lifecycle="asserted")]
    assembly = assert_clean(retired + live)
    assert assembly.excluded == 5
    assert len(assembly.facts) == 1
    assert len(assembly.refused) == 0
