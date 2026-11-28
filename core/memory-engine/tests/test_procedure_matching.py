"""Preconditions matching and the procedure-shape parser as pure functions (E-030, issue #201 criteria 4-7 and 9).

No database: :func:`matches` and :func:`parse_shape` are deterministic functions of their arguments. The database
twin of the parser (``guard_procedure_shape``, migration 21) is pinned to agree with it in
``tests/integration/test_migration_m21.py``.
"""

import pytest

from custom_memory_engine.engine.procedure_matching import ApplicabilityRequest, ArtifactFacts, applicable, matches
from custom_memory_engine.engine.procedure_shape import (
    MAX_PRECONDITIONS,
    MAX_STEP_LENGTH,
    MAX_STEPS,
    NO_SHAPE,
    Precondition,
    ProcedureShape,
    ProcedureShapeError,
    parse_shape,
)

REPO = "/u/alice/p/chrona/"
CI_FAILURE = ArtifactFacts(kind="episode", lifecycle="observed", predicate="episode")
DIET = ArtifactFacts(kind="claim", lifecycle="asserted", facet="preference", predicate="diet")


def pre(*raw: dict[str, object]) -> tuple[Precondition, ...]:
    return parse_shape(list(raw)).preconditions


# ---------------------------------------------------------------------------------------------- each operator


def test_scope_within_holds_for_the_scope_and_its_subtree_only() -> None:
    condition = pre({"op": "scope_within", "scope": REPO})
    assert matches(condition, ApplicabilityRequest(REPO), [])
    assert matches(condition, ApplicabilityRequest(REPO + "s/s1/"), [])
    assert not matches(condition, ApplicabilityRequest("/u/alice/"), [])  # an ancestor is not inside
    assert not matches(condition, ApplicabilityRequest("/u/alice/p/chrona-old/"), [])  # segment, not string, prefix


def test_scope_within_is_not_satisfied_by_a_request_that_names_no_scope() -> None:
    """Missing information never makes a procedure applicable."""
    assert not matches(pre({"op": "scope_within", "scope": REPO}), ApplicabilityRequest(None), [CI_FAILURE])


def test_exists_holds_when_one_artifact_matches_every_field() -> None:
    condition = pre({"op": "exists", "match": {"kind": "episode", "predicate": "episode"}})
    assert matches(condition, ApplicabilityRequest(), [DIET, CI_FAILURE])
    assert not matches(condition, ApplicabilityRequest(), [DIET])
    # every field must match on the same artifact, not across two
    mixed = pre({"op": "exists", "match": {"kind": "episode", "facet": "preference"}})
    assert not matches(mixed, ApplicabilityRequest(), [DIET, CI_FAILURE])


def test_absent_holds_when_no_artifact_matches_and_on_an_empty_set() -> None:
    condition = pre({"op": "absent", "match": {"facet": "preference"}})
    assert matches(condition, ApplicabilityRequest(), [CI_FAILURE])
    assert matches(condition, ApplicabilityRequest(), [])
    assert not matches(condition, ApplicabilityRequest(), [CI_FAILURE, DIET])


def test_lifecycle_is_matched_like_every_other_field() -> None:
    inferred = ArtifactFacts(kind="claim", lifecycle="inferred")
    condition = pre({"op": "exists", "match": {"kind": "claim", "lifecycle": "asserted"}})
    assert not matches(condition, ApplicabilityRequest(), [inferred])
    assert matches(condition, ApplicabilityRequest(), [inferred, DIET])


def test_the_conditions_are_a_conjunction() -> None:
    both = pre(
        {"op": "scope_within", "scope": REPO},
        {"op": "exists", "match": {"kind": "episode"}},
    )
    assert matches(both, ApplicabilityRequest(REPO), [CI_FAILURE])
    assert not matches(both, ApplicabilityRequest(REPO), [DIET])  # second fails
    assert not matches(both, ApplicabilityRequest("/u/bob/"), [CI_FAILURE])  # first fails


@pytest.mark.parametrize("scope", ["/u/alice/p/chrona", "/u/alice/p/chrona/../x/", "u/alice/", "/u/alice/P/"])
def test_a_request_scope_must_be_canonical(scope: str) -> None:
    """The literal-prefix rule is exact only for canonical paths, so a non-canonical request scope is refused."""
    with pytest.raises(ValueError):
        ApplicabilityRequest(scope)


# ---------------------------------------------------------------------------------------------- §5.4: no preconditions


def test_a_procedure_with_no_preconditions_is_always_applicable() -> None:
    """The empty conjunction is true: every procedure written before E-030 keeps working (issue #201 §5.4)."""
    for request in (ApplicabilityRequest(None), ApplicabilityRequest(REPO), ApplicabilityRequest("/u/bob/")):
        assert matches((), request, [])
        assert matches(NO_SHAPE.preconditions, request, [DIET])
    assert applicable(["p"], {}, ApplicabilityRequest(None), []) == ("p",)  # no stored shape at all


# ---------------------------------------------------------------------------------------------- criteria 6 and 7


