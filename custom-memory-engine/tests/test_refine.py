"""E-034: the result handle, read-set containment and the rule-based instruction vocabulary (``V2-D83``).

These are the parts of ``refine`` that need no database: the handle store, the interpreter and the plan narrowing.
The read path itself is exercised against the real schema in ``tests/integration/test_refine_db.py``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from custom_memory_engine.api.read_handlers import QueryResult
from custom_memory_engine.api.refine import (
    INSTRUCTION_REQUIRED,
    INSTRUCTION_UNRECOGNISED,
    RESULT_PREFIX,
    RESULT_TTL,
    Instruction,
    InstructionError,
    ResultStore,
    parse_instruction,
)
from custom_memory_engine.engine.channels import ChannelBudgets
from custom_memory_engine.engine.planning import (
    ASSOCIATION_EXPANSION,
    DEFAULT_HOP_BUDGET,
    PLAN_INTENT_REFUSED,
    Intent,
    PlanningError,
    RuleBasedPlanner,
    refine_plan,
)
from custom_memory_engine.scope.access import ReadEntry, ReadSet, Selector, covers, resolve_read_set

INTENT = Intent(mode="current_state", limit=5)
BUDGETS = ChannelBudgets(facts=64, hypotheses=64)
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def read_set(*entries: ReadEntry) -> ReadSet:
    return ReadSet(entries, ())


def alice(*paths: str) -> ReadSet:
    return resolve_read_set("alice", [Selector.of(path) for path in paths], [])


class Clock:
    """A clock a test moves by hand, so expiry is decided rather than waited for."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


# --- the handle ----------------------------------------------------------------------------------------


def test_a_handle_is_opaque_random_and_never_derivable_from_the_result() -> None:
    """Criterion 1: the id is a capability, not a digest of content or of the request."""
    store = ResultStore()
    first = store.issue(
        principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("a1", "a2")
    )
    second = store.issue(
        principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("a1", "a2")
    )
    assert first.startswith(RESULT_PREFIX) and len(first) == len(RESULT_PREFIX) + 32
    assert first != second, "two results with identical content must not share a handle"
    assert all(character in "0123456789abcdef" for character in first.removeprefix(RESULT_PREFIX))
    assert not {"a1", "a2"} & {first, second}


def test_resolving_a_handle_returns_what_the_drill_down_needs() -> None:
    store = ResultStore()
    held = alice("/u/alice/p/repo/")
    handle = store.issue(principal="alice", budgets=BUDGETS, read_set=held, intent=INTENT, candidates=("a1",), depth=2)
    snapshot = store.resolve("alice", handle)
    assert snapshot is not None
    assert snapshot.principal == "alice"
    assert snapshot.read_set == held
    assert snapshot.intent == INTENT
    assert snapshot.budgets == BUDGETS, "a refine re-assembles with the answer's own budgets"
    assert snapshot.candidates == ("a1",)
    assert snapshot.depth == 2


@pytest.mark.parametrize(
    "guess",
    [
        "",
        "r1",
        "res_",
        RESULT_PREFIX + "0" * 31,
        RESULT_PREFIX + "Z" * 32,
        "opq_" + "0" * 32,
        "res_" + "0" * 32 + "\n",
        None,
        7,
    ],
)
def test_a_guessed_handle_names_nothing(guess: object) -> None:
    """Criterion 1/5: no lookup, no error that distinguishes "never existed" from "wrong shape"."""
    assert ResultStore().resolve("alice", guess) is None


def test_a_foreign_handle_is_unknown_to_its_reader_and_to_nobody_else() -> None:
    """Criterion 5: another principal's handle answers exactly like a handle that was never issued."""
    store = ResultStore()
    handle = store.issue(
        principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("a1",)
    )
    assert store.resolve("bob", handle) is None
    assert store.resolve("alice", handle) is not None  # the owner still holds it: one lookup, one answer


def test_an_expired_handle_is_dropped_not_merely_refused() -> None:
    clock = Clock()
    store = ResultStore(ttl=timedelta(minutes=5), clock=clock)
    handle = store.issue(
        principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("a1",)
    )
    clock.advance(timedelta(minutes=4, seconds=59))
    assert store.resolve("alice", handle) is not None
    clock.advance(timedelta(seconds=2))
    assert store.resolve("alice", handle) is None
    assert len(store) == 0, "an expired entry is removed, so it cannot be revived by moving the clock back"


def test_the_store_is_bounded_and_evicts_the_oldest_handle_first() -> None:
    store = ResultStore(capacity=3)
    handles = [
        store.issue(
            principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=(f"a{i}",)
        )
        for i in range(4)
    ]
    assert len(store) == 3
    assert store.resolve("alice", handles[0]) is None, "the oldest issued handle is the one evicted"
    assert all(store.resolve("alice", handle) is not None for handle in handles[1:])


