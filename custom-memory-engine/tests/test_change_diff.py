"""``V2-D81``: the pure ``change`` diff -- its key and its three outcomes, with no database.

``engine/change.py`` sees two tuples of rows that the database has already filtered, so everything it decides is
decided here: which identity a row is matched on, which outcome each key gets, and in what order they are reported.
The database-backed half (the two views, the read set, the refusals) is ``tests/integration/test_change_mode_db.py``.
"""

from custom_memory_engine.engine.change import OUTCOMES, Change, change_key, diff_views
from custom_memory_engine.engine.predicates import ScopedRow


def _single(version_id: str, content: str, *, memory_key: str | None = None) -> ScopedRow:
    """One version of the single-valued ``(alice, residence)`` belief; each version has its own ``memory_key``."""
    return ScopedRow(
        version_id,
        "claim",
        "asserted",
        content,
        "/u/alice/",
        "private",
        memory_key=memory_key or f"ep_{version_id}",
        subject_key="alice",
        predicate_key="residence",
        single_valued=True,
    )


def _multi(version_id: str, memory_key: str, content: str) -> ScopedRow:
    """One version of a multi-valued belief: ``R7`` keeps its ``memory_key`` across the close-then-insert."""
    return ScopedRow(
        version_id,
        "claim",
        "asserted",
        content,
        "/u/alice/",
        "private",
        memory_key=memory_key,
        subject_key="alice",
        predicate_key="hobby",
        single_valued=False,
    )


def test_a_single_valued_row_is_keyed_on_subject_and_predicate_not_memory_key() -> None:
    """Two versions of one belief carry different ``memory_key`` values but share a key (``V2-D81``)."""
    before, after = _single("v1", "Cairo"), _single("v2", "Berlin")
    assert before.memory_key != after.memory_key
    assert change_key(before) == change_key(after) == ("single_valued", "alice", "residence")


def test_a_multi_valued_row_is_keyed_on_memory_key() -> None:
    """Two values of one multi-valued predicate are two beliefs, so they must not share a key."""
    chess, go = _multi("v1", "mk-chess", "chess"), _multi("v2", "mk-go", "go")
    assert change_key(chess) == ("memory_key", "mk-chess")
    assert change_key(chess) != change_key(go)


def test_supersession_is_one_change_not_an_add_and_a_remove() -> None:
    assert diff_views([_single("v1", "Cairo")], [_single("v2", "Berlin")]) == (Change("changed", ("v1",), ("v2",)),)


def test_a_key_only_in_the_current_view_is_added() -> None:
    assert diff_views([], [_multi("v1", "mk-chess", "chess")]) == (Change("added", (), ("v1",)),)


def test_a_key_only_in_the_previous_view_is_removed() -> None:
    assert diff_views([_multi("v1", "mk-chess", "chess")], []) == (Change("removed", ("v1",), ()),)


def test_an_ended_multi_valued_value_is_changed_under_its_own_key() -> None:
    """``R7`` re-issues the ended value under the same ``memory_key``: one change, not an add and a remove."""
    open_ended, closed = _multi("v1", "mk-chess", "chess"), _multi("v1-ended", "mk-chess", "chess")
    assert diff_views([open_ended], [closed]) == (Change("changed", ("v1",), ("v1-ended",)),)


def test_an_unchanged_key_is_not_reported() -> None:
    """The diff names what moved; a key with the same versions in both views is omitted, not reported unchanged."""
    steady = _multi("v1", "mk-chess", "chess")
    assert diff_views([steady], [steady]) == ()
    assert diff_views([], []) == ()


def test_every_outcome_together_in_a_deterministic_order() -> None:
    """One of each outcome, reported once and ordered by the smallest version id named, whatever the input order."""
    previous = [_single("b-old", "Cairo"), _multi("a-gone", "mk-tabs", "tabs"), _multi("z-steady", "mk-z", "z")]
    current = [_multi("c-new", "mk-spaces", "spaces"), _single("d-new", "Berlin"), _multi("z-steady", "mk-z", "z")]
    expected = (
        Change("removed", ("a-gone",), ()),
        Change("changed", ("b-old",), ("d-new",)),
        Change("added", (), ("c-new",)),
    )
    assert diff_views(previous, current) == expected
    assert diff_views(list(reversed(previous)), list(reversed(current))) == expected
    assert {change.outcome for change in expected} == set(OUTCOMES)