def test_two_procedures_of_one_kind_the_applicable_is_selected_and_the_other_excluded() -> None:
    """Issue #201 W5: same kind, different preconditions; the exclusion is asserted directly, not just inclusion."""
    shapes = {
        "fix-ci": ProcedureShape(pre({"op": "exists", "match": {"kind": "episode", "predicate": "episode"}})),
        "plan-menu": ProcedureShape(pre({"op": "exists", "match": {"facet": "preference"}})),
        "anywhere": NO_SHAPE,
    }
    selected = applicable(["fix-ci", "plan-menu", "anywhere"], shapes, ApplicabilityRequest(REPO), [CI_FAILURE])
    assert selected == ("fix-ci", "anywhere")
    assert "plan-menu" not in selected


def test_applicable_is_a_filter_that_keeps_the_given_order_and_never_adds() -> None:
    shapes = {"b": ProcedureShape(pre({"op": "absent", "match": {"kind": "claim"}}))}
    assert applicable(["c", "b", "a"], shapes, ApplicabilityRequest(), [CI_FAILURE]) == ("c", "b", "a")
    assert applicable(["c", "b", "a"], shapes, ApplicabilityRequest(), [DIET]) == ("c", "a")
    assert applicable([], shapes, ApplicabilityRequest(), [DIET]) == ()


def test_matching_is_deterministic_and_does_not_consume_its_inputs() -> None:
    conditions = pre({"op": "exists", "match": {"kind": "episode"}}, {"op": "absent", "match": {"kind": "claim"}})
    artifacts = iter([CI_FAILURE])  # a one-shot iterator is materialised once, then reused for every condition
    assert matches(conditions, ApplicabilityRequest(), artifacts)
    assert [matches(conditions, ApplicabilityRequest(), [CI_FAILURE]) for _ in range(3)] == [True] * 3


# ------------------------------------------------------------------------------------------- the parser (criterion 4)


def test_a_shape_round_trips_through_its_json_form() -> None:
    raw = [
        {"op": "scope_within", "scope": REPO},
        {"op": "exists", "match": {"predicate": "episode", "kind": "episode"}},
        {"op": "absent", "match": {"lifecycle": "contested"}},
    ]
    shape = parse_shape(raw, ["Rerun the focused suite.", "Check the import."])
    assert parse_shape([c.to_json() for c in shape.preconditions], list(shape.steps)) == shape
    assert shape.steps == ("Rerun the focused suite.", "Check the import.")
    assert parse_shape(None, None) == NO_SHAPE == parse_shape([], [])


@pytest.mark.parametrize(
    ("preconditions", "steps", "code"),
    [
        ("scope_within", None, "preconditions_must_be_a_list"),
        ({"op": "exists"}, None, "preconditions_must_be_a_list"),
        (["x"], None, "precondition_must_be_an_object"),
        ([{"op": "run_steps", "match": {"kind": "claim"}}], None, "precondition_operator_unknown"),
        ([{"match": {"kind": "claim"}}], None, "precondition_operator_unknown"),
        ([{"op": "scope_within"}], None, "precondition_fields_invalid"),
        ([{"op": "scope_within", "scope": REPO, "extra": 1}], None, "precondition_fields_invalid"),
        ([{"op": "scope_within", "scope": "/u/alice"}], None, "precondition_scope_invalid"),
        ([{"op": "scope_within", "scope": "/u/alice/P/"}], None, "precondition_scope_invalid"),
        ([{"op": "exists", "match": {}}], None, "precondition_fields_invalid"),
        ([{"op": "exists", "match": {"kind": "claim"}, "scope": REPO}], None, "precondition_fields_invalid"),
        ([{"op": "exists", "match": {"kind": "fact"}}], None, "precondition_match_invalid"),
        ([{"op": "exists", "match": {"lifecycle": "verified"}}], None, "precondition_match_invalid"),
        ([{"op": "absent", "match": {"facet": "inferred_preference"}}], None, "precondition_match_invalid"),
        ([{"op": "exists", "match": {"predicate": "Diet"}}], None, "precondition_match_invalid"),
        ([{"op": "exists", "match": {"content": "x"}}], None, "precondition_match_invalid"),
        ([{"op": "exists", "match": {"kind": 3}}], None, "precondition_match_invalid"),
        ([{"op": "absent", "match": {"kind": "claim"}}] * (MAX_PRECONDITIONS + 1), None, "too_many_preconditions"),
        (None, "do it", "steps_must_be_a_list"),
        (None, ["ok", ""], "step_invalid"),
        (None, ["　\t"], "step_invalid"),
        (None, [3], "step_invalid"),
        (None, ["x" * (MAX_STEP_LENGTH + 1)], "step_invalid"),
        (None, ["x"] * (MAX_STEPS + 1), "too_many_steps"),
    ],
)
def test_a_malformed_shape_is_refused_with_a_static_code(preconditions: object, steps: object, code: str) -> None:
    with pytest.raises(ProcedureShapeError) as refused:
        parse_shape(preconditions, steps)
    assert refused.value.code == code
    assert str(refused.value) == code  # a static code, never the (content-bearing) input


def test_the_bounds_themselves_are_accepted() -> None:
    shape = parse_shape(
        [{"op": "absent", "match": {"kind": "claim"}}] * MAX_PRECONDITIONS, ["x" * MAX_STEP_LENGTH] * MAX_STEPS
    )
    assert (len(shape.preconditions), len(shape.steps)) == (MAX_PRECONDITIONS, MAX_STEPS)