def test_another_principal_burst_preserves_a_live_handle_and_total_capacity_stays_bounded() -> None:
    store = ResultStore(capacity=3)
    held = store.issue(
        principal="bob", budgets=BUDGETS, read_set=alice("/u/bob/"), intent=INTENT, candidates=("held",)
    )
    for index in range(4):
        store.issue(
            principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=(str(index),)
        )

    assert store.resolve("bob", held) is not None
    resident_by_principal = sum(len(handles) for handles in store._principals.values())
    assert resident_by_principal == len(store)
    assert resident_by_principal <= store._capacity


def test_admission_reissues_a_surplus_slot_before_evicting_a_principal_and_keeps_one_entry_each() -> None:
    """The reservation is one resident handle per principal, not every handle a principal ever held (`T-47`).

    A single hard ceiling cannot admit a newcomer while another principal holds every slot: something must give.
    The rule, pinned here: admission reissues the largest surplus (oldest entry first), so every resident principal
    keeps at least one; after that a principal that needs capacity sheds **its own** surplus first, so an incumbent's
    entries are not taken by a neighbour's continued bursts; only when every principal holds exactly one is the
    least-recently-used principal evicted whole.
    """
    store = ResultStore(capacity=3)
    bob = [
        store.issue(principal="bob", budgets=BUDGETS, read_set=alice("/u/bob/"), intent=INTENT, candidates=(str(n),))
        for n in range(3)
    ]
    alice_handle = store.issue(
        principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("a1",)
    )

    assert store.resolve("bob", bob[0]) is None, "admission reissues the largest surplus, oldest entry first"
    assert store.resolve("bob", bob[2]) is not None, "a principal keeps its newest entry, and its reservation"
    assert store.resolve("alice", alice_handle) is not None, "the newcomer is admitted"
    assert len(store) == 3

    second = store.issue(
        principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("a2",)
    )

    assert store.resolve("bob", bob[1]) is not None, "the active principal sheds its own surplus before a neighbour's"
    assert store.resolve("bob", bob[2]) is not None
    assert store.resolve("alice", second) is not None, "the newest handle is always the one that survives"
    assert store.resolve("alice", alice_handle) is None, "alice's own oldest entry is what her second query costs"
    assert len(store) == 3


def test_an_evicted_handle_still_resolves_as_unknown() -> None:
    store = ResultStore(capacity=1)
    first = store.issue(
        principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("first",)
    )
    store.issue(principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("second",))
    assert store.resolve("alice", first) is None


def test_issuing_while_full_drops_expired_entries_before_live_ones() -> None:
    clock = Clock()
    store = ResultStore(ttl=timedelta(minutes=1), capacity=2, clock=clock)
    stale = store.issue(
        principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("a1",)
    )
    fresh = store.issue(
        principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("a2",)
    )
    clock.advance(timedelta(minutes=1, seconds=1))
    newest = store.issue(
        principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("a3",)
    )
    assert store.resolve("alice", stale) is None
    assert store.resolve("alice", fresh) is None  # expired as well; both were dropped, nothing was evicted live
    assert store.resolve("alice", newest) is not None


@pytest.mark.parametrize(("ttl", "capacity"), [(timedelta(0), 1), (timedelta(minutes=-1), 1), (RESULT_TTL, 0)])
def test_the_store_refuses_a_configuration_that_could_not_be_bounded(ttl: timedelta, capacity: int) -> None:
    with pytest.raises(ValueError):
        ResultStore(ttl=ttl, capacity=capacity)


def test_the_clock_must_be_timezone_aware() -> None:
    store = ResultStore(clock=lambda: datetime(2026, 3, 10, 12, 0))
    with pytest.raises(ValueError):
        store.issue(principal="alice", budgets=BUDGETS, read_set=alice("/u/alice/"), intent=INTENT, candidates=("a1",))


# --- the instruction vocabulary ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "operator", "argument"),
    [
        ("artifact a1", "artifact", "a1"),
        ("ARTIFACT a1", "artifact", "a1"),
        ("channel hypotheses", "channel", "hypotheses"),
        ("Channel FACTS", "channel", "facts"),
        ("mention vegetarian", "mention", "vegetarian"),
        ("mention two  spaces", "mention", "two  spaces"),
        ("scope /u/alice/p/repo/", "scope", "/u/alice/p/repo/"),
    ],
)
def test_every_instruction_operator_has_one_documented_spelling(text: str, operator: str, argument: str) -> None:
    parsed = parse_instruction(text)
    assert (parsed.operator, parsed.argument) == (operator, argument)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        None,
        7,
        "frobnicate a1",  # no rule understands this operator
        "artifact",  # an operator with no argument
        "artifact   ",
        "channel colours",  # not one of the four channels
        "channel facts, hypotheses",  # one operator per instruction
        "artifact a1 channel facts",  # two operators: an id is one token, never a silent empty match
        "artifact a1 a2",
        "scope /u/alice/ /u/bob/",
        "scope alice",  # not a canonical path
        "mention",
    ],
)
def test_an_unrecognised_instruction_is_an_error_and_never_a_silent_no_op(text: object) -> None:
    """Criterion 6: there is no default operator, so a drill-down that was not understood cannot return the result."""
    with pytest.raises(InstructionError) as raised:
        parse_instruction(text)
    expected = INSTRUCTION_REQUIRED if not isinstance(text, str) or not text.strip() else INSTRUCTION_UNRECOGNISED
    assert raised.value.code == expected
    assert str(raised.value) == expected, "the error text carries no instruction, id or scope"


