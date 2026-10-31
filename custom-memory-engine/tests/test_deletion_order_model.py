"""Executable reference model of the deletion-ordering argument (MA-014, ``PLAN/V2/DELETION_ORDER.md``).

This models the *rule*, not the engine: a derivation graph, a set of deleted seeds, and the fate each
dependent receives. It is exhaustive over every derivation DAG of up to five nodes, so the argument in
the document is checked rather than only stated. ``E-037`` must implement the same rule against the
database and supersede the bounded-depth behaviour that ``test_deletion_closure_stops_at_depth_three``
currently asserts.

Fates, for a node ``x`` with source set ``S(x)``:

* ``deleted``    explicitly deleted, or every source is gone;
* ``alive``      a grounded node nothing happened to, or a derived node whose sources all survive;
* ``rederived``  some sources are gone but at least ``threshold`` survive: re-derived on the survivors;
* ``retired``    some sources are gone and fewer than ``threshold`` survive.

The invariant (``I13`` in ``ARCHITECTURE_V2.md``): after the transaction no surviving node (``alive`` or
``rederived``) keeps a provenance edge to a ``deleted`` or ``retired`` node.
"""

from collections.abc import Iterator
from itertools import combinations, product

import pytest

from custom_memory_engine.deletion.closure import MAX_LINEAGE_DEPTH, lineage_closure

type Graph = tuple[tuple[int, ...], ...]  # graph[j] = sources of node j; every source index is < j

SURVIVES = ("alive", "rederived")


def all_graphs(size: int) -> Iterator[Graph]:
    """Every DAG on ``size`` nodes in topological numbering (node j may only derive from nodes < j)."""
    choices = [[subset for r in range(j + 1) for subset in combinations(range(j), r)] for j in range(size)]
    yield from product(*choices)


def classify(
    graph: Graph, seeds: frozenset[int], threshold: int, order: list[int]
) -> tuple[dict[int, str], dict[int, tuple[int, ...]]]:
    """Visit nodes in ``order`` and return each node's fate and the sources it was judged to keep.

    A source not yet visited is assumed to survive unless it is a seed. In sources-first order every
    source has been visited already, so its fate is final; in any other order the assumption can be wrong.
    """
    fate: dict[int, str] = {}
    kept: dict[int, tuple[int, ...]] = {}
    for node in order:
        sources = graph[node]
        if node in seeds:
            fate[node], kept[node] = "deleted", ()
        elif not sources:
            fate[node], kept[node] = "alive", ()
        else:
            judged = tuple(s for s in sources if (fate[s] in SURVIVES if s in fate else s not in seeds))
            kept[node] = judged
            if not judged:
                fate[node] = "deleted"
            elif len(judged) == len(sources):
                fate[node] = "alive"
            elif len(judged) >= threshold:
                fate[node] = "rederived"
            else:
                fate[node] = "retired"
    return fate, kept


def count_violations(size: int, order: list[int]) -> tuple[int, int]:
    """Return (cases, survivors left leaning on a deleted or retired node) over all small cases."""
    cases = bad = 0
    for graph in all_graphs(size):
        for seed_mask in range(1, 2**size):
            seeds = frozenset(i for i in range(size) if seed_mask >> i & 1)
            for threshold in (1, 2):
                fate, kept = classify(graph, seeds, threshold, order)
                cases += 1
                bad += sum(
                    1
                    for node, sources in kept.items()
                    if fate[node] in SURVIVES and any(fate[s] in ("deleted", "retired") for s in sources)
                )
    return cases, bad


@pytest.mark.parametrize("size", [3, 4, 5])
def test_sources_before_dependents_never_leaves_a_survivor_leaning_on_a_dead_source(size: int) -> None:
    cases, bad = count_violations(size, list(range(size)))
    assert cases > 0
    assert bad == 0


@pytest.mark.parametrize("size", [3, 4, 5])
def test_classifying_dependents_before_their_sources_does_leave_such_survivors(size: int) -> None:
    """Non-vacuity: classifying the most-derived nodes first is demonstrably wrong."""
    _, bad = count_violations(size, list(reversed(range(size))))
    assert bad > 0


def test_the_smallest_counterexample_for_dependents_first_classification() -> None:
    # 0 -> 1 -> 2 and 0 -> 2; delete 0. Node 1 loses its only source and is deleted, so node 2 must
    # not keep an edge to it. Visiting node 2 before node 1 lets node 2 judge node 1 still alive.
    graph: Graph = ((), (0,), (0, 1))
    seeds = frozenset({0})
    sources_first, _ = classify(graph, seeds, 1, [0, 1, 2])
    dependents_first, kept = classify(graph, seeds, 1, [2, 1, 0])
    assert sources_first == {0: "deleted", 1: "deleted", 2: "deleted"}
    assert dependents_first[2] == "rederived"
    assert kept[2] == (1,), "node 2 stayed attached to node 1, which is then deleted"
    assert dependents_first[1] == "deleted"


def test_the_v1_depth_bound_does_not_reach_dependents_beyond_it() -> None:
    """The real v1 closure function: a chain longer than the bound is only partly reached."""
    chain = MAX_LINEAGE_DEPTH + 4
    edges = tuple((f"v{i}", f"v{i - 1}") for i in range(1, chain))
    bounded = lineage_closure(("v0",), edges)
    unbounded = lineage_closure(("v0",), edges, max_depth=chain)
    assert len(bounded) == MAX_LINEAGE_DEPTH
    assert len(unbounded) == chain - 1
    assert set(bounded) < set(unbounded), "the bound silently leaves deeper dependents out of the closure"
