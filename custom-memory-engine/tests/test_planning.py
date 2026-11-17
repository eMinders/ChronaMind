"""E-031 planner: purity, determinism, the plan type and the model seam (no database).

These are the properties that do not need a connection: the planner is a pure function, so everything it promises can
be asserted on the values it returns. The four temporal modes end to end, replay against a real read, and the
"temporal stays a filter" property live in ``tests/integration/test_planning_db.py``.

**What "pure" means here, and why it is asserted rather than assumed.** Criterion 3 says the planner does no I/O. A
docstring cannot enforce that, so ``test_the_planner_does_no_io`` reads the module's own AST and fails on an import it
must never have (a connection layer, a clock, a network client). The check is on imports because that is what a
regression would actually add.

**The determinism tests are byte comparisons on purpose.** ``Plan`` holds tuples and frozen fields, so an unordered
collection entering it would still *look* equal while iterating differently. Comparing ``json.dumps(plan.to_dict())``
twice catches an ordering bug that ``plan == plan`` would miss, because dict insertion order follows the iteration that
built it.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest

from custom_memory_engine.engine.planning import (
    MODES,
    PLAN_INTENT_REFUSED,
    STAGES,
    TEMPORAL_INDEX,
    Intent,
    Plan,
    Planner,
    PlanningError,
    RuleBasedPlanner,
    plan_retrieval,
    read_scopes,
    record_nothing,
)
from custom_memory_engine.engine.predicates import QueryMode
from custom_memory_engine.scope.access import ReadEntry, ReadSet

T0 = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)


def _read_set(*entries: ReadEntry) -> ReadSet:
    return ReadSet(entries, ())


ALICE = _read_set(
    ReadEntry("/u/alice/", frozenset({"private", "project", "shared"}), "subtree"),
    ReadEntry("/u/alice/p/chrona/", frozenset({"project"}), "subtree"),
)


def _intent(mode: str = "current_state", **extra: object) -> Intent:
    fields: dict[str, object] = {"limit": 200}
    if mode == "historical_as_of":
        fields["target_time"] = T0
    elif mode == "historical_window":
        fields.update(window_start=T0, window_end=T1)
    elif mode == "audit_as_of":
        fields.update(target_time=T0, belief_time=T1)
    fields.update(extra)
    return Intent(mode=mode, **fields)  # type: ignore[arg-type]


def _bytes(plan: Plan) -> str:
    return json.dumps(plan.to_dict(), sort_keys=False)


# ─────────────────────────────────────────────────────────── criterion 1: the type ──


def test_a_plan_serialises_and_round_trips() -> None:
    """Criterion 1: a plan is inspectable and serialisable, and its record rebuilds to an equal plan."""
    plan = plan_retrieval(_intent("historical_window"), ALICE)
    rebuilt = Plan.from_dict(json.loads(_bytes(plan)))
    assert rebuilt == plan, "a recorded plan must rebuild to the identical plan"
    assert _bytes(rebuilt) == _bytes(plan)
    payload = plan.to_dict()
    assert payload["mode"] == "historical_window"
    assert payload["index"] == TEMPORAL_INDEX
    assert payload["stages"] == list(STAGES)


def test_a_plan_carries_only_the_instants_its_mode_uses() -> None:
    """A mode's plan carries the instants that describe *its* question, and nothing it ignores."""
    current = plan_retrieval(_intent("current_state"), ALICE)
    assert (current.target_time, current.belief_time, current.window_start, current.window_end) == (None,) * 4
    window = plan_retrieval(_intent("historical_window"), ALICE)
    assert window.window_start == T0.isoformat() and window.window_end == T1.isoformat()
    assert window.target_time is None and window.belief_time is None
    audit = plan_retrieval(_intent("audit_as_of"), ALICE)
    assert audit.target_time == T0.isoformat() and audit.belief_time == T1.isoformat()
    assert audit.window_start is None


def test_the_plan_carries_no_reference_instant() -> None:
    """``V2-D73``: ρ is the engine clock, so no plan may carry one — a plan cannot pin belief time."""
    fields = set(Plan.__dataclass_fields__)
    assert "reference_time" not in fields
    assert "reference_time" not in Plan.to_dict(plan_retrieval(_intent(), ALICE))


def test_a_truncated_plan_record_is_refused_rather_than_replayed() -> None:
    """A record missing a field must not replay as a different query (fail closed)."""
    payload = plan_retrieval(_intent(), ALICE).to_dict()
    for field in ("mode", "route", "index", "filters", "rows", "scopes", "stages"):
        truncated = {k: v for k, v in payload.items() if k != field}
        with pytest.raises(PlanningError):
            Plan.from_dict(truncated)


# ────────────────────────────────────────────────────── criterion 2: purity + order ──


