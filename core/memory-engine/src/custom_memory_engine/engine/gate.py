"""Five-check write validation gate (RL-060, Proposal §7.5).

The gate is pure Python over already-extracted candidates: it never calls a model,
so it is deterministic, replayable, and cheap enough to run on every candidate.
Model-backed classification (assertion mode, conflict relation) is injected by the
caller as a callable, which keeps the engine provider-free and lets the evaluation
harness freeze extraction once (RL-032).

Checks, in order, each recording a ``MEMORY_CONFLICT`` reason code:

1. ``evidence`` — every **turn** span must occur in a user-authored turn, and a non-turn source must be
   structurally complete. Before W3.5 this check was turn-only, which made a claim resting on a tool result, a file or
   a fetched document unrepresentable however well it was cited.
2. ``assertion_mode`` — only ``asserted`` may become active; ``planned`` routes to
   ``plans_to``; ``hedged``/``hypothetical`` hold; ``third_party`` holds too (W3.5:
   a claim attributed to a source the engine did not hear it from is a hypothesis
   awaiting adjudication, and rejecting it would destroy evidence a user pasted);
   ``quoted``/``joking`` are rejected because they assert nothing.
3. ``instruction_screen`` — persistent behavioral directives are rejected.
4. ``conflict`` — relation against existing current beliefs decides commit style.
5. ``confidence`` — numeric thresholding applies only when calibration is enabled.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.errors import InvalidParameterValue
from psycopg.rows import tuple_row

type AssertionMode = Literal["asserted", "hedged", "hypothetical", "planned", "quoted", "third_party", "joking"]
type Decision = Literal["accept", "hold", "reject"]
type ConflictRelation = Literal["update", "correction", "additive", "duplicate", "unrelated", "uncertain"]
type GateCheck = Literal["evidence", "assertion_mode", "instruction_screen", "conflict", "confidence"]

PLANS_TO_PREDICATE = "plans_to"


class GateError(InvalidParameterValue):
    """Static gate contract error (SQLSTATE 22023)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GateInput:
    """One extracted candidate plus the evidence turn texts it claims to rest on."""

    version_id: str
    user_id: str
    content: str
    evidence_spans: tuple[str, ...]
    evidence_turns: tuple[tuple[str, str], ...]  # (role, content)
    assertion_mode: AssertionMode = "asserted"
    predicate_key: str = ""
    confidence: float | None = None
    #: One entry per ``evidence_spans`` item: the declared ``source_type`` of that source, or ``None`` for a v1 writer
    #: whose evidence is a turn. A non-turn source is grounded structurally -- the schema requires a reference, a span
    #: and a content hash -- so it has no turn to look for, and demanding one would refuse every citation of a tool's
    #: output. What stops such a claim from being presented as fact is the authority ladder and the assertion mode,
    #: not this check.
    evidence_source_types: tuple[str | None, ...] = ()


@dataclass(frozen=True)
class GateDecision:
    """The gate's verdict; ``reason_code`` is static and content-free."""

    decision: Decision
    reason_code: str
    checked: tuple[str, ...]
    status: Literal["active", "conflict_pending", "rejected"]
    predicate_key: str | None = None
    relation: ConflictRelation | None = None


# Directive shapes that must never persist as memory. Deliberately narrow and
# literal: a false candidate here is a rejected memory, not an incorrect answer.
_INSTRUCTION_PATTERNS: tuple[str, ...] = (
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "disregard the above",
    "system prompt",
    "you must always",
    "from now on, always",
    "always tell the user",
    "always answer",
    "never mention",
    "do not tell the user",
    "override your instructions",
)


def instruction_screen(content: str) -> bool:
    """Return ``True`` when the text contains a persistent directive pattern."""
    normalized = " ".join(content.lower().split())
    return any(pattern in normalized for pattern in _INSTRUCTION_PATTERNS)


