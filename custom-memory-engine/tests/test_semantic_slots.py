"""``semantic.slots`` and ``semantic.temporal``: one normalization, and bounds that are legal (W3.5).

Both modules exist to make a value the engine will *key on* or *compare* arrive in exactly one form, so that the
database constraint and the handler cannot disagree. The tests are therefore written as pairs wherever possible: what
normalizes to the same key (and therefore supersedes), what does not (and therefore does not), and which refusal a
malformed value earns.
"""

from datetime import UTC, datetime

import pytest

from custom_memory_engine.semantic.slots import SlotError, normalize_slot, slots_equivalent

T0 = datetime(2026, 3, 10, tzinfo=UTC)

# ------------------------------------------------------------------------------------------------------- slots


@pytest.mark.parametrize(
    "spelling",
    ["residence", "Residence", "  residence ", "residence.", "RESIDENCE", "residence\u00a0"],
)
def test_every_ordinary_spelling_of_one_slot_normalizes_to_one_key(spelling: str) -> None:
    """Five honest callers spelling one slot five ways must not create five predicates."""
    assert normalize_slot(spelling) == "residence"


def test_interior_separators_are_folded_but_not_deleted() -> None:
    assert normalize_slot("meeting time constraint") == "meeting_time_constraint"
    assert normalize_slot("meeting-time-constraint") == "meeting_time_constraint"
    assert normalize_slot("meeting.time.constraint") == "meeting_time_constraint"
    assert normalize_slot("meeting_time_constraint") == "meeting_time_constraint"


def test_a_full_width_spelling_is_the_same_slot() -> None:
    """NFKC: what an IME or a paste produces is not a second fact about the user."""
    assert normalize_slot("ｒｅｓｉｄｅｎｃｅ") == "residence"


def test_two_different_languages_are_two_different_keys() -> None:
    """Deciding these mean the same thing is a retrieval judgement, not an identity merge: merging keys would let one
    belief silently supersede an independent one."""
    assert normalize_slot("إقامة") != normalize_slot("residence")


def test_diacritics_are_kept_because_a_false_merge_is_invisible() -> None:
    assert normalize_slot("مُقيم") != normalize_slot("مقيم")


def test_no_slot_is_not_an_error() -> None:
    assert normalize_slot(None) is None


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "slot_required"),
        ("   ", "slot_required"),
        ("...", "slot_unusable"),
        ("a", "slot_too_short"),
        ("x" * 121, "slot_too_long"),
        ("bad\u0007slot", "slot_contains_a_control_character"),
        (7, "slot_must_be_a_string"),
    ],
)
def test_a_slot_the_schema_would_refuse_earns_a_typed_code_here_first(value: object, code: str) -> None:
    with pytest.raises(SlotError) as refused:
        normalize_slot(value)
    assert refused.value.code == code


def test_equivalence_is_answerable_for_absent_slots() -> None:
    assert slots_equivalent(None, None) is True
    assert slots_equivalent(None, "residence") is False
    assert slots_equivalent("Residence", "residence.") is True
