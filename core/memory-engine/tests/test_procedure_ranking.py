"""The procedure ranking rule, tested directly (E-027 §7.4, §10 rows 5, 8; ``V2-D75``).

These are property tests over the pure function, not retrieval tests: they pin the formula's monotonicity, the
handling of an unseen procedure, and the totality of the order, so a later change to the ranking cannot silently
reintroduce the failure mode the Laplace form exists to avoid. The end-to-end "feedback changes the order" claim is
``tests/integration/test_record_outcome_db.py``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from custom_memory_engine.engine.procedure_ranking import OutcomeStats, order_procedures, rank_key

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def stats(successes: int, failures: int, *, used: datetime | None = None, cost: float | None = None) -> OutcomeStats:
    return OutcomeStats("p", successes, failures, used, cost)


# --- the formula ----------------------------------------------------------------------------------------


def test_an_unseen_procedure_is_exactly_between_a_successful_and_a_failed_one() -> None:
    """The failure mode of a raw ratio: 0/0 must not outrank a winner, nor lose to a loser."""
    unseen = stats(0, 0).smoothed_success_rate
    assert stats(3, 0).smoothed_success_rate > unseen > stats(0, 3).smoothed_success_rate
    assert unseen == 0.5


def test_a_success_never_lowers_the_rate_and_a_failure_never_raises_it() -> None:
    """Monotonicity, for every combination up to a small bound rather than one example."""
    for successes in range(6):
        for failures in range(6):
            base = stats(successes, failures)
            assert stats(successes + 1, failures).smoothed_success_rate > base.smoothed_success_rate
            assert stats(successes, failures + 1).smoothed_success_rate < base.smoothed_success_rate


def test_more_successes_at_an_equal_sample_size_always_ranks_higher() -> None:
    """The primary ordering: with the same number of outcomes, more successes wins outright."""
    order = order_procedures(
        ["p0", "p1", "p2", "p3"],
        {f"p{i}": OutcomeStats(f"p{i}", i, 3 - i if i <= 3 else 0, None, None) for i in range(4)},
    )
    assert order == ("p3", "p2", "p1", "p0")


def test_the_observed_rate_is_reported_but_not_what_ranks() -> None:
    """``success_rate`` is 0.0 before any outcome, yet it does not rank last: the two differ by design."""
    unseen = stats(0, 0)
    assert unseen.success_rate == 0.0
    assert unseen.smoothed_success_rate == 0.5
    assert order_procedures(["unseen", "failed"], {"failed": OutcomeStats("failed", 0, 1, None, None)}) == (
        "unseen",
        "failed",
    )


def test_uses_counts_both_outcomes() -> None:
    assert stats(2, 3).uses == 5
    assert stats(0, 0).uses == 0


# --- tie-breaks ---------------------------------------------------------------------------------------


def test_an_equal_rate_is_broken_by_most_recent_use() -> None:
    order = order_procedures(
        ["old", "new", "middle"],
        {
            "old": OutcomeStats("old", 1, 1, T0, None),
            "new": OutcomeStats("new", 2, 2, T0 + timedelta(hours=2), None),
            "middle": OutcomeStats("middle", 1, 1, T0 + timedelta(hours=1), None),
        },
    )
    assert order == ("new", "middle", "old")


def test_a_never_used_procedure_sorts_after_a_used_one_at_the_same_rate() -> None:
    """0/0 and 1/1 both smooth to 3/4; the procedure that was actually used comes first."""
    order = order_procedures(
        ["never", "used"],
        {"used": OutcomeStats("used", 1, 1, T0, None)},
    )
    assert order == ("used", "never")


def test_the_order_is_total_so_id_breaks_the_last_tie() -> None:
    """Idents are unique, so the order is total: no reliance on input order or on DB row order."""
    ids = ["c", "a", "b"]
    assert order_procedures(ids, {}) == ("a", "b", "c")
    assert order_procedures(list(reversed(ids)), {}) == ("a", "b", "c")


def test_order_is_independent_of_input_order_and_stable_across_runs() -> None:
    """Determinism: the same rows in any order produce the same result, every time."""
    mapping = {
        "x": OutcomeStats("x", 1, 0, T0, None),
        "y": OutcomeStats("y", 0, 1, T0 + timedelta(minutes=1), None),
        "z": OutcomeStats("z", 1, 0, T0 + timedelta(minutes=2), None),
    }
    expected = order_procedures(["x", "y", "z"], mapping)
    for permutation in (["z", "y", "x"], ["y", "x", "z"], ["z", "x", "y"]):
        assert order_procedures(permutation, mapping) == expected
    assert expected == ("z", "x", "y")  # x and z tie on rate; z was used later


# --- the key itself -----------------------------------------------------------------------------------


def test_a_missing_stats_row_and_an_empty_ledger_produce_the_same_key() -> None:
    """No record and 0/0 carry the same information, so they must not be distinguishable by rank."""
    assert rank_key(None, "p") == rank_key(OutcomeStats("p", 0, 0, None, None), "p")


def test_cost_does_not_change_the_rank() -> None:
    """Recorded, not ranked: the same counters with wildly different costs order identically."""
    cheap = OutcomeStats("p", 2, 1, T0, 0.001)
    dear = OutcomeStats("p", 2, 1, T0, 10_000.0)
    assert rank_key(cheap, "p") == rank_key(dear, "p")
    assert order_procedures(["a", "b"], {"a": cheap, "b": dear}) == ("a", "b")


def test_an_empty_input_orders_to_nothing() -> None:
    assert order_procedures([], {}) == ()


def test_a_single_procedure_orders_to_itself() -> None:
    assert order_procedures(["only"], {}) == ("only",)


@pytest.mark.parametrize("successes,failures", [(0, 0), (1, 0), (0, 1), (5, 5), (100, 0)])
def test_the_smoothed_rate_stays_within_the_unit_interval(successes: int, failures: int) -> None:
    """The rate is a probability: no combination of counts may push it outside ``(0, 1)``."""
    assert 0.0 < stats(successes, failures).smoothed_success_rate < 1.0
