"""Four-channel membership and assembly (E-017; ``PLAN/V2/CHANNELS.md``, frozen by ``V2-D48``).

Retrieval results are returned in four channels: ``facts``, ``hypotheses``, ``procedures`` and ``conflicts``;
an artifact that belongs in none (``retired``) is rendered in no channel and is only visible to
``audit_as_of``. This is the runtime mechanism behind three invariants:

* ``I1'``  an inferred guess is never rendered as a fact (the single, bounded exception below);
* ``I14``  every rendered abstraction carries its bound;
* ``I17``  provenance into a scope the caller cannot read is opaque (``scope.provenance``, ``V2-D37``).

**Membership is a pure function** of ``(kind, lifecycle, verification, stale, open_contradiction)`` and nothing
else: not of rank, score, budget, caller or query. :func:`channel_of` is the function in ``CHANNELS.md`` section 1
and is checked against the executable reference model (``tests/test_channel_membership_model.py``).

**Staleness is derived, never stored** (``V2-D24``). The closure hash that will compute it does not exist until
``E-038``, so assembly takes the staleness function as an injected dependency; :func:`stale_of` is the
five-cause OR of ``CHANNELS.md`` section 2.3 for the day the inputs exist.

Ranking, per-channel budgets and cost reporting are out of scope (``E-033``). ``engine/render.py`` (v1) is
untouched and keeps working until ``E-033`` replaces it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from custom_memory_engine.model.axes import KINDS, LEGAL_LIFECYCLE, LIFECYCLES, Kind, Lifecycle
from custom_memory_engine.scope.access import ReadSet
from custom_memory_engine.scope.provenance import (
    ProvenanceKey,
    ProvenanceReference,
    ProvenanceSource,
    render_bundle,
)

type Verification = Literal["none", "unverified", "verified", "refuted"]
type Channel = Literal["facts", "hypotheses", "procedures", "conflicts", "none"]
type ContradictionOrigin = Literal["grounding_policy", "tier_c", "tier_b"]

VERIFICATIONS: tuple[Verification, ...] = ("none", "unverified", "verified", "refuted")
GROUNDED: tuple[Lifecycle, ...] = ("observed", "asserted")


class ChannelError(ValueError):
    """Static code; never echoes artifact content, identifiers or scopes."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- membership ----------------------------------------------------------------------------------------


def is_legal_tuple(
    kind: Kind, lifecycle: Lifecycle, verification: Verification, stale: bool, open_contradiction: bool
) -> bool:
    """Whether the engine can hold this combination (``CHANNELS.md`` section 1.1)."""
    if kind not in KINDS or lifecycle not in LIFECYCLES or verification not in VERIFICATIONS:
        return False
    if lifecycle not in LEGAL_LIFECYCLE[kind]:
        return False
    if lifecycle in GROUNDED:
        # Grounded artifacts carry no verification; only they can have an open contradiction.
        return verification == "none" and not stale
    if open_contradiction:
        return False
    if lifecycle == "inferred":
        # `refuted` always retires the artifact (inferred -> retired), so it is never `inferred`.
        return verification in ("unverified", "verified") and (not stale or verification == "verified")
    # contested / retired may carry the history of their verification; staleness needs `verified`.
    return not stale or verification == "verified"


def _is_fresh_verified_abstraction(kind: Kind, lifecycle: Lifecycle, verification: Verification, stale: bool) -> bool:
    return kind == "abstraction" and lifecycle == "inferred" and verification == "verified" and not stale


def channel_of(
    kind: Kind, lifecycle: Lifecycle, verification: Verification, stale: bool, open_contradiction: bool
) -> Channel:
    """The membership function of ``CHANNELS.md`` section 1. Illegal tuples raise; they are never routed."""
    if not is_legal_tuple(kind, lifecycle, verification, stale, open_contradiction):
        raise ChannelError("illegal_artifact_tuple")
    if lifecycle == "retired":
        return "none"
    if lifecycle == "contested" or open_contradiction:
        return "conflicts"
    if lifecycle == "inferred":
        return "facts" if _is_fresh_verified_abstraction(kind, lifecycle, verification, stale) else "hypotheses"
    return "procedures" if kind == "procedure" else "facts"


def opens_contradiction(origin: ContradictionOrigin, endpoint_lifecycles: tuple[Lifecycle, Lifecycle]) -> bool:
    """Whether a ``contradicts`` edge sets ``open_contradiction`` (``V2-D48`` C-P4).

    Only when both endpoints are grounded **and** the edge came from the grounding policy or a Tier C caller. A
    Tier B edge is stored as an association with no channel effect: demoting a fact is adjudication, which Tier B
    may not do (``API_V2.md`` TB2).
    """
    return origin in ("grounding_policy", "tier_c") and all(lifecycle in GROUNDED for lifecycle in endpoint_lifecycles)


