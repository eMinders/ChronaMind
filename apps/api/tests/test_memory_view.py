"""The engine-result mapping behind the memory inspector (`apps/api/app/services/memory_view.py`). Pure functions, no server.

The fixtures below are written in the engine host's own wire shape (`host/app.py::_jsonable` over `QueryResult`,
`Artifact`, `ProvenanceView`, `DeletionReceipt`); the live tests (`tests/integration/test_memory_router_live.py`) prove
the same mapping against a real host.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.services.memory_view import (
    EngineShapeError,
    changes_from_query,
    deletion_counts,
    item_from_artifact,
    provenance_from_view,
    timeline_from_query,
    view_from_query,
)

T1 = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 20, 11, 0, tzinfo=UTC)


def artifact(artifact_id: str, kind: str, content: str, *, lifecycle: str = "asserted", channel: str = "facts",
             **extra: Any) -> dict[str, Any]:  # fmt: skip
    """An engine `Artifact` exactly as the host serialises it."""
    return {
        "artifact_id": artifact_id,
        "bound": None,
        "channel": channel,
        "conflict_sides": [],
        "content": content,
        "hop_distance": 0,
        "kind": kind,
        "label": None,
        "lifecycle": lifecycle,
        "preconditions": None,
        "provenance": [
            {
                "source_id": f"app://session/turn-{artifact_id}",
                "scope_path": "/u/42/",
                "source_type": "external",
                "derivation_type": "evidence",
                "readable": True,
            }
        ],
        "scope_path": "/u/42/",
        "steps": None,
        "support_summary": "",
        "verification": "unverified",
        **extra,
    }


def test_a_recorded_turn_is_never_a_known_fact() -> None:
    result = {
        "facts": [
            artifact("v-claim", "claim", "I moved to Paris"),
            artifact("v-turn", "episode", "I live in Berlin", lifecycle="observed"),
        ],
        "hypotheses": [],
        "procedures": [],
        "conflicts": [],
    }
    view = view_from_query(result)

    assert [item.content for item in view.known_facts] == ["I moved to Paris"]
    assert [item.content for item in view.conversation] == ["I live in Berlin"]


def test_an_episode_outside_the_facts_channel_is_still_moved_to_the_conversation_record() -> None:
    result = {"conflicts": [artifact("v-turn", "episode", "said", lifecycle="contested", channel="conflicts")]}
    view = view_from_query(result)

    assert view.conflicts == []
    assert [item.id for item in view.conversation] == ["v-turn"]


def test_missing_channels_are_empty_sections_and_is_empty_says_so() -> None:
    view = view_from_query({})
    assert view.is_empty()
    assert view.all_items() == []


def test_a_readable_source_keeps_its_reference_and_drops_the_scope_path() -> None:
    item = item_from_artifact(artifact("v-1", "claim", "x"))

    assert item.sources[0].readable is True
    assert item.sources[0].source_type == "external"
    assert item.sources[0].ref == "app://session/turn-v-1"
    assert "/u/42/" not in item.model_dump_json()


def test_an_opaque_source_carries_its_handle_and_nothing_else() -> None:
    opaque = {"opaque_ref": "opq-abc", "derivation_type": "evidence", "readable": False}
    item = item_from_artifact(artifact("v-1", "claim", "x", provenance=[opaque]))

    assert item.sources[0].readable is False
    assert item.sources[0].source_type is None
    assert item.sources[0].ref == "opq-abc"


def test_a_bounded_abstraction_keeps_its_bound() -> None:
    """`I14`: an abstraction never renders without its bound, so the mapping must never drop one."""
    bound = {
        "support_count": 34,
        "opportunity_count": 40,
        "confidence_lower": 0.91,
        "coverage": 0.7,
        "method_id": "m-1",
        "method_version": "1",
        "verified_on": "held-out",
        "expires_at": "2027-01-01T00:00:00+00:00",
    }
    item = item_from_artifact(
        artifact(
            "v-a",
            "abstraction",
            "prefers mornings",
            lifecycle="inferred",
            bound=bound,
            support_summary="in 34 of 40 observed sessions",
        )  # fmt: skip
    )

    assert item.bound is not None and item.bound.support_count == 34
    assert item.support_summary == "in 34 of 40 observed sessions"


def test_both_sides_of_a_conflict_are_kept() -> None:
    sides = [
        {"artifact_id": "v-1", "lifecycle": "asserted", "text": "Berlin"},
        {"artifact_id": "v-2", "lifecycle": "contested", "text": "Munich"},
    ]
    item = item_from_artifact(artifact("v-2", "claim", "Munich", lifecycle="contested", channel="conflicts",
                                       conflict_sides=sides))  # fmt: skip

    assert [(side.id, side.text) for side in item.conflict_sides] == [("v-1", "Berlin"), ("v-2", "Munich")]


@pytest.mark.parametrize(
    "broken",
    [
        {"kind": "memory"},  # a kind outside the contract
        {"lifecycle": "archived"},  # `archived` is not a lifecycle (V2-D38)
        {"channel": "answers"},
    ],
)
def test_a_value_outside_the_contract_is_an_error_not_a_guess(broken: dict[str, str]) -> None:
    with pytest.raises(EngineShapeError):
        item_from_artifact({**artifact("v-1", "claim", "x"), **broken})


def test_an_artifact_without_an_id_is_an_error() -> None:
    broken = artifact("v-1", "claim", "x")
    del broken["artifact_id"]
    with pytest.raises(EngineShapeError):
        item_from_artifact(broken)


def test_the_timeline_reads_the_audit_rows_and_splits_out_recorded_turns() -> None:
    result = {
        "facts": [artifact("v-ignored", "claim", "an answer row is not an audit row")],
        "audit": [
            artifact("v-berlin", "claim", "I live in Berlin", channel="none"),
            artifact("v-turn", "episode", "I live in Berlin", lifecycle="observed"),
        ],
        "is_audit": True,
    }
    timeline = timeline_from_query(T1, result)

    assert timeline.at == T1
    assert [item.id for item in timeline.beliefs] == ["v-berlin"]
    assert [item.id for item in timeline.conversation] == ["v-turn"]


def test_changes_resolve_version_ids_and_omit_ids_the_result_does_not_carry() -> None:
    result = {
        "facts": [artifact("v-paris", "claim", "I moved to Paris")],
        "audit": [artifact("v-berlin", "claim", "I live in Berlin", lifecycle="retired", channel="none")],
        "changes": [
            {"outcome": "changed", "previous": ["v-berlin"], "current": ["v-paris"]},
            {"outcome": "added", "previous": [], "current": ["v-not-in-result"]},
        ],
    }
    changes = changes_from_query(T1, T2, result)

    assert (changes.start, changes.end) == (T1, T2)
    first, second = changes.changes
    assert first.outcome == "changed"
    assert [item.content for item in first.previous] == ["I live in Berlin"]
    assert [item.content for item in first.current] == ["I moved to Paris"]
    assert second.outcome == "added" and second.current == []


def test_provenance_edges_carry_references_only() -> None:
    view = {
        "artifact_id": "v-paris",
        "depth": 1,
        "depth_limited": True,
        "edges": [
            {
                "depth": 1,
                "reference": {
                    "source_id": "app://session/turn-1",
                    "scope_path": "/u/42/",
                    "source_type": "external",
                    "derivation_type": "evidence",
                    "readable": True,
                },
            }
        ],
    }
    provenance = provenance_from_view(view)

    assert (provenance.id, provenance.depth, provenance.depth_limited) == ("v-paris", 1, True)
    assert provenance.edges[0].source.ref == "app://session/turn-1"
    assert "/u/42/" not in provenance.model_dump_json()


def test_a_deletion_receipt_is_reduced_to_counts() -> None:
    receipt = {
        "request_id": "a1b2c3-digest",
        "scope": "all",
        "deletion_epoch": 3,
        "deleted_versions": 7,
        "dependents": 2,
        "rederived_dependents": 0,
        "purged_jobs": 1,
        "external_exclusions": [{"source_type": "external", "content_hash": "f00d"}],
        "external_exclusion_count": 1,
        "retired_dependents": 1,
        "deleted_turns": 4,
        "deleted_entities": 0,
        "deleted_mentions": 0,
    }
    counts = deletion_counts(receipt)
    dumped = counts.model_dump_json()

    assert (counts.deletion_epoch, counts.deleted_versions, counts.deleted_turns) == (3, 7, 4)
    assert "a1b2c3-digest" not in dumped and "f00d" not in dumped
