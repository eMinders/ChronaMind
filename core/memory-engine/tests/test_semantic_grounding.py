"""``semantic.grounding``: a quote must exist, its specifics must appear, and nothing else is folded (W3.5).

Grounding is the cheapest defence against the failure that matters most -- a fluent, plausible, fabricated memory that
is indistinguishable from a true one *after* storage. These tests pin what it accepts (an honest quote across a line
break, an Arabic quote, a claim with no specifics), what it refuses (a fabricated quote, a number from another
document, a one-character "quote"), and what it deliberately does **not** fold (case, diacritics, newlines beyond a
whitespace run).
"""

import pytest

from custom_memory_engine.semantic.contracts import CandidateBelief, ExtractedClaim
from custom_memory_engine.semantic.grounding import (
    GroundingError,
    ground_claim,
    ground_text,
    normalize,
    quote_grounded,
    specifics_in,
)


def candidate(content: str, quote: str, source: str, *, specifics: tuple[str, ...] = ()) -> CandidateBelief:
    return CandidateBelief(
        claim=ExtractedClaim(content=content, verbatim_quote=quote, specifics=specifics),
        source_text=source,
        source_ref="t1",
    )


# ----------------------------------------------------------------------------------------------- normalization


def test_normalization_folds_only_what_carries_no_meaning_for_a_match() -> None:
    assert normalize("  Hello\r\n\r\n  world\u00a0 ") == "Hello world"
    # NFC: the same composed string, so a quote taken from the stored (NFC) text matches
    assert normalize("e\u0301") == normalize("\u00e9")
    # case is content: a name's capitalisation is what the user wrote
    assert normalize("Ahmed") != normalize("ahmed")


def test_normalization_refuses_a_non_string_instead_of_coercing_it() -> None:
    with pytest.raises(GroundingError):
        normalize(1)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------------------- quote grounding


def test_an_honest_quote_across_a_transcript_line_break_is_grounded() -> None:
    source = "I moved to Berlin\nlast spring, actually."
    assert quote_grounded("moved to Berlin last spring", source)


def test_a_fabricated_quote_is_not_grounded() -> None:
    assert not quote_grounded("I moved to Munich", "I moved to Berlin last spring")


def test_an_empty_or_absent_quote_is_never_grounded() -> None:
    assert not quote_grounded("", "anything")
    assert not quote_grounded("x", "")


@pytest.mark.parametrize(
    ("content", "quote", "source", "ok"),
    [
        ("I live in Berlin", "live in Berlin", "I live in Berlin", True),
        # Arabic, verbatim
        ("أسكن في برلين", "أسكن في برلين", "انتقلت إلى برلين، أسكن في برلين الآن", True),
        # Arabic, one diacritic different: NOT folded, because deciding that is the resolver's job
        ("أسكن في برلين", "أسْكن في برلين", "أسكن في برلين", False),
        # CJK, verbatim substring
        ("住在柏林", "住在柏林", "我家住在柏林", True),
        # code-switched
        (
            "Approved the roadmap",
            "approved the ChronaMind roadmap",
            "My manager أحمد approved the ChronaMind roadmap",
            True,
        ),
    ],
)
def test_grounding_holds_across_scripts(content: str, quote: str, source: str, ok: bool) -> None:
    assert ground_claim(candidate(content, quote, source)).grounded is ok


# -------------------------------------------------------------------------------------------------- specifics


def test_a_number_the_source_does_not_contain_is_missing() -> None:
    assert specifics_in("meets Sara at 10am", "I met Sara yesterday") == ("10",)
    assert specifics_in("meets Sara at 10am", "I met Sara at 10am") == ()


def test_an_identifier_is_checked_as_well_as_a_number() -> None:
    assert specifics_in("pinned chronamind-core to v2.3.1", "we released chronamind-core") == ("v2.3.1",)


def test_a_claim_with_no_specifics_has_nothing_to_check() -> None:
    assert specifics_in("I prefer tea", "I prefer coffee") == ()


def test_a_non_ascii_digit_is_a_number() -> None:
    """Arabic-Indic digits are digits; an ASCII-only pattern would silently skip the check."""
    assert specifics_in("أعمل ١٠ ساعات", "أعمل ساعات") == ("١٠",)
    assert specifics_in("أعمل ١٠ ساعات", "أعمل ١٠ ساعات") == ()


# --------------------------------------------------------------------------------------------- ground_claim


def test_ground_claim_returns_static_reasons_never_the_text() -> None:
    verdict = ground_claim(candidate("I live in Munich", "I live in Munich", "I live in Berlin"))
    assert verdict.grounded is False
    assert verdict.code == "quote_not_in_source"


def test_a_claimed_specific_absent_from_the_source_is_refused() -> None:
    verdict = ground_claim(candidate("meets Sara at 10am", "meet Sara", "I meet Sara tomorrow", specifics=("10am",)))
    assert verdict.reasons == ("specifics_not_in_source",)


def test_a_one_character_quote_is_refused_as_decorative() -> None:
    verdict = ground_claim(candidate("a", "a", "a"))
    assert verdict.reasons == ("quote_too_short",)


def test_grounding_is_necessary_but_never_sufficient() -> None:
    """A grounded claim can still be refused later; that separation is what makes each answer testable."""
    verdict = ground_claim(candidate("I live in Berlin", "live in Berlin", "I live in Berlin"))
    assert verdict.grounded is True
    assert verdict.code is None


# ---------------------------------------------------------------------------------------------- ground_text


def test_a_quote_must_be_licensed_by_one_fragment_with_its_specifics() -> None:
    """A number taken from a *different* document would otherwise be "present" for the wrong reason."""
    tool = ("build failed after 3 attempts", "tool")
    diff = ("retry limit raised to 5", "file")
    assert ground_text("retry limit is 5", "retry limit raised to 5", (tool, diff)).grounded is True
    # the quote is in the diff but the specific claimed (3) is in the tool fragment: refused
    assert ground_text("retry limit is 3", "retry limit raised to 5", (tool, diff)).grounded is False


def test_no_source_at_all_is_refused() -> None:
    assert ground_text("c", "c", ()).reasons == ("no_source",)


def test_a_quote_absent_from_every_fragment_names_that_reason() -> None:
    assert ground_text("c", "nothing like this", (("some text", "user"),)).code == "quote_not_in_source"
