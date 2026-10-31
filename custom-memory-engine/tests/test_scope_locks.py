"""V2-D51: the global lock order for cross-scope closures cannot deadlock; discovery order can.

The lock model is exhaustive: every interleaving of 2 or 3 transactions, each acquiring its lock
list one key at a time and holding every lock until commit (advisory *transaction* locks). A
state where every unfinished transaction waits for a lock another holds is a deadlock.
"""

from functools import cache
from itertools import combinations

import pytest

from custom_memory_engine.scope.locks import lock_order, must_retry, serialisation_key
from custom_memory_engine.scope.paths import ScopeError

type Plan = tuple[str, ...]


def can_deadlock(plans: tuple[Plan, ...]) -> bool:
    """Explore every interleaving; return True if some reachable state is a deadlock."""

    @cache
    def explore(progress: tuple[int, ...]) -> bool:
        held = {  # a committed transaction (progress == len) has released its locks
            key: t for t, plan in enumerate(plans) if progress[t] < len(plan) for key in plan[: progress[t]]
        }
        unfinished = [t for t, plan in enumerate(plans) if progress[t] < len(plan)]
        if not unfinished:
            return False
        movable = [t for t in unfinished if held.get(plans[t][progress[t]], t) == t]
        if not movable:
            return True  # every unfinished transaction waits on another: a cycle of waits
        for t in movable:
            step = list(progress)
            step[t] += 1
            if explore(tuple(step)):
                return True
        return False

    return explore(tuple(0 for _ in plans))


SCOPES = ("/u/alice/p/a/", "/u/bob/p/b/", "/u/carol/", "/u/bob/")


def discovery_order(own: str, closure: tuple[str, ...]) -> Plan:
    """The naive order: own namespace first, then the closure as discovered."""
    keys = [serialisation_key(own)] + [serialisation_key(s) for s in closure]
    return tuple(dict.fromkeys(keys))


def test_opposite_closures_in_discovery_order_can_deadlock() -> None:
    """DELETION_ORDER.md 6.1: T1 deletes in A reaching B, T2 deletes in B reaching A."""
    t1 = discovery_order("/u/alice/p/a/", ("/u/bob/p/b/",))
    t2 = discovery_order("/u/bob/p/b/", ("/u/alice/p/a/",))
    assert can_deadlock((t1, t2))


def test_the_same_closures_in_global_order_cannot_deadlock() -> None:
    t1 = lock_order(("/u/alice/p/a/", "/u/bob/p/b/"))
    t2 = lock_order(("/u/bob/p/b/", "/u/alice/p/a/"))
    assert t1 == t2 == ("/u/alice/", "/u/bob/")
    assert not can_deadlock((t1, t2))


@pytest.mark.parametrize("n", [2, 3])
def test_no_combination_of_closures_in_global_order_can_deadlock(n: int) -> None:
    subsets = [c for r in (1, 2, 3) for c in combinations(SCOPES, r)]
    checked = 0
    for closures in combinations(subsets, n):
        plans = tuple(lock_order(c) for c in closures)
        assert not can_deadlock(plans), closures
        checked += 1
    assert checked > 50


def test_discovery_order_deadlocks_for_some_three_way_cycle() -> None:
    owners = ("/u/alice/", "/u/bob/", "/u/carol/")
    plans = tuple(discovery_order(owners[i], (owners[(i + 1) % 3],)) for i in range(3))
    assert can_deadlock(plans)
    assert not can_deadlock(tuple(lock_order((owners[i], owners[(i + 1) % 3])) for i in range(3)))


def test_key_is_the_owner_namespace_and_v1_equivalent() -> None:
    assert serialisation_key("/u/alice/") == "/u/alice/"
    assert serialisation_key("/u/alice/projects/x/") == "/u/alice/"
    assert lock_order(["/u/a_b/p/", "/u/a_b/", "/u/aXb/"]) == ("/u/aXb/", "/u/a_b/")  # byte order, no collation


def test_order_is_byte_order_for_non_ascii_user_ids() -> None:
    keys = lock_order(["/u/é/", "/u/z/", "/u/Z/"])
    assert keys == tuple(sorted(keys, key=str.encode)) == ("/u/Z/", "/u/z/", "/u/é/")


@pytest.mark.parametrize("bad", ["/", "/x/alice/", "u/alice/", "/u/alice"])
def test_malformed_or_non_namespace_scopes_fail_closed(bad: str) -> None:
    with pytest.raises(ScopeError):
        lock_order([bad])


def test_empty_lock_set_is_an_error() -> None:
    with pytest.raises(ScopeError):
        lock_order([])


def test_closure_growth_under_the_locks_requires_a_retry() -> None:
    held = lock_order(["/u/alice/p/"])
    assert not must_retry(held, ["/u/alice/p/", "/u/alice/q/"])
    assert must_retry(held, ["/u/alice/p/", "/u/bob/shared/"])
