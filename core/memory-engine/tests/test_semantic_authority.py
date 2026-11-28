"""``semantic.authority``: the ladder is derived, not declared, and it mirrors the schema (W3.5, ``V2-D87``).

Veracium's authority ladder is a guarantee only if the text it governs cannot set its own rank. Two things are
therefore tested here: the derivation (`source_type` + `evidence_role` → rank), and the fact that the Python twin and
migration 24's generated column agree — which `tests/integration/test_migration_m24.py` checks from the database side
with the same table of cases.

The supersession rule is tested as three cases and nothing else, because a fourth case would be a rank the ladder does
not know, and defaulting one is how a security rule becomes a suggestion.
"""

import pytest

from custom_memory_engine.semantic.authority import (
    ASSISTANT,
    LEVELS,
    NAMES,
    SYSTEM,
    THIRD_PARTY,
    USER,
    AuthorityError,
    may_supersede,
    quarantine,
    rank_for,
    strongest,
    version_authority,
)

#: The table migration 24's generated column must produce, and `test_migration_m24` asserts directly.
CASES: list[tuple[str, str | None, int]] = [
    ("turn", "user", USER),
    ("turn", "system", SYSTEM),
    ("turn", "assistant", ASSISTANT),
    ("tool", "tool", THIRD_PARTY),
    ("file", "file", THIRD_PARTY),
    ("event", "event", THIRD_PARTY),
    ("external", "external", THIRD_PARTY),
    # the source type wins: a claimed user role on third-party content proves nothing
    ("tool", "user", THIRD_PARTY),
    # an unrecognised role is untrusted, not trusted by default
    ("turn", "root", THIRD_PARTY),
    ("turn", None, THIRD_PARTY),
]


@pytest.mark.parametrize(("source_type", "role", "rank"), CASES)
def test_the_rank_is_derived_from_what_the_evidence_is(source_type: str, role: str | None, rank: int) -> None:
    assert rank_for(source_type, role) == rank


def test_an_empty_source_type_is_refused_rather_than_ranked() -> None:
    with pytest.raises(AuthorityError) as refused:
        rank_for("")
    assert refused.value.code == "source_type_required"


def test_a_claim_is_as_authoritative_as_its_weakest_support() -> None:
    """One user quote must not act as cover for nine web pages."""
    assert strongest([USER, USER, USER]) == USER
    assert strongest([USER, THIRD_PARTY]) == THIRD_PARTY
    assert strongest([]) == THIRD_PARTY


def test_a_version_takes_the_weakest_rank_of_its_evidence() -> None:
    assert version_authority([("turn", "user")]) == USER
    assert version_authority([("turn", "user"), ("external", None)]) == THIRD_PARTY
    assert version_authority([("turn", "assistant")]) == ASSISTANT


def test_the_four_levels_have_names_and_no_fifth_level() -> None:
    assert LEVELS == (USER, SYSTEM, ASSISTANT, THIRD_PARTY)
    assert set(NAMES) == set(LEVELS)
    assert NAMES[USER] == "user" and NAMES[THIRD_PARTY] == "third_party"


# ---------------------------------------------------------------------------------------------- supersession


@pytest.mark.parametrize(
    ("candidate", "existing", "allowed"),
    [
        # equal rank: a user correcting themselves is the commonest supersession there is
        (USER, USER, True),
        (ASSISTANT, ASSISTANT, True),
        (THIRD_PARTY, THIRD_PARTY, True),
        # a stronger source may always replace a weaker one
        (USER, ASSISTANT, True),
        (SYSTEM, THIRD_PARTY, True),
        (ASSISTANT, THIRD_PARTY, True),
        # a weaker source never replaces a stronger one
        (THIRD_PARTY, USER, False),
        (THIRD_PARTY, ASSISTANT, False),
        (ASSISTANT, USER, False),
        (SYSTEM, USER, False),
    ],
)
def test_the_ladder_admits_exactly_the_supersessions_it_should(candidate: int, existing: int, allowed: bool) -> None:
    decision = may_supersede(candidate, existing)  # type: ignore[arg-type]
    assert decision.allowed is allowed
    assert decision.refused is (not allowed)


def test_a_refused_supersession_is_named_quarantine_and_not_dropped() -> None:
    """The attempt is recorded, never silently discarded: the fact that a web page disagrees is itself information."""
    decision = may_supersede(THIRD_PARTY, USER)
    assert decision.reason_code == "authority_quarantine"
    assert decision.quarantined is True


def test_an_equal_rank_supersession_is_permitted_and_not_called_quarantine() -> None:
    decision = may_supersede(USER, USER)
    assert (decision.allowed, decision.reason_code, decision.quarantined) == (
        True,
        "authority_permits_supersession",
        False,
    )


def test_a_rank_the_ladder_does_not_know_is_refused_rather_than_defaulted() -> None:
    decision = may_supersede(9, USER)  # type: ignore[arg-type]
    assert decision.refused is True
    assert decision.reason_code == "authority_rank_unknown"


def test_quarantine_is_structural_so_it_cannot_be_paraphrased_around() -> None:
    """There is no phrase list to bypass: only the source decides."""
    assert quarantine("external") is True
    assert quarantine("tool") is True
    assert quarantine("file") is True
    assert quarantine("event") is True
    assert quarantine("turn") is False
