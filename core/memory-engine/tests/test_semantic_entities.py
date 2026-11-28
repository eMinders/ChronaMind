"""``semantic.entities``: a cascade that proposes, and an ambiguity that is reported (W3.5, ``V2-D90``).

The database half (`resolve_mention`'s stages) is exercised in
`tests/integration/test_entity_resolution_db.py`. What is tested here is the part that decides *when* a merge may
happen at all -- the label normalization and the ambiguity rule -- because that is where a false merge becomes
permanent: the typed foreign key that makes an entity subject sound is the same key that would carry a wrong
attachment into every future read.
"""

import pytest

from custom_memory_engine.semantic.entities import (
    EntityCandidate,
    ResolutionError,
    ResolutionOutcome,
    normalize_label,
)

# ----------------------------------------------------------------------------------------------- normalization


@pytest.mark.parametrize(
    "label",
    ["Ahmed", "ahmed", "  AHMED  ", "Ahmed's", "Ahmed’s", "“Ahmed”", "أحمد،", "(Ahmed)"],
)
def test_the_ordinary_spellings_of_one_label_normalize_together(label: str) -> None:
    assert normalize_label(label) == normalize_label("Ahmed") or normalize_label(label) == "أحمد"


def test_a_possessive_is_dropped_because_it_changes_no_identity() -> None:
    assert normalize_label("Sarah's") == "sarah"


def test_case_is_folded_because_a_label_is_not_prose() -> None:
    assert normalize_label("NeoVim") == "neovim"


def test_a_multi_word_label_keeps_its_interior_spacing() -> None:
    assert normalize_label("  Sara   Ahmed ") == "sara ahmed"


def test_diacritics_are_kept_because_a_false_merge_is_invisible() -> None:
    """`أحمد` and `احمد` are different spellings; deciding they are one person belongs to adjudication, not to a
    normalizer where the merge would be unobservable."""
    assert normalize_label("أحمد") != normalize_label("احمد")
    assert normalize_label("José") != normalize_label("Jose")


def test_a_blank_label_is_refused_rather_than_normalized_to_nothing() -> None:
    with pytest.raises(ResolutionError) as refused:
        normalize_label("   ")
    assert refused.value.code == "entity_label_required"


# -------------------------------------------------------------------------------------------------- ambiguity


def test_an_outcome_reports_ambiguity_with_both_ids_and_no_winner() -> None:
    outcome = ResolutionOutcome("my boss", "unresolved", None, ("e1", "e2"))
    assert outcome.resolved is False
    assert outcome.ambiguous == ("e1", "e2")


def test_a_candidate_carries_the_stage_that_found_it() -> None:
    """`stage` is what tells an operator whether a merge was deterministic or adjudicated."""
    candidate = EntityCandidate("e1", "Ahmed", "trigram", 0.91)
    assert (candidate.stage, candidate.score) == ("trigram", 0.91)


def test_the_ambiguity_margin_and_the_floors_are_ordered_for_the_cascade() -> None:
    """A trigram floor that is not well below 1 would starve the cascade; a vector floor above it would never fire."""
    from custom_memory_engine.semantic.entities import AMBIGUITY_MARGIN, TRIGRAM_FLOOR, VECTOR_FLOOR

    assert 0 < AMBIGUITY_MARGIN < 0.1
    assert 0.3 <= TRIGRAM_FLOOR <= 0.7
    assert 0.4 <= VECTOR_FLOOR <= 0.9
