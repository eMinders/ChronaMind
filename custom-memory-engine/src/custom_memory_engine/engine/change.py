"""The ``change`` query mode: a diff of two temporal views (``V2-D81``; ``API_V2.md`` §4.2).

``TECHNICAL_SPECIFICATION.md`` §3.5 groups change queries with the *window* form of the historical predicate, and §7.3
labels a change-mode item ``previous`` or ``current``. ``V2-D81`` makes that precise:

* **The two views.** The *previous* view is what the engine believes now (ρ) was in effect at ``window_start``; the
  *current* view is what it believes now was in effect at ``window_end``. Each is ``historical_as_of`` under ρ, so
  both are answer-context reads: ``status='active'``, read set applied in SQL. Nothing retired, archived, held or
  rejected can enter either view, which is why ``change`` is not a back door to ``audit_as_of`` (``V2-D73``).
* **The key.** A row is matched on the identity a belief keeps across versions. For a single-valued predicate that is
  ``(subject_key, predicate_key)``: supersession (``R1``/``R2``) closes one version and opens another under exactly
  that pair, while the API mints a fresh ``memory_key`` per assertion. For a multi-valued predicate it is
  ``memory_key``, which ``R7`` keeps across the close-then-insert that ends one value.
* **The outcomes.** Per key: ``added`` (only in *current*), ``removed`` (only in *previous*), ``changed`` (in both,
  but the versions differ). A key whose versions are identical in both views is unchanged and omitted.

This module is pure: it sees two tuples of rows and returns a tuple of :class:`Change`. It never reads, so it cannot
widen what a caller sees -- the views it diffs were already filtered by the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from custom_memory_engine.engine.predicates import ScopedRow

type ChangeOutcome = Literal["added", "removed", "changed"]

#: The order outcomes are reported in when two keys sort equal; never relied on for meaning.
OUTCOMES: tuple[ChangeOutcome, ...] = ("added", "removed", "changed")


@dataclass(frozen=True, slots=True)
class Change:
    """One key that differs between the two views. Ids only: content is in the channels, routed like any other row.

    ``previous`` and ``current`` are the version ids of that key in each view, sorted. ``added`` has no
    ``previous``; ``removed`` has no ``current``; ``changed`` has both and they differ.
    """

    outcome: ChangeOutcome
    previous: tuple[str, ...]
    current: tuple[str, ...]


def change_key(row: ScopedRow) -> tuple[str, ...]:
    """The identity a belief keeps across its versions (``V2-D81``)."""
    if row.single_valued:
        return ("single_valued", row.subject_key, row.predicate_key)
    return ("memory_key", row.memory_key)


def diff_views(previous: Sequence[ScopedRow], current: Sequence[ScopedRow]) -> tuple[Change, ...]:
    """The keys that differ between ``previous`` and ``current``, in a deterministic order.

    Order: by the smallest version id the change names. A version id is unique, so no two changes tie and the order
    never depends on dictionary iteration.
    """
    before: dict[tuple[str, ...], set[str]] = {}
    after: dict[tuple[str, ...], set[str]] = {}
    for row in previous:
        before.setdefault(change_key(row), set()).add(row.id)
    for row in current:
        after.setdefault(change_key(row), set()).add(row.id)
    changes: list[Change] = []
    for key in before.keys() | after.keys():
        old, new = before.get(key, set()), after.get(key, set())
        if old == new:
            continue
        outcome: ChangeOutcome = "added" if not old else "removed" if not new else "changed"
        changes.append(Change(outcome, tuple(sorted(old)), tuple(sorted(new))))
    return tuple(sorted(changes, key=lambda change: min((*change.previous, *change.current))))


__all__ = ["OUTCOMES", "Change", "ChangeOutcome", "change_key", "diff_views"]