@dataclass(frozen=True, slots=True)
class StalenessInputs:
    """The five causes of staleness (``CHANNELS.md`` section 2.3). ``E-037``/``E-038`` compute them."""

    closure_changed: bool
    support_below_threshold: bool
    source_retired: bool
    method_changed: bool
    expired: bool


def stale_of(inputs: StalenessInputs) -> bool:
    """Stale is the OR of the five causes. Without ``expired`` an expired preference would still render as fact."""
    return (
        inputs.closure_changed
        or inputs.support_below_threshold
        or inputs.source_retired
        or inputs.method_changed
        or inputs.expired
    )


# --- rendered items ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bound:
    """The statistical bound an abstraction must carry when rendered (``CHANNELS.md`` R3). Every field is required."""

    support_count: int
    opportunity_count: int
    confidence_lower: float
    coverage: float
    method_id: str
    method_version: str
    verified_on: str
    expires_at: datetime

    def __post_init__(self) -> None:
        for count in (self.support_count, self.opportunity_count):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ChannelError("bound_counts_must_be_nonnegative_integers")
        if self.opportunity_count == 0 or self.support_count > self.opportunity_count:
            raise ChannelError("bound_support_must_not_exceed_positive_opportunity")
        for share in (self.confidence_lower, self.coverage):
            if isinstance(share, bool) or not isinstance(share, int | float) or not 0.0 <= share <= 1.0:
                raise ChannelError("bound_shares_must_be_in_unit_interval")
        for label in (self.method_id, self.method_version, self.verified_on):
            if not isinstance(label, str) or not label.strip():
                raise ChannelError("bound_labels_required")
        if not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None:
            raise ChannelError("bound_expiry_must_be_timezone_aware")

    def summary(self) -> str:
        return (
            f"in {self.support_count} of {self.opportunity_count} observed cases "
            f"(confidence >= {self.confidence_lower:.2f}, coverage {self.coverage:.2f}; "
            f"{self.method_id}@{self.method_version}; verified on {self.verified_on}; "
            f"expires {self.expires_at.isoformat()})"
        )


def render_bounded_text(statement: str, bound: Bound) -> str:
    """The only text a bounded abstraction is rendered as: the bound is part of the sentence, not metadata."""
    return f"{bound.summary()}: {statement}"


@dataclass(frozen=True, slots=True)
class GroundedFact:
    """An ``observed`` or ``asserted`` episode or claim: a record of what a declared source said."""

    artifact_id: str
    kind: Kind
    text: str
    provenance: tuple[ProvenanceReference, ...]


@dataclass(frozen=True, slots=True)
class BoundedFact:
    """A fresh, verified abstraction. It cannot exist without its bound, and the bound is in its text (``I14``)."""

    artifact_id: str
    statement: str
    bound: Bound
    text: str
    provenance: tuple[ProvenanceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.bound, Bound):
            raise ChannelError("bounded_fact_requires_a_bound")
        if self.text != render_bounded_text(self.statement, self.bound):
            raise ChannelError("bound_must_be_part_of_the_rendered_text")

    @classmethod
    def create(
        cls, artifact_id: str, statement: str, bound: Bound, provenance: tuple[ProvenanceReference, ...]
    ) -> BoundedFact:
        return cls(artifact_id, statement, bound, render_bounded_text(statement, bound), provenance)


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """An inferred artifact. Always labelled; never presented as knowledge (``I1'``)."""

    artifact_id: str
    kind: Kind
    label: str
    text: str
    support_summary: str
    provenance: tuple[ProvenanceReference, ...]
    bound: Bound | None = None


@dataclass(frozen=True, slots=True)
class ProcedureItem:
    artifact_id: str
    text: str
    provenance: tuple[ProvenanceReference, ...]


@dataclass(frozen=True, slots=True)
class ConflictSide:
    artifact_id: str
    lifecycle: Lifecycle
    text: str


@dataclass(frozen=True, slots=True)
class ConflictItem:
    """A contested artifact, or a settled one with an open contradiction. It shows both sides and picks none."""

    artifact_id: str
    reason: Literal["contested", "open_contradiction"]
    sides: tuple[ConflictSide, ...]
    provenance: tuple[ProvenanceReference, ...]

    def __post_init__(self) -> None:
        needed = 2 if self.reason == "open_contradiction" else 1
        if len(self.sides) < needed:
            raise ChannelError("conflict_must_show_both_sides")


@dataclass(frozen=True, slots=True)
class Refusal:
    """An item assembly would not render. Content-free: an id and a static reason."""

    artifact_id: str
    reason: Literal[
        "illegal_artifact_tuple", "provenance_unmaterialisable", "bound_missing", "conflict_counterpart_missing"
    ]