def evidence_supported(
    spans: Sequence[str],
    turns: Sequence[tuple[str, str]],
    source_types: Sequence[str | None] = (),
) -> bool:
    """Whether the cited evidence can carry a claim.

    Every span whose source is a **turn** (or whose type is unknown, which is the v1 shape) must appear verbatim in at
    least one user-authored turn -- the check v1 shipped, unchanged for the case it was written for. A span whose
    source is ``external``/``tool``/``file``/``event`` is grounded by its citation: the schema refuses one without a
    reference, a span and a well-formed content hash, and :mod:`semantic.grounding` verifies the quote against the
    captured text at ingestion. Requiring a user turn for such a source would make non-chat evidence unusable, which is
    the defect ``V2-D87`` removes.
    """
    if not spans:
        return False
    types = tuple(source_types) if source_types else tuple(None for _ in spans)
    if len(types) != len(spans):
        return False
    user_turns = [content for role, content in turns if role == "user"]
    for span, source_type in zip(spans, types, strict=True):
        if source_type is not None and source_type != "turn":
            if not span:
                return False  # a non-turn citation still needs its span; the schema agrees
            continue
        if not user_turns or not any(span and span in content for content in user_turns):
            return False
    return True


def classify_assertion(mode: AssertionMode) -> GateDecision | None:
    """Map assertion mode to a decision; ``None`` means "continue to conflict check"."""
    if mode == "asserted":
        return None
    if mode == "planned":
        return GateDecision("accept", "planned_routed_to_plans_to", ("assertion_mode",), "active", PLANS_TO_PREDICATE)
    if mode in ("hedged", "hypothetical"):
        return GateDecision("hold", f"{mode}_requires_confirmation", ("assertion_mode",), "conflict_pending")
    if mode == "third_party":
        # `V2-D87`: third-party evidence is quarantined, not discarded. A held row is `contested`, which the channel
        # router sends to `conflicts` -- so a claim resting only on a web page, a tool's output or a file can never be
        # presented as a fact, and the evidence is still there for a Tier C adjudication. The authority ladder
        # (`semantic.authority`) is what stops it superseding a user's own belief in the meantime.
        return GateDecision("hold", "third_party_requires_adjudication", ("assertion_mode",), "conflict_pending")
    return GateDecision("reject", "non_user_assertion", ("assertion_mode",), "rejected")


def evaluate(
    candidate: GateInput,
    *,
    relation: ConflictRelation | None = None,
    calibration: Mapping[str, float] | None = None,
    enabled: Sequence[GateCheck] = ("evidence", "assertion_mode", "instruction_screen", "conflict", "confidence"),
) -> GateDecision:
    """Run the enabled checks in order and return the first decisive verdict.

    ``A3`` (P-nogate) passes ``enabled=()``; ``A4`` disables individual checks.
    """
    checked: list[str] = []

    if "evidence" in enabled:
        checked.append("evidence")
        if not evidence_supported(candidate.evidence_spans, candidate.evidence_turns, candidate.evidence_source_types):
            return GateDecision("reject", "unconfirmed_assistant_claim", tuple(checked), "rejected")

    if "assertion_mode" in enabled:
        verdict = classify_assertion(candidate.assertion_mode)
        if verdict is not None:
            checked.extend(verdict.checked)
            return GateDecision(
                verdict.decision,
                verdict.reason_code,
                tuple(dict.fromkeys(checked)),
                verdict.status,
                verdict.predicate_key,
            )
        checked.append("assertion_mode")

    if "instruction_screen" in enabled:
        checked.append("instruction_screen")
        if instruction_screen(candidate.content):
            return GateDecision("reject", "instruction_injection", tuple(checked), "rejected")

    if "confidence" in enabled and calibration is not None and candidate.confidence is not None:
        # A per-predicate threshold wins even when it is 0.0 (accept any confidence): ``or`` would let the
        # global "*" threshold override it because 0.0 is falsy.
        threshold = calibration.get(candidate.predicate_key)
        if threshold is None:
            threshold = calibration.get("*")
        if threshold is not None and candidate.confidence < threshold:
            checked.append("confidence")
            return GateDecision("hold", "below_calibrated_confidence", tuple(checked), "conflict_pending")

    if "conflict" in enabled:
        checked.append("conflict")
        if relation in ("update", "correction"):
            return GateDecision("accept", f"supersede_{relation}", tuple(checked), "active", relation=relation)
        if relation == "duplicate":
            return GateDecision("accept", "reinforce_duplicate", tuple(checked), "active", relation=relation)
        if relation == "uncertain":
            return GateDecision("hold", "uncertain_conflict", tuple(checked), "conflict_pending", relation=relation)
        if relation in ("additive", "unrelated"):
            return GateDecision("accept", f"new_{relation}", tuple(checked), "active", relation=relation)

    return GateDecision("accept", "gate_passed", tuple(checked), "active")


