"""Procedure storage through ``Engine.call`` (``E-030``, issue #201 criteria 2, 3, 4, 6, 7, 9 and 11).

The claim: *a procedure carries the shape the architecture describes, and the engine can select it by preconditions*.
Everything runs through the public entry point under a ``NOSUPERUSER NOBYPASSRLS`` role against the real migrated
schema: procedures are written by ``assert``/``ingest_artifact``, read back by ``get``/``query``, and selected by the
pure matcher over what ``query`` returned. Selection is shown together with the **exclusion** of the non-applicable
procedure, and two controls show the assertions fail when the exclusion or the kind guard is removed.

Criterion 8 (the ``E-031`` planner consumes the matcher) is not here: the planner does not exist on ``main`` yet.
"""

from collections.abc import Mapping, Sequence

import pytest

from custom_memory_engine.api import ingest_handlers
from custom_memory_engine.api.entry import Engine, EntryError
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.outcome_handlers import register_outcome_handlers
from custom_memory_engine.api.read_handlers import Artifact, QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.engine import procedure_matching, procedure_shape
from custom_memory_engine.engine.procedure_matching import ApplicabilityRequest, ArtifactFacts, applicable
from custom_memory_engine.engine.procedure_ranking import OutcomeStats
from custom_memory_engine.engine.procedure_shape import ProcedureShape, ProcedureShapeError, parse_shape
from custom_memory_engine.grounding.sources import canonical_hash
from tests.integration.test_api_wiring_structure import database, rows, world  # noqa: F401  (fixture)
from tests.integration.test_read_path_db import caller
from tests.integration.test_record_outcome_db import seed_turn

pytestmark = pytest.mark.integration

ROOT = "/u/alice/"
FIX_CI = [{"op": "exists", "match": {"kind": "episode", "lifecycle": "observed"}}]
NEEDS_AN_ABSTRACTION = [{"op": "exists", "match": {"kind": "abstraction"}}]
STEPS = ["Rerun the focused suite.", "Check the missing import in the failing module."]


def wire(engine: Engine) -> None:
    assert isinstance(engine.resolver, DbTargets)
    runtime = engine.resolver.runtime
    register_ingest_handlers(engine, runtime)
    register_read_handlers(engine, runtime)
    register_outcome_handlers(engine, runtime)


def evidence(key: str, content: str) -> list[dict[str, str]]:
    return [{"type": "turn", "ref": f"turn:{key}", "span": content, "content_hash": canonical_hash(content)}]


async def store(
    engine: Engine,
    conn,
    key: str,
    content: str,
    *,
    sequence: int,
    kind: str = "procedure",
    operation: str = "assert",
    shape: Mapping[str, object] | None = None,
) -> str:
    """Store one artifact via the entry point; ``shape`` holds ``preconditions``/``steps`` as a caller sends them."""
    shape = shape or {}
    await seed_turn(conn, f"turn:{key}", content, sequence=sequence)
    if operation == "assert":
        arguments: dict[str, object] = {
            "scope": ROOT, "kind": kind, "content": content, "evidence": evidence(key, content),
            "event_range": None, "idempotency_key": key, **shape,
        }  # fmt: skip
    else:
        arguments = {
            "scope": ROOT, "kind": kind, "candidate": {"content": content, **shape},
            "evidence": evidence(key, content), "idempotency_key": key,
        }  # fmt: skip
    receipt = await engine.call(caller("alice"), operation, arguments)
    assert isinstance(receipt, Receipt), receipt
    return receipt.id


async def get(engine: Engine, artifact_id: str) -> Artifact:
    artifact = await engine.call(caller("alice"), "get", {"artifact_id": artifact_id})
    assert isinstance(artifact, Artifact)
    return artifact


async def query(engine: Engine) -> QueryResult:
    result = await engine.call(caller("alice"), "query", {"request": {"scope": ROOT}})
    assert isinstance(result, QueryResult)
    return result


async def refusal(
    engine: Engine,
    conn,
    key: str,
    content: str,
    *,
    sequence: int,
    shape: Mapping[str, object],
    kind: str = "procedure",
    operation: str = "assert",
) -> EntryError:
    with pytest.raises(EntryError) as refused:
        await store(engine, conn, key, content, sequence=sequence, kind=kind, operation=operation, shape=shape)
    return refused.value