@dataclass(frozen=True, slots=True)
class Assembly:
    facts: tuple[GroundedFact | BoundedFact, ...]
    hypotheses: tuple[Hypothesis, ...]
    procedures: tuple[ProcedureItem, ...]
    conflicts: tuple[ConflictItem, ...]
    refused: tuple[Refusal, ...]
    excluded: int  # artifacts in no channel (`retired`); a count, never content


# --- assembly ------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArtifactView:
    """What assembly needs to know about one retrieved artifact. There is deliberately no ``stale`` field."""

    artifact_id: str
    kind: Kind
    lifecycle: Lifecycle
    verification: Verification
    text: str
    sources: tuple[ProvenanceSource, ...]
    open_contradiction: bool = False
    bound: Bound | None = None
    support_summary: str = ""
    counterparts: tuple[ConflictSide, ...] = ()


type Staleness = Callable[[ArtifactView], bool]


def never_stale(_view: ArtifactView) -> bool:
    """A staleness function for callers (and tests) with no closure hash yet."""
    return False


def hypothesis_label(verification: Verification, stale: bool) -> str:
    """``CHANNELS.md`` R4: the visible label a hypothesis carries."""
    if verification == "verified":
        return "verified, needs re-verification" if stale else "verified"
    return "unverified"


def assemble(
    views: Iterable[ArtifactView],
    *,
    caller_user_id: str,
    read_set: ReadSet,
    key: ProvenanceKey,
    staleness: Staleness,
) -> Assembly:
    """Sort retrieved artifacts into the four channels and render their mandatory fields.

    Fails closed per item: an illegal tuple, a missing bound, a missing conflict counterpart, or provenance that
    cannot be materialised refuses that item (reported, never rendered) and does not abort the rest.
    """
    facts: list[GroundedFact | BoundedFact] = []
    hypotheses: list[Hypothesis] = []
    procedures: list[ProcedureItem] = []
    conflicts: list[ConflictItem] = []
    refused: list[Refusal] = []
    excluded = 0

    for view in views:
        stale = staleness(view) if view.verification == "verified" else False
        try:
            channel = channel_of(view.kind, view.lifecycle, view.verification, stale, view.open_contradiction)
        except ChannelError:
            refused.append(Refusal(view.artifact_id, "illegal_artifact_tuple"))
            continue
        if channel == "none":
            excluded += 1
            continue
        if not view.sources:
            refused.append(Refusal(view.artifact_id, "provenance_unmaterialisable"))
            continue
        provenance = render_bundle(list(view.sources), caller_user_id=caller_user_id, read_set=read_set, key=key)

        if channel == "facts":
            if view.kind == "abstraction":
                if view.bound is None:
                    refused.append(Refusal(view.artifact_id, "bound_missing"))
                    continue
                facts.append(BoundedFact.create(view.artifact_id, view.text, view.bound, provenance))
            else:
                facts.append(GroundedFact(view.artifact_id, view.kind, view.text, provenance))
        elif channel == "hypotheses":
            hypotheses.append(
                Hypothesis(
                    view.artifact_id,
                    view.kind,
                    hypothesis_label(view.verification, stale),
                    view.text,
                    view.support_summary,
                    provenance,
                    view.bound,
                )
            )
        elif channel == "procedures":
            procedures.append(ProcedureItem(view.artifact_id, view.text, provenance))
        else:
            reason: Literal["contested", "open_contradiction"] = (
                "contested" if view.lifecycle == "contested" else "open_contradiction"
            )
            own = ConflictSide(view.artifact_id, view.lifecycle, view.text)
            sides = (own, *view.counterparts)
            if reason == "open_contradiction" and not view.counterparts:
                refused.append(Refusal(view.artifact_id, "conflict_counterpart_missing"))
                continue
            conflicts.append(ConflictItem(view.artifact_id, reason, sides, provenance))

    return Assembly(tuple(facts), tuple(hypotheses), tuple(procedures), tuple(conflicts), tuple(refused), excluded)


__all__ = [
    "GROUNDED",
    "VERIFICATIONS",
    "ArtifactView",
    "Assembly",
    "Bound",
    "BoundedFact",
    "Channel",
    "ChannelError",
    "ConflictItem",
    "ConflictSide",
    "ContradictionOrigin",
    "GroundedFact",
    "Hypothesis",
    "ProcedureItem",
    "Refusal",
    "Staleness",
    "StalenessInputs",
    "Verification",
    "assemble",
    "channel_of",
    "hypothesis_label",
    "is_legal_tuple",
    "never_stale",
    "opens_contradiction",
    "render_bounded_text",
    "stale_of",
]
