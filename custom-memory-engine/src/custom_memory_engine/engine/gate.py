"""Five-check write validation gate (RL-060, Proposal §7.5).

The gate is pure Python over already-extracted candidates: it never calls a model,
so it is deterministic, replayable, and cheap enough to run on every candidate.
Model-backed classification (assertion mode, conflict relation) is injected by the
caller as a callable, which keeps the engine provider-free and lets the evaluation
harness freeze extraction once (RL-032).

Checks, in order, each recording a ``MEMORY_CONFLICT`` reason code:

1. ``evidence`` — the candidate's evidence spans must occur in user-authored turns.
2. ``assertion_mode`` — only ``asserted`` may become active; ``planned`` routes to
   ``plans_to``; ``hedged``/``hypothetical`` hold; ``quoted``/``third_party``/
   ``joking`` are rejected.
3. ``instruction_screen`` — persistent behavioral directives are rejected.
4. ``conflict`` — relation against existing current beliefs decides commit style.
5. ``confidence`` — numeric thresholding applies only when calibration is enabled.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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


def evidence_supported(spans: Sequence[str], turns: Sequence[tuple[str, str]]) -> bool:
    """Every span must appear verbatim in at least one user-authored turn."""
    user_turns = [content for role, content in turns if role == "user"]
    if not spans or not user_turns:
        return False
    return all(any(span and span in content for content in user_turns) for span in spans)


def classify_assertion(
    mode: AssertionMode, *, calibrated_confidence: Mapping[str, float] | None = None, confidence: float | None = None
) -> GateDecision | None:
    """Map assertion mode to a decision; ``None`` means "continue to conflict check"."""
    if mode == "asserted":
        return None
    if mode == "planned":
        return GateDecision("accept", "planned_routed_to_plans_to", ("assertion_mode",), "active", PLANS_TO_PREDICATE)
    if mode in ("hedged", "hypothetical"):
        return GateDecision("hold", f"{mode}_requires_confirmation", ("assertion_mode",), "conflict_pending")
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
        if not evidence_supported(candidate.evidence_spans, candidate.evidence_turns):
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
        threshold = calibration.get(candidate.predicate_key) or calibration.get("*")
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
    schema_name: str = "cme_engine",
) -> tuple[ConflictRelation | None, str | None]:
    """Cheap deterministic conflict lookup over current single-valued beliefs.

    Returns ``(relation, existing_version_id)``. Exact normalized content is a
    ``duplicate``; other overlaps are left ``None`` for an injected model
    classifier (or for the transactional supersession path) to decide.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id, content FROM {v} WHERE user_id = %s AND subject_key = %s AND predicate_key = %s "
                "AND single_valued = true AND status = 'active' AND upper_inf(recorded_range) "
                "ORDER BY lower(valid_range) DESC, id LIMIT 5"
            ).format(v=sql.Identifier(schema_name, "memory_version")),
            (user_id, subject_key, predicate_key),
        )
        rows = await cursor.fetchall()
    normalized = " ".join(candidate_content.lower().split())
    for version_id, content in rows:
        if " ".join(content.lower().split()) == normalized:
            return "duplicate", version_id
    return (None, rows[0][0] if rows else None)


# Injected model-backed classifier; absent by default so the gate stays provider-free.
type RelationClassifier = Callable[[GateInput, Sequence[str]], ConflictRelation]