# ---------------------------------------------------------------------------------------------- criterion 2, 11


@pytest.mark.parametrize("operation", ["assert", "ingest_artifact"])
async def test_preconditions_and_steps_round_trip_through_the_entry_point(database, operation: str) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        procedure = await store(
            engine, conn, "p1", "Fix a failing CI run", sequence=2, operation=operation,
            shape={"preconditions": FIX_CI, "steps": STEPS},
        )  # fmt: skip
        expected = parse_shape(FIX_CI, STEPS)
        got = await get(engine, procedure)
        assert (got.preconditions, got.steps) == (expected.preconditions, expected.steps)
        (listed,) = (await query(engine)).procedures
        assert (listed.artifact_id, listed.preconditions, listed.steps) == (procedure, *_shape(expected))
        # stored once, in the side table, exactly as sent
        assert await rows(
            conn, "SELECT preconditions, steps FROM procedure_shape WHERE version_id = %s", (procedure,)
        ) == [(FIX_CI, STEPS)]


def _shape(shape: ProcedureShape) -> tuple[object, object]:
    return shape.preconditions, shape.steps


async def test_a_replayed_assert_returns_the_same_procedure_and_stores_its_shape_once(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        first = await store(engine, conn, "p1", "Fix a failing CI run", sequence=2, shape={"preconditions": FIX_CI})
        again = await engine.call(
            caller("alice"),
            "assert",
            {
                "scope": ROOT, "kind": "procedure", "content": "Fix a failing CI run",
                "evidence": evidence("p1", "Fix a failing CI run"), "event_range": None, "idempotency_key": "p1",
                "preconditions": FIX_CI,
            },
        )  # fmt: skip
        assert isinstance(again, Receipt) and again.id == first
        assert await rows(conn, "SELECT count(*) FROM procedure_shape") == [(1,)]


# ---------------------------------------------------------------------------------------------- criterion 3, 4


async def assert_a_claim_with_a_shape_is_refused(engine: Engine, conn) -> None:
    """Criterion 3: ``validation_error``, never silently dropped and never ``forbidden``; nothing is stored."""
    for sequence, (key, shape) in enumerate(
        (("c1", {"preconditions": FIX_CI}), ("c2", {"steps": STEPS}), ("c3", {"preconditions": []})), start=2
    ):
        refused = await refusal(
            engine, conn, key, "The user is vegetarian", sequence=sequence, kind="claim", shape=shape
        )
        assert (refused.code, refused.detail) == ("validation_error", "procedure_shape_requires_a_procedure"), key
    assert await rows(conn, "SELECT count(*) FROM memory_version WHERE kind = 'claim'") == [(0,)]
    assert await rows(conn, "SELECT count(*) FROM procedure_shape") == [(0,)]


async def test_a_non_procedure_write_with_a_shape_is_refused_with_validation_error(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        await assert_a_claim_with_a_shape_is_refused(engine, conn)
        # the same claim without a shape is stored: the refusal is about the shape, not the claim
        await store(engine, conn, "c9", "The user is vegetarian", sequence=9, kind="claim")


@pytest.mark.parametrize("operation", ["assert", "ingest_artifact"])
@pytest.mark.parametrize(
    ("shape", "code"),
    [
        ({"preconditions": [{"op": "run_steps", "match": {"kind": "claim"}}]}, "precondition_operator_unknown"),
        ({"preconditions": [{"op": "exists", "match": {"kind": "fact"}}]}, "precondition_match_invalid"),
        ({"preconditions": [{"op": "scope_within", "scope": "/u/alice"}]}, "precondition_scope_invalid"),
        ({"preconditions": {"op": "exists"}}, "preconditions_must_be_a_list"),
        ({"steps": [""]}, "step_invalid"),
    ],
)
async def test_a_malformed_shape_is_rejected_at_write_time_and_nothing_is_stored(  # noqa: F811
    database,  # noqa: F811
    operation: str,
    shape: Mapping[str, object],
    code: str,
) -> None:
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        refused = await refusal(
            engine, conn, "p1", "Fix a failing CI run", sequence=2, operation=operation, shape=shape
        )
        assert (refused.code, refused.detail) == ("validation_error", code)
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE kind = 'procedure'") == [(0,)]


# ---------------------------------------------------------------------------------------------- criteria 6, 7, 9


async def selection(engine: Engine) -> tuple[tuple[str, ...], Sequence[str]]:
    """Select applicable procedures as a reader would: everything ``query`` returned, matched by the pure function."""
    result = await query(engine)
    artifacts = [
        ArtifactFacts(kind=a.kind, lifecycle=a.lifecycle)
        for channel in ("facts", "hypotheses", "procedures", "conflicts")
        for a in result.channel(channel)
    ]
    procedures = [a.artifact_id for a in result.procedures]
    shapes = {
        a.artifact_id: ProcedureShape(a.preconditions or (), a.steps or ()) for a in result.procedures
    }  # fmt: skip
    return applicable(procedures, shapes, ApplicabilityRequest(ROOT), artifacts), procedures


async def a_ci_session(engine: Engine, conn) -> tuple[str, str, str]:
    """Two procedures of one kind that differ only in preconditions, one with none, and a CI episode to match."""
    fix_ci = await store(
        engine, conn, "fix", "Fix a failing CI run", sequence=2, shape={"preconditions": FIX_CI, "steps": STEPS}
    )
    summarise = await store(
        engine, conn, "sum", "Summarise the abstraction", sequence=3, shape={"preconditions": NEEDS_AN_ABSTRACTION}
    )
    anywhere = await store(engine, conn, "any", "Ask before a destructive command", sequence=4)  # no shape at all
    episode = await engine.call(
        caller("alice"),
        "ingest_episode",
        {
            "scope": ROOT,
            "source": {"type": "external", "ref": "ci://run/42", "span": "log", "content_hash": canonical_hash("log")},
            "content": "CI run 42 failed: NameError",
            "idempotency_key": "ep",
        },
    )
    assert isinstance(episode, Receipt)
    return fix_ci, summarise, anywhere


async def assert_selection_and_exclusion(engine: Engine, fix_ci: str, summarise: str, anywhere: str) -> None:
    selected, procedures = await selection(engine)
    assert set(procedures) == {fix_ci, summarise, anywhere}  # all three are stored and routed to `procedures`
    assert fix_ci in selected, "the applicable procedure was not selected"  # criterion 6
    assert summarise not in selected, "the non-applicable procedure was not excluded"  # criterion 7
    assert anywhere in selected  # criterion 9: no preconditions, always applicable


async def test_the_applicable_procedure_is_selected_and_the_other_is_excluded(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        await assert_selection_and_exclusion(engine, *await a_ci_session(engine, conn))


async def test_a_procedure_without_preconditions_reads_back_empty_and_is_always_applicable(database) -> None:  # noqa: F811
    """Issue #201 §5.4: no preconditions is "always applicable", so every procedure written before E-030 works."""
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        plain = await store(engine, conn, "any", "Ask before a destructive command", sequence=2)
        got = await get(engine, plain)
        assert (got.preconditions, got.steps) == ((), ())
        assert await rows(conn, "SELECT count(*) FROM procedure_shape") == [(0,)]  # nothing invented for it
        selected, _ = await selection(engine)
        assert selected == (plain,)


async def test_a_shape_changes_no_channel_and_no_other_kind_carries_one(database) -> None:  # noqa: F811
    """``V2-D75``/``I1'``: membership is a pure function of the axes; preconditions are not one of them."""
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        shaped = await store(engine, conn, "p1", "Fix a failing CI run", sequence=2, shape={"preconditions": FIX_CI})
        plain = await store(engine, conn, "p2", "Ask before a destructive command", sequence=3)
        claim = await store(engine, conn, "c1", "The user is vegetarian", sequence=4, kind="claim")
        result = await query(engine)
        assert {a.artifact_id for a in result.procedures} == {shaped, plain}
        assert [a.artifact_id for a in result.facts] == [claim]
        assert (result.facts[0].preconditions, result.facts[0].steps) == (None, None)


async def test_record_outcome_works_unchanged_on_a_procedure_with_a_shape(database) -> None:  # noqa: F811
    """Criterion 10's neighbour: the feedback loop and the shape live in separate side tables and do not interact."""
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        procedure = await store(
            engine, conn, "p1", "Fix a failing CI run", sequence=2, shape={"preconditions": FIX_CI}
        )
        stats = await engine.call(caller("alice"), "record_outcome", {"procedure_id": procedure, "outcome": "success"})
        assert isinstance(stats, OutcomeStats) and (stats.successes, stats.failures) == (1, 0)
        assert (await get(engine, procedure)).preconditions == parse_shape(FIX_CI).preconditions


# ---------------------------------------------------------------------------------------------- review regressions


async def test_a_shape_is_not_attached_when_the_gate_stores_no_row_for_the_candidate(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate's ``reinforce_duplicate`` path stores no row for the candidate (it bumps the existing version's
    counter). The shape then has no procedure to attach to: the call must answer as the gate decided, not fail with
    ``internal_error`` on ``procedure_shape_requires_a_procedure``. Simulated, because the generic predicates do not
    reach that branch."""
    async with world(database) as (conn, engine, _schema):
        wire(engine)

        async def stores_nothing(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(ingest_handlers, "apply_candidate", stores_nothing)
        await store(engine, conn, "p1", "Fix a failing CI run", sequence=2, shape={"preconditions": FIX_CI})
        assert await rows(conn, "SELECT count(*) FROM procedure_shape") == [(0,)]


async def test_a_stored_shape_the_parser_cannot_read_is_an_internal_error_not_the_readers_fault(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the two grammars ever drift, a read must not blame the reader with ``validation_error``."""
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        procedure = await store(
            engine, conn, "p1", "Fix a failing CI run", sequence=2, shape={"preconditions": FIX_CI}
        )

        def drifted(*_args: object) -> ProcedureShape:
            raise ProcedureShapeError("precondition_operator_unknown")

        monkeypatch.setattr(procedure_shape, "parse_shape", drifted)
        with pytest.raises(EntryError) as refused:
            await engine.call(caller("alice"), "get", {"artifact_id": procedure})
        assert (refused.value.code, refused.value.detail) == ("internal_error", None)


# ---------------------------------------------------------------------------------------------- controls (§11.5)


async def test_control_removing_the_exclusion_clause_fails_criterion_7(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With every condition made to hold, the non-applicable procedure is no longer excluded, and criterion 7 fails."""
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        session = await a_ci_session(engine, conn)
        monkeypatch.setattr(procedure_matching, "_holds", lambda *_: True)  # test-local; the product has no switch
        with pytest.raises(AssertionError) as broken:
            await assert_selection_and_exclusion(engine, *session)
        print(f"\ncontrol (exclusion removed) -> {str(broken.value).splitlines()[0]}")
        assert "not excluded" in str(broken.value)


async def test_control_dropping_the_kind_guard_fails_criterion_3(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the handler's kind check, a claim's shape reaches the database, whose own guard refuses it: the call
    still fails closed and stores nothing, but with ``internal_error`` instead of the contract's ``validation_error``,
    so criterion 3's assertion fails."""
    async with world(database) as (conn, engine, _schema):
        wire(engine)

        def no_kind_guard(_kind: object, preconditions: object, steps: object) -> ProcedureShape | None:
            if preconditions is None and steps is None:
                return None
            try:
                return parse_shape(preconditions, steps)
            except ProcedureShapeError as error:
                raise EntryError("validation_error", error.code) from None

        monkeypatch.setattr(ingest_handlers, "_procedure_shape", no_kind_guard)
        with pytest.raises(AssertionError) as broken:
            await assert_a_claim_with_a_shape_is_refused(engine, conn)
        print("\ncontrol (kind guard dropped) -> " + " | ".join(str(broken.value).splitlines()[:2]))
        assert "internal_error" in str(broken.value)
        assert await rows(conn, "SELECT count(*) FROM procedure_shape") == [(0,)]  # the schema's guard still held