def test_the_same_intent_produces_byte_identical_plans() -> None:
    """Criterion 2: the same inputs produce a byte-identical plan, twice, in one process."""
    first = _bytes(plan_retrieval(_intent("historical_as_of"), ALICE))
    second = _bytes(plan_retrieval(_intent("historical_as_of"), ALICE))
    assert first == second, "the plan differs between two identical calls: an unordered iteration leaked in"


@pytest.mark.parametrize("mode", sorted(MODES))
def test_every_mode_plans_deterministically(mode: str) -> None:
    """Determinism holds for all four modes, not just the default one."""
    assert _bytes(plan_retrieval(_intent(mode), ALICE)) == _bytes(plan_retrieval(_intent(mode), ALICE))


def test_read_set_insertion_order_does_not_change_the_plan() -> None:
    """The plan's order is the plan's, not the read set's: rebuilt in reverse, the bytes are unchanged."""
    forward = _read_set(
        ReadEntry("/u/alice/", frozenset({"private", "project", "shared"}), "subtree"),
        ReadEntry("/u/alice/p/chrona/", frozenset({"project"}), "subtree"),
    )
    backward = _read_set(*reversed(forward.entries))
    assert _bytes(plan_retrieval(_intent(), forward)) == _bytes(plan_retrieval(_intent(), backward))


def test_visibility_order_does_not_change_the_plan() -> None:
    """A frozenset of visibilities iterates in hash order; the plan sorts it, so equal sets give equal bytes."""
    one = _read_set(ReadEntry("/u/alice/", frozenset({"private", "project", "shared"}), "subtree"))
    other = _read_set(ReadEntry("/u/alice/", frozenset(["shared", "project", "private"]), "subtree"))
    assert _bytes(plan_retrieval(_intent(), one)) == _bytes(plan_retrieval(_intent(), other))


def test_read_scopes_have_a_total_order() -> None:
    """Every collection the plan carries has a total order, so equality of plans is a real question."""
    scopes = read_scopes(ALICE)
    assert scopes == tuple(sorted(scopes, key=lambda scope: (scope.path, scope.mode)))
    assert all(isinstance(scope.visibilities, tuple) for scope in scopes)


_SUBPROCESS_PLAN = """
import json
from custom_memory_engine.engine.planning import Intent, plan_retrieval
from custom_memory_engine.scope.access import ReadEntry, ReadSet
entries = (
    ReadEntry("/u/alice/p/chrona/", frozenset({"project", "shared"}), "subtree"),
    ReadEntry("/u/alice/", frozenset({"private", "project", "shared"}), "subtree"),
    ReadEntry("/u/alice/notes/", frozenset({"shared", "private"}), "exact"),
)
print(json.dumps(plan_retrieval(Intent(mode="current_state", limit=50), ReadSet(entries, ())).to_dict()))
"""


def test_the_plan_is_identical_across_hash_seeds() -> None:
    """§5.5: the test that catches set iteration. One process cannot: two identical sets iterate alike in it.

    String hashing is randomised **per process** (``PYTHONHASHSEED``), so a frozenset of visibilities iterates in a
    different order in different processes. Planning the same intent under several seeds and comparing the bytes is
    therefore the direct test that no unordered collection reaches the plan.
    """
    outputs = set()
    for seed in ("0", "1", "2", "3", "42", "2718"):
        completed = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_PLAN],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        outputs.add(completed.stdout)
    assert len(outputs) == 1, f"the plan differs across hash seeds ({len(outputs)} variants): unordered iteration"


# ───────────────────────────────────────────────────────── criterion 3: no I/O ──


def test_the_planner_does_no_io() -> None:
    """Criterion 3: the planner module imports no connection, clock or network layer.

    Import-level, because that is what a regression would add. ``scope.access`` is allowed: it is the read-set data
    model, not a query layer. An import under ``if TYPE_CHECKING:`` is **not** counted: it never runs, so it cannot
    perform I/O — the planner uses one for the ``QueryMode`` alias precisely so the connection layer stays out.
    """
    source = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine" / "engine" / "planning.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    # one pass to build a parent map: a type-only import is one inside an `if TYPE_CHECKING:` body
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def guarded(node: ast.AST) -> bool:
        """Is this node inside an ``if TYPE_CHECKING:`` body (a type-only import)?"""
        current = parents.get(node)
        while current is not None:
            test = current.test if isinstance(current, ast.If) else None
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                return True
            current = parents.get(current)
        return False

    for node in ast.walk(tree):
        if guarded(node):
            continue
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {
        "psycopg",
        "custom_memory_engine.engine.predicates",
        "custom_memory_engine.api.entry",
        "custom_memory_engine.api.runtime",
        "socket",
        "httpx",
        "requests",
        "time",
    }
    assert not (imported & forbidden), f"the planner imported an I/O layer: {sorted(imported & forbidden)}"
    # and no function in it is async: an await is where I/O would hide
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))


