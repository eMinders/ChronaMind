"""Deterministic, model-free slot classification for the app's fast-lane writes (``V2-D115``).

A user message becomes an engine claim through ``assert``, and the engine only supersedes a claim that is
``cardinality="single"`` *and* keyed by a ``semantic_slot``: a single-valued claim with no slot would fall back to the
generic ``statement`` predicate and make every unrelated single-valued message exclusive with every other one. So the
app may only call a message single-valued when it can also name the slot, and it must be right when it does: a false
"single" silently retires a true belief, while a false "multi" only leaves an old belief standing until the slow lane
(general extraction, off the write path) resolves it.

That asymmetry is why this classifier is **high-precision and deliberately low-recall**:

* the whole message must be one plain present-state statement (an optional discourse opener such as ``Actually,`` and a
  closed set of time words such as ``now`` or ``last month`` are the only extras admitted);
* the value must look like a proper name (capitalised words, an optional leading ``the``, a few lowercase connectors
  such as ``of`` or ``de``), so ``I live in fear`` or ``I work at home`` stay ``multi``;
* questions, negations, hypotheticals, plans, past narrative (``I lived in``, ``I visited``, ``I moved to Paris in
  2010``) and lists (``Berlin and Paris``) never match, because nothing in the grammar admits them.

The slot vocabulary is closed and small: :data:`RESIDENCE`, :data:`EMPLOYER` and :data:`NAME`. The pinning tests are
``tests/test_memory_slots.py``.
"""

from __future__ import annotations

import re
from typing import Literal, NamedTuple

type Cardinality = Literal["single", "multi"]

#: Where the user lives now. ``I live in X``, ``I moved to X``, ``I'm based in X``.
RESIDENCE = "residence"
#: Who the user works for now. ``I work at X``, ``I work for X``, ``I'm employed by X``.
EMPLOYER = "employer"
#: What the user is called. ``My name is X``.
NAME = "name"

#: Every slot this module can emit. The engine normalizes slots (``semantic.slots.normalize_slot``); these are already
#: in that canonical form, so the spelling sent is the spelling stored.
SLOT_VOCABULARY: frozenset[str] = frozenset({RESIDENCE, EMPLOYER, NAME})


class SlotClassification(NamedTuple):
    """``("single", slot)`` for a clear single-valued state statement, otherwise ``("multi", None)``."""

    cardinality: Cardinality
    semantic_slot: str | None


MULTI = SlotClassification("multi", None)

# An optional discourse opener: the only words admitted before the statement itself.
_OPENER = r"(?:(?:actually|by the way|btw|fyi|update|just so you know)\s*[,:]?\s+)?"
# Present-state adverbs admitted between the subject and the verb.
_NOW = r"(?:(?:now|currently|still)\s+)?"
_JUST = r"(?:(?:just|recently|finally)\s+)?"
_AM = r"(?:i'm|i\s+am)\s+"
_HAVE = r"(?:i've\s+|i\s+have\s+|i\s+)"

#: Each slot's verb phrases. Every phrase is a present state or a completed move *into* a state; none is a plan, a wish,
#: a negation or a past state, so none of those can ever reach the value capture below.
_PHRASES: tuple[tuple[str, str], ...] = (
    (RESIDENCE, rf"i\s+{_NOW}live\s+in"),
    (RESIDENCE, rf"{_AM}{_NOW}living\s+in"),
    (RESIDENCE, rf"{_AM}{_NOW}based\s+in"),
    (RESIDENCE, rf"i\s+{_NOW}reside\s+in"),
    (RESIDENCE, rf"{_HAVE}{_JUST}(?:moved|relocated)\s+to"),
    (EMPLOYER, rf"i\s+{_NOW}work\s+(?:at|for)"),
    (EMPLOYER, rf"{_AM}{_NOW}working\s+(?:at|for)"),
    (EMPLOYER, rf"{_AM}{_NOW}employed\s+(?:at|by)"),
    (NAME, r"my\s+name(?:'s|\s+is)"),
)

# The time words a statement may end with. Each keeps the statement about the present state; an explicit date or a
# clause ("in 2010", "when I was five") is not admitted, because that is narrative rather than state.
_TAIL = (
    r"(?:\s+(?:now|today|currently|these\s+days|recently|yesterday|"
    r"(?:last|this)\s+(?:week|month|year|spring|summer|autumn|fall|winter)))?"
)

# The value: one to six word tokens, matched lazily so a trailing time word ("Paris now") lands in `_TAIL`, not in the
# value. Its shape (capitalised, no conjunctions) is checked by `_is_proper_name`, because a case-insensitive pattern
# cannot say "this word starts with a capital".
_VALUE = r"(?P<value>[^\W\d_][\w'.&-]*(?:\s+[^\W_][\w'.&-]*){0,5}?)"

_STATEMENTS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (slot, re.compile(rf"{_OPENER}{phrase}\s+{_VALUE}{_TAIL}\s*[.!]?", re.IGNORECASE)) for slot, phrase in _PHRASES
)

#: Lowercase words allowed *inside* a proper name ("University of Oxford", "Rio de Janeiro"), never at its edges. ``on``
#: is deliberately absent: "Google on Mondays" is a schedule, not an employer.
_CONNECTORS = frozenset({"of", "de", "da", "del", "di", "du", "la", "le", "van", "von", "am"})
#: The longest word (full stop included) read as an abbreviation inside a value: ``St.``, ``Mt.``, ``Ave.``.
_ABBREVIATION_LENGTH = 4


def _is_proper_name(value: str) -> bool:
    """A value that reads as a named place, organisation or person: capitalised words, an optional leading ``the``."""
    words = value.split()
    if words and words[0] == "the":
        words = words[1:]
    if not words or not words[0][0].isupper() or not words[-1][0].isupper():
        return False
    # A full stop inside the value is only an abbreviation ("St. Louis"); "Berlin. Paris" is two sentences.
    if any(word.endswith(".") and len(word) > _ABBREVIATION_LENGTH for word in words[:-1]):
        return False
    return all(word[0].isupper() or word in _CONNECTORS for word in words)


def _normalize(message: str) -> str:
    """Fold typographic apostrophes and whitespace runs, so ``I’m`` and ``I'm`` are one spelling."""
    return " ".join(message.replace("\u2019", "'").replace("\u2018", "'").split())


def classify_slot(message: str) -> SlotClassification:
    """Classify one user message as a single-valued slot update, or ``("multi", None)`` when it is not clearly one.

    Deterministic and model-free: the same text always yields the same answer, and the write path stays cheap.
    """
    text = _normalize(message)
    if not text:
        return MULTI
    for slot, pattern in _STATEMENTS:
        match = pattern.fullmatch(text)
        if match is not None and _is_proper_name(match.group("value")):
            return SlotClassification("single", slot)
    return MULTI
