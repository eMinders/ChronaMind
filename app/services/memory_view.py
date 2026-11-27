"""Map the engine's JSON results onto the memory inspector's response models. Pure functions, no I/O.

The engine host serialises its own result classes field by field (`host/app.py::_jsonable`), so the shapes read here are
the engine's: `QueryResult` (`facts`, `hypotheses`, `procedures`, `conflicts`, `audit`, `changes`, …), `Artifact`
(`artifact_id`, `kind`, `lifecycle`, `channel`, `content`, `verification`, `label`, `support_summary`, `bound`,
`conflict_sides`, `provenance`, …) and `ProvenanceView` (`artifact_id`, `depth`, `edges`, `depth_limited`).

A shape this module does not recognise raises `EngineShapeError` instead of guessing; the router answers it with 502, so
a contract change shows up as an error, not as a silently empty screen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.schemas.memory import (
    BoundOut,
    ChangeOut,
    ChangesView,
    ConflictSideOut,
    DeletionCounts,
    MemoryItem,
    MemoryView,
    ProvenanceEdgeOut,
    ProvenanceOut,
    SourceRefOut,
    TimelineView,
)

#: The channels a `query` result carries, in the order the inspector shows them.
CHANNELS = ("facts", "hypotheses", "procedures", "conflicts")


class EngineShapeError(ValueError):
    """The engine answered with a shape this module does not know. Raised rather than guessed around."""


def _mapping(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EngineShapeError(what)
    return value


def _sequence(value: object) -> list[Any]:
    """A JSON list, or an empty list when the field is absent (``None``)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise EngineShapeError("expected_list")
    return value


def source_from_reference(reference: object) -> SourceRefOut:
    """One provenance reference: readable (`source_id` …) or opaque (`opaque_ref`). The scope path is never kept."""
    data = _mapping(reference, "reference")
    if data.get("readable") is False or "opaque_ref" in data:
        return SourceRefOut(
            readable=False,
            source_type=None,
            derivation_type=str(data.get("derivation_type", "")),
            ref=str(data.get("opaque_ref", "")),
        )
    return SourceRefOut(
        readable=True,
        source_type=data.get("source_type"),
        derivation_type=str(data.get("derivation_type", "")),
        ref=str(data.get("source_id", "")),
    )


def _conflict_side(side: object) -> ConflictSideOut:
    data = _mapping(side, "conflict_side")
    return ConflictSideOut(id=str(data["artifact_id"]), lifecycle=data["lifecycle"], text=str(data["text"]))


def item_from_artifact(artifact: object) -> MemoryItem:
    """One engine `Artifact` as a `MemoryItem`. Fails loudly on a kind, lifecycle or channel outside the contract."""
    data = _mapping(artifact, "artifact")
    try:
        bound = data.get("bound")
        return MemoryItem(
            id=str(data["artifact_id"]),
            kind=data["kind"],
            lifecycle=data["lifecycle"],
            channel=data.get("channel") or "none",
            content=str(data.get("content") or ""),
            verification=str(data.get("verification") or ""),
            label=data.get("label"),
            support_summary=str(data.get("support_summary") or ""),
            bound=BoundOut.model_validate(bound) if bound is not None else None,
            conflict_sides=[_conflict_side(side) for side in _sequence(data.get("conflict_sides"))],
            sources=[source_from_reference(reference) for reference in _sequence(data.get("provenance"))],
        )
    except (KeyError, ValidationError) as error:
        raise EngineShapeError("artifact") from error


def _split_episodes(items: Iterable[MemoryItem]) -> tuple[list[MemoryItem], list[MemoryItem]]:
    """(not episodes, episodes), each in the engine's order."""
    others: list[MemoryItem] = []
    episodes: list[MemoryItem] = []
    for item in items:
        (episodes if item.kind == "episode" else others).append(item)
    return others, episodes


def view_from_query(result: object) -> MemoryView:
    """A `query` result as a `MemoryView`: the four channels, with episodes moved from `facts` to `conversation`.

    Only the `facts` channel can hold an episode (`CHANNELS.md`); an episode anywhere else is still moved, so the rule
    "a recorded turn is never shown as a fact" does not depend on where the engine put it.
    """
    data = _mapping(result, "query_result")
    channels = {channel: [item_from_artifact(a) for a in _sequence(data.get(channel))] for channel in CHANNELS}
    conversation: list[MemoryItem] = []
    sections: dict[str, list[MemoryItem]] = {}
    for channel, items in channels.items():
        sections[channel], episodes = _split_episodes(items)
        conversation.extend(episodes)
    return MemoryView(
        known_facts=sections["facts"],
        hypotheses=sections["hypotheses"],
        procedures=sections["procedures"],
        conflicts=sections["conflicts"],
        conversation=conversation,
    )


def timeline_from_query(at: datetime, result: object) -> TimelineView:
    """An `audit_as_of` result: its rows are in `audit`, never in the answer channels."""
    data = _mapping(result, "query_result")
    beliefs, conversation = _split_episodes(item_from_artifact(a) for a in _sequence(data.get("audit")))
    return TimelineView(at=at, beliefs=beliefs, conversation=conversation)


def changes_from_query(start: datetime, end: datetime, result: object) -> ChangesView:
    """A `change` result: each change names version ids, resolved against the items the same result carries."""
    data = _mapping(result, "query_result")
    by_id: dict[str, MemoryItem] = {}
    for channel in (*CHANNELS, "audit"):
        for artifact in _sequence(data.get(channel)):
            item = item_from_artifact(artifact)
            by_id.setdefault(item.id, item)
    changes: list[ChangeOut] = []
    for raw in _sequence(data.get("changes")):
        change = _mapping(raw, "change")
        try:
            changes.append(
                ChangeOut(
                    outcome=change["outcome"],
                    previous=[by_id[str(i)] for i in _sequence(change.get("previous")) if str(i) in by_id],
                    current=[by_id[str(i)] for i in _sequence(change.get("current")) if str(i) in by_id],
                )
            )
        except (KeyError, ValidationError) as error:
            raise EngineShapeError("change") from error
    return ChangesView(start=start, end=end, changes=changes)


def provenance_from_view(view: object) -> ProvenanceOut:
    """A `ProvenanceView`: edges carry references only."""
    data = _mapping(view, "provenance_view")
    try:
        edges = [_mapping(edge, "edge") for edge in _sequence(data.get("edges"))]
        return ProvenanceOut(
            id=str(data["artifact_id"]),
            depth=int(data["depth"]),
            depth_limited=bool(data.get("depth_limited", False)),
            edges=[
                ProvenanceEdgeOut(depth=int(edge["depth"]), source=source_from_reference(edge["reference"]))
                for edge in edges
            ],
        )
    except (KeyError, TypeError, ValidationError) as error:
        raise EngineShapeError("provenance_view") from error


def deletion_counts(receipt: object) -> DeletionCounts:
    """The engine's `DeletionReceipt` reduced to counts. `request_id` and `external_exclusions` are dropped."""
    data = _mapping(receipt, "deletion_receipt")
    try:
        return DeletionCounts.model_validate(
            {name: data[name] for name in DeletionCounts.model_fields if name in data}
        )
    except ValidationError as error:
        raise EngineShapeError("deletion_receipt") from error