async def record_decision(
    connection: AsyncConnection,
    *,
    user_id: str,
    candidate_version_id: str,
    decision: GateDecision,
    existing_version_id: str | None = None,
    decided_by: str = "validation-gate",
    schema_name: str = "cme_engine",
) -> str:
    """Persist one ``MEMORY_CONFLICT`` audit row and return its id.

    The gate itself is pure; this is the only database side effect, so a caller
    may evaluate candidates without touching the database (dry runs, ablations).
    """
    conflict_id = f"conflict-{uuid4().hex}"
    async with connection.cursor() as cursor:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {c} (id, candidate_version_id, existing_version_id, user_id, relation, "
                "decision, reason_code, decided_by, decided_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, pg_catalog.transaction_timestamp())"
            ).format(c=sql.Identifier(schema_name, "memory_conflict")),
            (
                conflict_id,
                candidate_version_id,
                existing_version_id,
                user_id,
                decision.relation or "unrelated",
                decision.decision,
                decision.reason_code,
                decided_by,
            ),
        )
    return conflict_id


async def existing_relation(
    connection: AsyncConnection,
    *,
    user_id: str,
    subject_key: str,
    predicate_key: str,
    candidate_content: str,
    semantic_slot: str | None = None,
    valid_from: datetime | None = None,
    schema_name: str = "cme_engine",
) -> tuple[ConflictRelation | None, str | None]:
    """Cheap deterministic conflict lookup over current single-valued beliefs.

    Returns ``(relation, existing_version_id)``. Exact normalized content is a ``duplicate`` only when that belief is
    **valid at** ``valid_from`` (the candidate's valid-time start); other overlaps are left ``None`` for the open-ended
    three-stage resolution (``semantic.conflict``) or the transactional supersession path to decide.

    Why valid time matters: supersession closes the old value's *valid* range and reissues it as a still-believed
    historical fact ("lived in Berlin until March"). A user who later says "I live in Berlin" again is not repeating
    that historical row — at the new valid time the current value is Paris — so matching it as a duplicate would
    reinforce history, drop the new statement and leave Paris current. ``valid_from=None`` keeps the content-only
    match for callers that have no valid time to offer.

    The lookup is scoped by ``COALESCE(semantic_slot, predicate_key)`` -- migration 24's exclusion key -- so it finds
    exactly the beliefs the constraint considers exclusive. Before that key the lookup was ``(subject_key,
    predicate_key)`` only, which with one generic predicate meant every single-valued claim about a person compared
    against every other one.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id, content, event_range FROM {v} "
                "WHERE user_id = %s AND subject_key = %s AND predicate_key = %s "
                "AND COALESCE(semantic_slot, predicate_key) = COALESCE(%s, predicate_key) "
                "AND single_valued = true AND status = 'active' AND upper_inf(recorded_range) "
                "ORDER BY lower(event_range) DESC, id LIMIT 5"
            ).format(v=sql.Identifier(schema_name, "memory_version")),
            (user_id, subject_key, predicate_key, semantic_slot),
        )
        rows = await cursor.fetchall()
    normalized = " ".join(candidate_content.lower().split())
    for version_id, content, event_range in rows:
        if " ".join(content.lower().split()) != normalized:
            continue
        if valid_from is None or event_range is None or valid_from in event_range:
            return "duplicate", version_id
    return (None, rows[0][0] if rows else None)


# Injected model-backed classifier; absent by default so the gate stays provider-free.
type RelationClassifier = Callable[[GateInput, Sequence[str]], ConflictRelation]