def test_keeps_is_total_over_the_operators_and_matches_what_it_names() -> None:
    row = {"artifact_id": "a1", "scope_path": "/u/alice/p/repo/", "content": "I do not eat Fish", "channel": "facts"}
    assert Instruction("artifact", "a1").keeps(**row)
    assert not Instruction("artifact", "a2").keeps(**row)
    assert Instruction("channel", "facts").keeps(**row)
    assert not Instruction("channel", "hypotheses").keeps(**row)
    assert Instruction("mention", "fish").keeps(**row)  # case-insensitive
    assert not Instruction("mention", "meat").keeps(**row)
    assert Instruction("scope", "/u/alice/").keeps(**row)
    assert Instruction("scope", "/u/alice/p/repo/").keeps(**row)
    assert not Instruction("scope", "/u/alice/p/other/").keeps(**row)
    assert not Instruction("channel", "facts").keeps(**{**row, "channel": None}), "a refused row has no channel"


def test_an_instruction_refuses_an_unknown_operator_or_channel_even_when_constructed_directly() -> None:
    with pytest.raises(InstructionError):
        Instruction("explain", "why")  # type: ignore[arg-type]
    with pytest.raises(InstructionError):
        Instruction("channel", "everything")


# --- read-set containment ------------------------------------------------------------------------------


def test_covers_is_exactly_the_never_widen_rule() -> None:
    held = alice("/u/alice/")
    assert covers(held, alice("/u/alice/p/repo/")), "a subtree grant covers a descendant"
    assert covers(held, held)
    assert not covers(alice("/u/alice/p/repo/"), alice("/u/alice/")), "a descendant never covers its parent"
    assert not covers(read_set(), alice("/u/alice/")), "an empty set covers nothing"
    assert covers(alice("/u/alice/"), read_set()), "nothing needed is always covered"


def test_covers_respects_visibility_and_selector_mode() -> None:
    private = read_set(ReadEntry("/u/alice/", frozenset({"private"})))
    all_visibilities = read_set(ReadEntry("/u/alice/", frozenset({"private", "project", "shared"})))
    assert covers(all_visibilities, private)
    assert not covers(private, all_visibilities), "a narrower credential must not be treated as covering"
    exact = read_set(ReadEntry("/u/alice/p/repo/", frozenset({"project"}), "exact"))
    subtree = read_set(ReadEntry("/u/alice/p/repo/", frozenset({"project"}), "subtree"))
    assert covers(subtree, exact)
    assert not covers(exact, subtree), "an exact selector never covers a subtree need"


# --- the narrowed plan ---------------------------------------------------------------------------------


def test_refine_plan_cuts_expansion_and_cannot_change_the_question() -> None:
    prior = RuleBasedPlanner().plan(INTENT, [])
    assert prior.expansion == ASSOCIATION_EXPANSION and prior.hop_budget == DEFAULT_HOP_BUDGET
    refined = refine_plan(prior, candidates=3)
    assert refined.expansion == "none" and refined.hop_budget == 0, "a drill-down must never reach a new neighbour"
    assert refined.rows == 3, "the candidate set is the row bound, not the request's limit"
    unchanged = (
        prior.mode,
        prior.route,
        prior.index,
        prior.filters,
        prior.scopes,
        prior.target_time,
        prior.belief_time,
        prior.window_start,
        prior.window_end,
        prior.scope,
        prior.stages,
    )
    assert (
        refined.mode,
        refined.route,
        refined.index,
        refined.filters,
        refined.scopes,
        refined.target_time,
        refined.belief_time,
        refined.window_start,
        refined.window_end,
        refined.scope,
        refined.stages,
    ) == unchanged


@pytest.mark.parametrize("candidates", [0, -1, True, 1.5])
def test_refine_plan_refuses_a_candidate_bound_it_could_not_execute(candidates: object) -> None:
    with pytest.raises(PlanningError) as raised:
        refine_plan(RuleBasedPlanner().plan(INTENT, []), candidates=candidates)  # type: ignore[arg-type]
    assert raised.value.code == PLAN_INTENT_REFUSED


def test_a_result_handle_is_a_capability_and_not_content() -> None:
    """Two results with equal content stay equal, whatever handles they were issued."""
    plain = QueryResult()
    handled = QueryResult(result_id="res_" + "0" * 32)
    assert plain == handled
    assert plain.result_id is None and handled.result_id == "res_" + "0" * 32
