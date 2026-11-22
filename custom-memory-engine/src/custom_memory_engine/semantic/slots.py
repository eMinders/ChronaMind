"""One normalization of an open semantic slot, so a backfill and a handler agree (``V2-D87``).

A slot is a **soft** grouping key ("residence", "preferred_editor", "meeting_time_constraint") -- suggested by
extraction, never looked up in a catalog. That makes it the one piece of a claim whose spelling is chosen freely, which
means the *same* belief can be written as ``residence``, ``Residence``, `` residence ``, ``residence.`` or
``residence `` (with a non-breaking space) by five honest callers. If those five strings reach the database, the
exclusion key sees five distinct predicates, `no_bitemporal_overlap` never fires, and two contradictory homes are both
active -- the exact failure the slot exists to prevent.

So the normalization happens **once**, here, and the database enforces the same rule with a `CHECK`
(``memory_version_semantic_slot_check``: trimmed, 1–120 characters, no control characters). That is the project's
"two implementations of one rule" pattern: if this function and the constraint ever disagree, the constraint is right
and this is the defect.

**What is normalized, and what is not.** Case, surrounding and repeated whitespace, and punctuation at the edges are
folded -- they carry no meaning in a key. Interior punctuation is kept (``meeting_time_constraint`` is not
``meetingtimeconstraint``), and internal case is folded because ``preferredEditor`` and ``preferred_editor`` are the
same slot. Nothing is *translated*: ``إقامة`` and ``residence`` are different keys on purpose. Deciding they mean the
same thing is a semantic judgement, and the place for it is retrieval (multilingual arms fuse them), not identity:
merging keys would silently combine two independent beliefs and make one supersede the other.

**Diacritics are kept.** Arabic ``مُقيم`` and ``مقيم`` differ by a harakat. Folding them is tempting and wrong here:
the fold belongs to the *lexical index* (`'simple'` + trigram, migration 23) where a false merge costs a slightly
noisier candidate list, not to an identity key where it costs a wrong supersession.
"""

from __future__ import annotations

from re import compile as compile_pattern
from unicodedata import category
from unicodedata import normalize as unicode_normalize

#: The longest slot the schema admits (``memory_version_semantic_slot_check``).
MAX_SLOT_LENGTH = 120
#: The shortest slot worth keeping. One character keys are too coarse to group anything usefully.
MIN_SLOT_LENGTH = 2

_SEPARATORS = compile_pattern(r"[\s\-./\\]+")
_EDGE_PUNCTUATION = compile_pattern(r"^[^\w]+|[^\w]+$")


class SlotError(ValueError):
    """A static code; never carries the slot text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_slot(value: object) -> str | None:
    """The canonical form of a slot, or ``None`` when the caller supplied none.

    Refuses rather than repairs where repair would change meaning: an empty or whitespace-only slot is
    ``slot_required`` (a caller who sent one believes it keyed something), a slot longer than the schema admits is
    ``slot_too_long``, and a slot made only of punctuation is ``slot_unusable``.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise SlotError("slot_must_be_a_string")
    # NFKC first: a full-width "ｒｅｓｉｄｅｎｃｅ" and "residence" are the same word, and the compatibility form is
    # what a keyboard or a paste may produce. NFKC also folds the non-breaking space to a normal one, which the
    # separator collapse below then treats as a separator.
    folded = unicode_normalize("NFKC", value).casefold().strip()
    if not folded:
        raise SlotError("slot_required")
    # Collapse runs, then trim the separator *and* any other edge punctuation. Both steps are needed and in this order:
    # `residence.` collapses to a trailing `_`, and `_` is a word character, so only trimming it first makes the
    # spelling equal to `residence`; `...` collapses to `_` and trims to nothing, which is `slot_unusable` rather than
    # a confusing `slot_too_short`.
    collapsed = _SEPARATORS.sub("_", folded).strip("_")
    trimmed = _EDGE_PUNCTUATION.sub("", collapsed)
    if not trimmed:
        raise SlotError("slot_unusable")
    if len(trimmed) < MIN_SLOT_LENGTH:
        raise SlotError("slot_too_short")
    if len(trimmed) > MAX_SLOT_LENGTH:
        raise SlotError("slot_too_long")
    if any(category(character).startswith("C") for character in trimmed):
        # A control or format character in a key is invisible and makes two keys that *print* the same differ. The
        # schema refuses it too; refusing here keeps the error a typed code rather than a constraint violation.
        raise SlotError("slot_contains_a_control_character")
    return trimmed


def slots_equivalent(left: object, right: object) -> bool:
    """Whether two slot spellings denote the same key: both ``None``, or both normalizing to the same form."""
    return normalize_slot(left) == normalize_slot(right)


__all__ = ["MAX_SLOT_LENGTH", "MIN_SLOT_LENGTH", "SlotError", "normalize_slot", "slots_equivalent"]
