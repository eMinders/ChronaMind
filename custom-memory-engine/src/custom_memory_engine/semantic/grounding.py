"""Deterministic grounding of an extracted claim: the quote exists, and the specifics appear (``V2-D88``).

A model that produces a plausible sentence and a fabricated citation is the single most dangerous failure mode in a
memory engine, because the result is indistinguishable from a true memory *after* storage. Two checks make it
detectable, and both are cheap, language-agnostic and independent of the model that produced the claim:

1. **Quote grounding.** ``verbatim_quote`` must occur in the source text. This is what Quipu's quote-grounded
   assertions do and what Veracium's provenance-first design exists to protect: the claim's licence to exist is a
   substring of something the user actually said.
2. **Specifics grounding** (Veracium). Every number, date and identifier the claim asserts must appear in the same
   source. A claim of "meets Sara at 10am" grounded on "I met Sara yesterday" is refused, and the refusal is a
   *static code*, not a warning.

**Why NFC and not the existing hash canonicalisation.** Both are right for their own purpose and they are not the same
function, which is why this module does not reuse ``grounding.sources.canonical_hash``'s input for matching:

* ``canonical_hash`` answers "is this the same *document*" -- NFC, ``\\r\\n`` folded, SHA-256 -- and is used to prove a
  caller sent the bytes it said it sent.
* Matching answers "does this quote occur in that text" -- NFC, and then a **Unicode-aware** substring test. Folding
  newlines would make a quote match across a paragraph break it does not actually span, and normalising further
  (casefold, accent stripping, Arabic diacritics) would let a *different* sentence licence a claim.

**Why not translation.** A quote translated back to the source language cannot be verified; the engine would have to
trust the model's claim that its translation is faithful. Storing the original quote and the canonical claim
separately costs one more string and removes the entire class of "grounded in nothing".

**Why whitespace is collapsed *only* for convenience matching.** A model reproducing a quote across a line break in a
transcript is honest, and refusing it would reject true claims. Whitespace runs are therefore collapsed on both sides
before comparison. Nothing else is folded: not case (a name's capitalisation is content), not diacritics (Arabic
``أحمد`` and ``احمد`` are different spellings of the same name, but deciding that is the entity resolver's job,
where a false merge is visible, not grounding's, where it would silently license a claim).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from custom_memory_engine.semantic.contracts import CandidateBelief

#: Digits in any script, not ``[0-9]``: a claim in Arabic asserts ``١٠`` as much as one in English asserts ``10``.
#: ``\d`` under Python's default string pattern is ``Nd`` (decimal digit) for every script, which is exactly right.
_IDENTIFIER = r"[^\W_]+(?:[._:/#-][^\W_]+)+"
_NUMBER = r"\d+(?:[.,:]\d+)*"


class GroundingError(ValueError):
    """A static code for a refused claim; never carries the text, the quote or a number."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class GroundingVerdict:
    """The outcome: whether the claim may proceed, and the static reasons when it may not."""

    grounded: bool
    reasons: tuple[str, ...]

    @property
    def code(self) -> str | None:
        """The first reason, as the code a handler returns. ``None`` when grounded."""
        return self.reasons[0] if self.reasons else None


def normalize(text: str) -> str:
    """NFC, then whitespace runs collapsed to one space, then trimmed -- nothing else.

    The same norm the database applies to a stored turn's hash is applied first (NFC) so a quote taken from the text
    the engine hashed matches the text the engine stored; the rest is the minimum needed for a line break in a
    transcript not to fail an honest quote.
    """
    if not isinstance(text, str):
        raise GroundingError("grounding_text_must_be_a_string")
    return " ".join(unicodedata.normalize("NFC", text).split())


def quote_grounded(quote: str, source: str) -> bool:
    """Whether ``quote`` occurs in ``source`` after :func:`normalize`."""
    if not isinstance(quote, str) or not quote:
        return False
    return normalize(quote) in normalize(source)


