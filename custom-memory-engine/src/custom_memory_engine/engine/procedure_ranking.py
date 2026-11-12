"""Procedure outcome ranking: the feedback term ``record_outcome`` feeds (E-027, ``V2-D75``).

**The rule.** A procedure is ordered by how well it has worked, then by how recently it was used, then by id:

    rank(procedure) = (  -success_rate_smoothed,   # more successful first
                         -last_used_at,            # then most recently used (never-used last)
                         procedure_id )            # ids are unique, so the order is total

    success_rate_smoothed = (successes + 1) / (successes + failures + 2)

**Why Laplace (``+1``/``+2``).** A raw ratio ``successes / (successes + failures)`` is undefined when a procedure has
never been used, and the two obvious ways to handle that are both wrong: scoring an unseen procedure ``0`` ranks it
below one that has demonstrably failed, and scoring it ``1`` ranks it above one that has demonstrably worked. The
Laplace form places an unseen procedure at exactly ``0.5`` -- strictly above any procedure with a failure and strictly
below any with a success -- and it is monotone: recording a success never lowers the rate, recording a failure never
raises it. :func:`rank_key` rests on those properties, and ``tests/test_procedure_ranking.py`` pins each one directly
rather than only through retrieval.

**What this module may not do.** It is an *ordering within one channel*, never a membership decision. ``CHANNELS.md``
makes membership a pure function of ``(kind, lifecycle, verification, stale, open_contradiction)``
(:func:`custom_memory_engine.engine.channels.channel_of`), and ``I1'`` forbids an inferred artifact rendering as a
fact. Reordering a bucket cannot change which bucket an artifact is in, so statistics cannot promote a ``contested`` or
``retired`` procedure: one never reached the ``procedures`` channel to be ordered. The temporal predicate is likewise a
filter and never a score term (``ARCHITECTURE_V2.md`` 9.3) -- this module only ever sees rows that already survived it.

**Cost is recorded, not ranked.** ``mean_cost`` is stored and returned because ``ARCHITECTURE_V2.md`` section 6 asks
for it, but it does not order procedures. Cost against success rate has no agreed trade-off, and inventing a weight
would make the order depend on a number nobody chose. ``E-033`` owns budgets and may consume it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class OutcomeStats:
    """The recorded outcome counters of one procedure: counts, a clock and a cost, and no content.

    Returned by ``record_outcome`` (``API_V2.md`` 4.5) and read back by the ranking term. ``mean_cost`` is ``None``
    when no recorded outcome carried a cost, which is deliberately distinct from a recorded cost of ``0.0``.
    """

    procedure_id: str
    successes: int
    failures: int
    last_used_at: datetime | None
    mean_cost: float | None

    @property
    def uses(self) -> int:
        """How many outcomes have been recorded: the sample size behind :attr:`success_rate`."""
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        """The observed ratio, or ``0.0`` before any outcome. For display; ranking uses the smoothed rate."""
        return 0.0 if self.uses == 0 else self.successes / self.uses

    @property
    def smoothed_success_rate(self) -> float:
        """``(successes + 1) / (uses + 2)``: exactly ``0.5`` for a procedure with no recorded outcome."""
        return (self.successes + 1) / (self.uses + 2)


def rank_key(stats: OutcomeStats | None, procedure_id: str) -> tuple[float, float, str]:
    """The sort key for one procedure: success rate descending, recency descending, id ascending.

    A procedure with no record yet has no stats row and is keyed exactly as an empty ``OutcomeStats`` (0 successes,
    0 failures): both carry the same information, and giving them different keys would need an arbitrary tie-break
    between two states that are indistinguishable to a caller.

    The key is built for ascending ``sorted()``: the rate is negated so the best procedure comes first, recency is
    negated so the newest comes first, and the id breaks any remaining tie low-to-high. Ids are unique, so the order
    is **total** -- two runs over the same rows can never disagree, and no DB row order is relied on.
    """
    effective = stats if stats is not None else OutcomeStats(procedure_id, 0, 0, None, None)
    return (-effective.smoothed_success_rate, _recency_key(effective.last_used_at), procedure_id)


def _recency_key(last_used_at: datetime | None) -> float:
    """Negated POSIX timestamp, so a newer instant sorts first; a never-used procedure sorts after every used one."""
    if last_used_at is None:
        return float("inf")  # only comparable at an equal success rate, where "used at all" is the better signal
    return -last_used_at.timestamp()


def order_procedures(ids: Iterable[str], stats: Mapping[str, OutcomeStats]) -> tuple[str, ...]:
    """``ids`` in rank order. ``stats`` maps an id to its recorded counters; a missing id means no outcome yet."""
    return tuple(sorted(ids, key=lambda procedure_id: rank_key(stats.get(procedure_id), procedure_id)))


__all__ = ["OutcomeStats", "order_procedures", "rank_key"]
