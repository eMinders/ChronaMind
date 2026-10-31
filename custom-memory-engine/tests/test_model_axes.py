"""B-05 / V2-D38: lifecycle vs retention, the closed transition table, and the v1 mapping."""

import itertools

import pytest

from custom_memory_engine.model.axes import (
    KINDS,
    LIFECYCLES,
    RETENTIONS,
    TRANSITIONS,
    V1_LAYERS,
    V1_STATUSES,
    AxisError,
    is_allowed_transition,
    map_v1_version,
    require_transition,
    validate_axes,
)


def test_archived_is_not_a_retention_or_lifecycle_value() -> None:
    assert "archived" not in RETENTIONS and "archived" not in LIFECYCLES
    with pytest.raises(AxisError) as error:
        validate_axes("claim", "retired", "archived")
    assert error.value.code == "unknown_retention"


def test_v1_archived_maps_to_retired_and_durable() -> None:
    for layer in V1_LAYERS:
        axes = map_v1_version("archived", layer, False)
        assert (axes.lifecycle, axes.retention, axes.reason_code) == ("retired", "durable", "v1_archived")


def test_mapping_is_total_deterministic_and_always_valid() -> None:
    seen = 0
    for status, layer, derived in itertools.product(V1_STATUSES, V1_LAYERS, (False, True)):
        first, second = map_v1_version(status, layer, derived), map_v1_version(status, layer, derived)
        assert first == second
        validate_axes(first.kind, first.lifecycle, first.retention)  # never violates a floor
        seen += 1
    assert seen == 5 * 4 * 2


@pytest.mark.parametrize(
    ("status", "layer", "derived", "expected"),
    [
        ("active", "episodic", False, ("episode", "observed")),
        ("active", "semantic", False, ("claim", "asserted")),
        ("active", "semantic", True, ("claim", "inferred")),
        ("active", "opinion", False, ("claim", "asserted")),
        ("active", "persona", False, ("abstraction", "inferred")),
        ("conflict_pending", "semantic", False, ("claim", "contested")),
        ("rejected", "semantic", False, ("claim", "retired")),
        ("candidate", "semantic", False, ("claim", "retired")),
    ],
)
def test_m1_layer_and_status_mapping(status: str, layer: str, derived: bool, expected: tuple[str, str]) -> None:
    axes = map_v1_version(status, layer, derived)
    assert (axes.kind, axes.lifecycle) == expected
    assert axes.retention == "durable"


@pytest.mark.parametrize(("status", "layer", "derived"), [("deleted", "semantic", False), ("active", "x", False)])
def test_unknown_v1_values_are_rejected(status: str, layer: str, derived: bool) -> None:
    with pytest.raises(AxisError):
        map_v1_version(status, layer, derived)


def test_derivation_flag_must_be_boolean_not_truthy() -> None:
    with pytest.raises(AxisError):
        map_v1_version("active", "semantic", 1)


def test_retention_floor_rejects_short_retention_for_claims_and_abstractions() -> None:
    for kind, retention in itertools.product(KINDS, RETENTIONS):
        if retention != "durable" and kind in ("claim", "abstraction"):
            with pytest.raises(AxisError):
                validate_axes(kind, "asserted", retention)
        else:
            validate_axes(kind, "asserted", retention)


def test_transition_table_is_closed_and_has_no_promotion_from_inferred() -> None:
    assert not is_allowed_transition("inferred", "asserted")  # §2.3 rule 1
    assert not is_allowed_transition("retired", "asserted")  # retired is never resurrected
    allowed = {(s, t) for s in (None, *LIFECYCLES) for t in LIFECYCLES if is_allowed_transition(s, t)}
    assert allowed == set(TRANSITIONS)
    assert all(target != "asserted" or source in (None, "contested") for source, target in TRANSITIONS)


def test_retired_is_reachable_from_every_believed_state() -> None:
    for source in ("observed", "asserted", "inferred", "contested"):
        assert is_allowed_transition(source, "retired")


def test_require_transition_rejects_unknown_and_forbidden() -> None:
    with pytest.raises(AxisError) as error:
        require_transition("inferred", "asserted")
    assert error.value.code == "lifecycle_transition_not_permitted"
    with pytest.raises(AxisError):
        require_transition("archived", "retired")  # type: ignore[arg-type]