def specifics_in(claim_text: str, source: str) -> tuple[str, ...]:
    """The numbers, dates and identifiers of ``claim_text`` that do **not** appear in ``source``.

    A claim with no specifics returns an empty tuple: nothing to check is not a failure. An empty result means every
    specific it asserts is present somewhere in the source, which is the strongest statement a substring check can make
    without parsing the sentence -- and parsing the sentence is the model's job, not this module's.
    """
    from re import findall  # local: this module's import surface stays minimal for callers that only normalize

    if not isinstance(claim_text, str) or not isinstance(source, str):
        raise GroundingError("grounding_text_must_be_a_string")
    haystack = normalize(source)
    found: list[str] = []
    for pattern in (_NUMBER, _IDENTIFIER):
        for token in findall(pattern, unicodedata.normalize("NFC", claim_text)):
            if token not in found and normalize(token) not in haystack:
                found.append(token)
    # Report only the maximal token: `v2.3.1` implies its `2.3.1` part, and a caller fixing one string should not be
    # told about a fragment of it. Order is the order the claim states them, so the answer is deterministic.
    return tuple(token for token in found if not any(token != other and token in other for other in found))


def ground_claim(candidate: CandidateBelief) -> GroundingVerdict:
    """Ground one candidate: its quote must exist, and every specific it asserts must appear in the same source.

    Reasons, in check order, each a static code:

    * ``quote_not_in_source`` -- the quote does not occur in the text the claim cites;
    * ``specifics_not_in_source`` -- a number, date or identifier it asserts occurs nowhere in that text;
    * ``quote_too_short`` -- a quote of fewer than :data:`MIN_QUOTE_CHARACTERS` after normalization. A one-character
      quote occurs in almost any text, so accepting it would make grounding decorative.

    A claim may be *grounded* and still be refused later: the authority ladder
    (:mod:`~custom_memory_engine.semantic.authority`) refuses a third-party claim's attempt to supersede a user's, and
    the lifecycle table refuses an illegal transition. This module answers exactly one question, and answering exactly
    one is what makes its answer testable.
    """
    reasons: list[str] = []
    if len(normalize(candidate.claim.verbatim_quote)) < MIN_QUOTE_CHARACTERS:
        reasons.append("quote_too_short")
    elif not quote_grounded(candidate.claim.verbatim_quote, candidate.source_text):
        reasons.append("quote_not_in_source")
    missing = specifics_in(candidate.claim.content, candidate.source_text)
    if missing:
        reasons.append("specifics_not_in_source")
    declared = tuple(normalize(item) for item in candidate.claim.specifics)
    source = normalize(candidate.source_text)
    if any(item and item not in source for item in declared):
        # The model's own declaration is checked too: it may under-report, and a declared specific is a stronger
        # claim than one this module happened to notice.
        reasons.append("specifics_not_in_source")
    return GroundingVerdict(not reasons, tuple(dict.fromkeys(reasons)))


#: Below this, a quote matches nearly any text and grounding stops meaning anything.
MIN_QUOTE_CHARACTERS = 3

#: The source-item shape :func:`ground_text` accepts: ``(text, role)``. ``role`` decides authority, never grounding.
type SourcedText = tuple[str, Literal["user", "assistant", "system", "tool", "file", "event", "external"]]


def ground_text(claim_text: str, quote: str, sources: Sequence[SourcedText]) -> GroundingVerdict:
    """Ground a quote and its specifics against a *set* of source fragments.

    Multiple sources matter for non-turn evidence: a claim assembled from a tool's output and a file's diff must be
    licensed by one of them, and its specifics must appear in the **same** fragment as the quote -- a number taken from
    a different document would otherwise be "present" for the wrong reason.
    """
    if not sources:
        return GroundingVerdict(False, ("no_source",))
    if len(normalize(quote)) < MIN_QUOTE_CHARACTERS:
        return GroundingVerdict(False, ("quote_too_short",))
    quoted_anywhere = False
    for text, _role in sources:
        if not quote_grounded(quote, text):
            continue
        quoted_anywhere = True
        if not specifics_in(claim_text, text):
            return GroundingVerdict(True, ())
    return GroundingVerdict(False, ("specifics_not_in_source" if quoted_anywhere else "quote_not_in_source",))


__all__ = [
    "MIN_QUOTE_CHARACTERS",
    "GroundingError",
    "GroundingVerdict",
    "SourcedText",
    "ground_claim",
    "ground_text",
    "normalize",
    "quote_grounded",
    "specifics_in",
]
