"""Deterministic authority: `USER > SYSTEM > ASSISTANT > THIRD_PARTY`, derived, never declared (``V2-D88``).

Veracium's contribution to the 2026 state of the art is not a prompt instruction -- it is the observation that a
memory engine which lets retrieved text influence what it believes has no trustworthy beliefs at all. The mechanism is
an **authority ladder**: every piece of evidence has a rank fixed by *what it is*, and a lower-ranked source can never
supersede a higher-ranked one. A web page cannot rewrite the user's home address; a tool's output cannot silently
replace an assertion the user made.

**Where the rank comes from.** ``memory_evidence`` already stores ``source_type`` (``turn`` / ``external`` / ``tool``
/ ``file`` / ``event``, migrations 8/16/17) and ``evidence_role`` (``user`` / ``assistant`` / anything a caller wrote).
Both are structural: they are what the caller declared and what the schema admits. Migration 24 therefore derives
``authority_rank`` as a **generated column** from those two, which is the one form a compromised runtime cannot forge.
This module is that derivation's Python twin, kept deliberately equal (the project's "two implementations of one rule"
pattern) so a handler can rank an in-memory candidate without a second query, and
``tests/test_semantic_authority.py`` pins the two against each other.

**The refusal is a decision, not an error.** A third-party claim that contradicts a user claim is not dropped: it is
*recorded* and routed to the `conflicts` channel, because the fact that the web says something different is itself
information the user may want. What the ladder forbids is the silent replacement -- ``supersession_refusals`` in
Veracium's terms, ``reason_code = 'authority_quarantine'`` in this engine's ``memory_conflict`` record.

**What is deliberately not here.** No prompt-injection phrase list. The old ``_INSTRUCTION_PATTERNS`` in
``engine.gate`` was twelve English substrings, which a paraphrase in any language defeats and which a legitimate claim
("never tell the user the meeting moved") trips. Quarantine by *source* is structural: it does not depend on what the
text says, only on where it came from, so it cannot be paraphrased around. ``instruction_screen`` remains as a
belt-and-braces check on *user* text for the narrow case of a directive a user could not have meant to store, and it is
no longer the primary defence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

type Authority = Literal[0, 1, 2, 3]

#: The ladder, strongest first. The names are the vocabulary; the numbers are what `memory_evidence.authority_rank`
#: stores, and they are frozen because a stored rank is read by the conflict path.
USER: Authority = 0
SYSTEM: Authority = 1
ASSISTANT: Authority = 2
THIRD_PARTY: Authority = 3
LEVELS: tuple[Authority, ...] = (USER, SYSTEM, ASSISTANT, THIRD_PARTY)
NAMES: dict[Authority, str] = {USER: "user", SYSTEM: "system", ASSISTANT: "assistant", THIRD_PARTY: "third_party"}

#: Source types that are third-party by definition: the engine did not hear them from the user. ``tool``, ``file``,
#: ``event`` and ``external`` all describe something fetched or produced, so all four are ranked alike. A ``tool``
#: result is not less forgeable than a web page -- both are text the engine did not author.
THIRD_PARTY_SOURCES: frozenset[str] = frozenset({"external", "tool", "file", "event"})
#: Roles on a ``turn`` that carry a rank of their own. Anything else is third-party: an unrecognised role is untrusted,
#: not trusted by default, which is the opposite of what a permissive default would do.
TURN_ROLES: dict[str, Authority] = {"user": USER, "system": SYSTEM, "assistant": ASSISTANT}


class AuthorityError(ValueError):
    """A static code; never carries the text it refused."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def rank_for(source_type: str, evidence_role: str | None = None) -> Authority:
    """The ladder rank of one evidence item. Mirrors migration 24's generated column exactly."""
    if not isinstance(source_type, str) or not source_type:
        raise AuthorityError("source_type_required")
    if source_type != "turn":
        return THIRD_PARTY
    if evidence_role is None:
        # A turn with no role is not attributable to the user; the conservative rank is the only safe one.
        return THIRD_PARTY
    return TURN_ROLES.get(evidence_role, THIRD_PARTY)


def strongest(ranks: Iterable[Authority]) -> Authority:
    """The weakest (numerically greatest) rank in a set: a claim is only as authoritative as its weakest support.

    Overflow is the right default, because the alternative -- taking the strongest -- lets a claim attach one user
    quote as cover for nine web pages. An empty set is third-party for the same reason.
    """
    found = list(ranks)
    if not found:
        return THIRD_PARTY
    return max(found)


def version_authority(evidence: Sequence[tuple[str, str | None]]) -> Authority:
    """The rank of an artifact from its ``(source_type, evidence_role)`` pairs."""
    return strongest(rank_for(source_type, role) for source_type, role in evidence)


@dataclass(frozen=True, slots=True)
class SupersessionDecision:
    """Whether a candidate may replace an existing belief, and the static reason when it may not.

    ``refused`` deliberately does not mean *dropped*: the caller records the attempt as a conflict, which is what makes
    the refusal auditable. ``quarantined`` names the case the ladder exists for -- third-party evidence contradicting a
    user's own assertion.
    """

    allowed: bool
    reason_code: str
    quarantined: bool = False

    @property
    def refused(self) -> bool:
        return not self.allowed


def may_supersede(candidate: Authority, existing: Authority) -> SupersessionDecision:
    """Whether evidence at ``candidate`` may supersede a belief resting on ``existing``.

    Three cases, and only three:

    * ``candidate <= existing`` -- allowed. Equal rank is allowed on purpose: a user's correction of their own earlier
      statement is the most common supersession there is, and an assistant's later message may refine an earlier one.
    * ``candidate > existing`` -- refused. A lower-ranked source cannot replace a higher-ranked belief.
    * anything else -- unreachable for a valid :data:`Authority`, so it is refused rather than defaulted: a rank the
      ladder does not know is not a rank the engine should act on.
    """
    if candidate not in LEVELS or existing not in LEVELS:
        return SupersessionDecision(False, "authority_rank_unknown")
    if candidate <= existing:
        return SupersessionDecision(True, "authority_permits_supersession")
    return SupersessionDecision(False, "authority_quarantine", quarantined=True)


def quarantine(source_type: str) -> bool:
    """Whether evidence of ``source_type`` is structurally quarantined: it may inform, never instruct.

    The engine applies this in three places and all three are deterministic: a quarantined claim may not supersede a
    non-quarantined one (:func:`may_supersede`), a quarantined claim is never routed to the `facts` channel if its
    lifecycle is anything but ``asserted`` (the channel router already guarantees that structurally), and a quarantined
    claim's text is never treated as an instruction -- which is why there is no phrase list to bypass.

    The question is about the *source class*, not about one item's rank: a ``turn`` is not quarantined by its type,
    even though a turn whose role is unrecognised ranks third-party (:func:`rank_for`). Those are two different
    questions and conflating them would quarantine every dialogue.
    """
    return source_type in THIRD_PARTY_SOURCES


__all__ = [
    "ASSISTANT",
    "LEVELS",
    "NAMES",
    "SYSTEM",
    "THIRD_PARTY",
    "THIRD_PARTY_SOURCES",
    "TURN_ROLES",
    "USER",
    "Authority",
    "AuthorityError",
    "SupersessionDecision",
    "may_supersede",
    "quarantine",
    "rank_for",
    "strongest",
    "version_authority",
]