def test_the_planner_modes_are_the_predicate_modes() -> None:
    """``MODES`` is stated in the planner to keep it I/O-free; it must equal the predicate's own vocabulary."""
    assert frozenset(get_args(QueryMode.__value__)) == MODES


def test_planning_is_a_pure_function_of_its_arguments() -> None:
    """Same intent and read set → equal plan; the read set is not mutated."""
    entries_before = ALICE.entries
    first = plan_retrieval(_intent(), ALICE)
    plan_retrieval(_intent(), ALICE)
    assert ALICE.entries == entries_before
    assert first == plan_retrieval(_intent(), ALICE)


# ────────────────────────────────────────────────── criteria 9 + 10: seam, stage names ──


def test_the_rule_based_planner_is_the_only_implementation() -> None:
    """Criterion 9: the seam exists (a protocol) and the rule-based planner is its only implementation."""
    assert isinstance(RuleBasedPlanner(), Planner), "the rule-based planner must satisfy its own protocol"
    # A Protocol is structural, so an implementation need not subclass it: find every class with a `plan(intent, …)`.
    source = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine"
    implementations: set[str] = set()
    for path in source.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if (
                    isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    and item.name == "plan"
                    and [arg.arg for arg in item.args.args[1:2]] == ["intent"]
                ):
                    implementations.add(node.name)
    assert implementations == {"Planner", "RuleBasedPlanner"}, f"a second planner appeared: {sorted(implementations)}"


def test_no_model_call_is_made() -> None:
    """Criterion 9: no model client is imported anywhere in the planner module."""
    source = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine" / "engine" / "planning.py"
    text = source.read_text(encoding="utf-8")
    for needle in ("openai", "anthropic", "litellm", "langchain", "langfuse"):
        assert needle not in text.lower(), f"a model client name appears in the planner: {needle}"


def test_the_stages_are_named_for_e036() -> None:
    """Criterion 10: the five stages are named with ``ARCHITECTURE_V2.md`` §9's words, in order.

    ``E-036`` (#206) measures these names; if the two lists drift, per-stage cost attributes to a stage that is not
    planned. This is the one place the vocabulary is declared.
    """
    assert STAGES == ("plan", "candidate_generation", "expansion", "filter_rerank", "assemble")


def test_a_plan_may_not_invent_stages() -> None:
    """A plan whose stage list differs from the declared one is refused, not silently carried."""
    fields = plan_retrieval(_intent(), ALICE).to_dict()
    payload = {**fields, "stages": ["plan", "other"]}
    with pytest.raises(PlanningError):
        Plan.from_dict(payload)


def test_every_mode_plans_to_an_index_that_exists() -> None:
    """§4.4: a plan names an index the schema has. Only the temporal index is on this path."""
    for mode in sorted(MODES):
        assert plan_retrieval(_intent(mode), ALICE).index == TEMPORAL_INDEX


# ──────────────────────────────────────────────── unknown intent: refused, not defaulted ──


def test_an_unknown_mode_is_refused_rather_than_defaulted() -> None:
    """Recommended 3: a silent default is indistinguishable from a bug in a replay."""
    with pytest.raises(PlanningError) as caught:
        Intent(mode="recently", limit=10)  # type: ignore[arg-type]
    assert caught.value.code == PLAN_INTENT_REFUSED


def test_a_plan_whose_route_disagrees_with_its_mode_is_unrepresentable() -> None:
    """``V2-D73``: only ``audit_as_of`` routes to ``audit``. The other combination cannot be built."""
    payload = {**plan_retrieval(_intent("current_state"), ALICE).to_dict(), "route": "audit"}
    with pytest.raises(PlanningError):
        Plan.from_dict(payload)


def test_a_nonpositive_row_limit_is_refused() -> None:
    for limit in (0, -1, True):
        with pytest.raises(PlanningError):
            Intent(mode="current_state", limit=limit)  # type: ignore[arg-type]


def test_the_default_recorder_does_nothing() -> None:
    """Recording is opt-in: an unnoticed plan cannot become a hidden side channel."""
    assert record_nothing(plan_retrieval(_intent(), ALICE)) is None


# ──────────────────────────────────────────────────────────── criterion 11: profiles ──


def test_the_planner_does_not_branch_on_a_profile_name() -> None:
    """Criterion 11: no profile name in the planner, and no profile import."""
    source = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine" / "engine" / "planning.py"
    text = source.read_text(encoding="utf-8").lower()
    for needle in ("coding", "companion", "dual-profile", "import profile", "profiles."):
        assert needle not in text, f"the planner mentions a profile: {needle}"


def test_the_read_scopes_come_from_the_caller_read_set() -> None:
    """``I7``: the plan's scopes are the caller's own, so a plan cannot widen what it reads."""
    plan = plan_retrieval(_intent(), ALICE)
    assert {scope.path for scope in plan.scopes} == {entry.path for entry in ALICE.entries}
