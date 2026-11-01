"""The two admission policies: ``ground()`` and ``assess_soundness()`` (E-015; V2-D02, V2-D40, V2-D41).

V2 admits two kinds of artifact under two different rules, replacing v1's single gate:

* :func:`ground` is for ``observed``/``asserted`` artifacts: the v1 write gate (``engine/gate.py::evaluate``)
  generalised from chat turns to any *declared* source type. For ``turn`` evidence it returns exactly the v1
  verdicts (``tests/test_admission.py`` proves it); non-turn evidence is judged by ``grounding.sources`` and fails
  closed with a specific, static reason code.
* :func:`assess_soundness` is for ``inferred`` artifacts. Nothing is grounded; admission rests on structural checks
  plus the three-state ledger ``(support, counter, undetermined)`` and the finite-sample bound
  ``confidence_lower = 1 - CP_upper(k + u, o)`` (the r3 correction: undetermined outcomes count as failures).

Both are **pure** (no I/O, no model call: ``I12``). :func:`record_soundness_assessment` is the only database effect
here, mirroring v1's ``gate.evaluate`` / ``gate.record_decision`` split.

**Where the bound gates (open question (a) of E-015, decided by ``V2-D40``).** ``STATISTICS.md`` 4.1 point 3: a
candidate must be *stored* to be tested against held-out data, so the bound gates **verification and rendering in
``facts``**, not creation. The structural checks gate *creation*. Both verdicts are separate outputs of
:class:`SoundnessAssessment`, so a change of that decision changes wiring, not formulas.

``tau_confidence`` and ``tau_coverage`` have **no defaults here**: they are decided in ``V2-D30`` (open until W4), so
a caller must say which it is using. (``soundness.admission.assess`` keeps documented defaults for its own tests;
this entry point does not inherit them.)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.engine import gate
from custom_memory_engine.engine.gate import AssertionMode, ConflictRelation, GateCheck
from custom_memory_engine.grounding.sources import Evidence, SourceRecord, canonical_hash
from custom_memory_engine.grounding.sources import ground as ground_evidence
from custom_memory_engine.model.axes import Kind, Lifecycle
from custom_memory_engine.security.audit_guard import content_free_details
from custom_memory_engine.soundness.admission import (
    AdmissionError,
    Assessment,
    Ledger,
    assess,
    cp_upper,
)

DEFAULT_CHECKS: tuple[GateCheck, ...] = ("evidence", "assertion_mode", "instruction_screen", "conflict", "confidence")
GROUNDED_KINDS: tuple[Kind, ...] = ("episode", "claim", "procedure")


# --- grounding -----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GroundingCandidate:
    """One extracted candidate and the evidence it claims to rest on, of any declared source type."""

    version_id: str
    user_id: str
    kind: Kind
    content: str
    evidence: tuple[Evidence, ...]
    assertion_mode: AssertionMode = "asserted"
    predicate_key: str = ""
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class GroundingVerdict:
    """The v1 verdict shape plus the V2 lifecycle and the grounded sources. ``reason_code`` is static."""

    decision: gate.Decision
    reason_code: str
    checked: tuple[str, ...]
    lifecycle: Lifecycle | None  # accept -> observed/asserted, hold -> contested, reject -> None
    predicate_key: str | None = None
    relation: ConflictRelation | None = None
    sources: tuple[SourceRecord, ...] = ()


def _lifecycle(kind: Kind, decision: gate.Decision) -> Lifecycle | None:
    if decision == "reject":
        return None
    if decision == "hold":
        return "contested"
    return "observed" if kind == "episode" else "asserted"


_UNCONFIRMED = "unconfirmed_assistant_claim"


def _ground_evidence(
    evidence: Sequence[Evidence], *, now: datetime, max_external_age: timedelta
) -> tuple[str | None, tuple[SourceRecord, ...]]:
    """Return ``(reject_reason, sources)``.

    **Turn evidence uses v1's own check** (``gate.evidence_supported``), so a turn-only candidate gets exactly the
    v1 verdict. That check treats the spans and the turns as two collections: a span is supported when *any* user
    turn contains it. ``grounding.sources.ground`` is stricter (each item against its own turn); it is applied to
    every non-turn item, which has no v1 behaviour to preserve. The difference is reported in the pull request.
    """
    turns = [e for e in evidence if e.source_type == "turn"]
    others = [e for e in evidence if e.source_type != "turn"]
    if not evidence:
        return _UNCONFIRMED, ()
    records: list[SourceRecord] = []
    if turns:
        if not gate.evidence_supported([t.span for t in turns], [(t.role or "", t.content) for t in turns]):
            return _UNCONFIRMED, ()
        records.extend(SourceRecord("turn", t.ref, t.span, canonical_hash(t.content), None, True) for t in turns)
    if others:
        result = ground_evidence(others, now=now, max_external_age=max_external_age)
        if not result.grounded:
            return result.reasons[0], ()
        records.extend(result.sources)
    return None, tuple(records)


def ground(
    candidate: GroundingCandidate,
    *,
    now: datetime,
    max_external_age: timedelta,
    relation: ConflictRelation | None = None,
    calibration: Mapping[str, float] | None = None,
    enabled: Sequence[GateCheck] = DEFAULT_CHECKS,
) -> GroundingVerdict:
    """Run the five v1 checks over any declared source type.

    ``A3`` (``enabled=()``) and ``A4`` (individual checks disabled) stay available, because hypothesis ``S3`` is
    ``P - A3``. The evidence check runs first, as in v1; the remaining four checks are the v1 gate unchanged.
    """
    if candidate.kind not in GROUNDED_KINDS:
        raise AdmissionError("only_episodes_claims_and_procedures_are_grounded")
    checked: list[str] = []
    sources: tuple[SourceRecord, ...] = ()

    if "evidence" in enabled:
        checked.append("evidence")
        reason, sources = _ground_evidence(candidate.evidence, now=now, max_external_age=max_external_age)
        if reason is not None:
            return GroundingVerdict("reject", reason, tuple(checked), None)

    rest: tuple[GateCheck, ...] = tuple(check for check in enabled if check != "evidence")
    verdict = gate.evaluate(
        gate.GateInput(
            version_id=candidate.version_id,
            user_id=candidate.user_id,
            content=candidate.content,
            evidence_spans=(),
            evidence_turns=(),
            assertion_mode=candidate.assertion_mode,
            predicate_key=candidate.predicate_key,
            confidence=candidate.confidence,
        ),
        relation=relation,
        calibration=calibration,
        enabled=rest,
    )
    return GroundingVerdict(
        verdict.decision,
        verdict.reason_code,
        (*checked, *verdict.checked),
        _lifecycle(candidate.kind, verdict.decision),
        verdict.predicate_key,
        verdict.relation,
        sources if verdict.decision != "reject" else (),
    )


# --- soundness -----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MethodRef:
    """A reference to a registered inference method (``METHOD``, ``E-014``)."""

    method_id: str
    version: str
    params_hash: str


@dataclass(frozen=True, slots=True)
class LedgerCounts:
    """``(s, k, u)``. Unlike :class:`soundness.admission.Ledger`, ``o = 0`` is allowed and means "no evidence"."""

    support: int
    counter: int
    undetermined: int

    def __post_init__(self) -> None:
        for value in (self.support, self.counter, self.undetermined):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AdmissionError("ledger_counts_must_be_nonnegative_integers")

    @property
    def opportunity(self) -> int:
        return self.support + self.counter + self.undetermined


@dataclass(frozen=True, slots=True)
class SoundnessCandidate:
    source_ids: tuple[str, ...]
    method: MethodRef
    ledger: LedgerCounts
    refutation_attempts: int
    negative_control_clean: bool


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    name: str
    passed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CreationVerdict:
    """Whether the candidate may be stored as an ``inferred`` hypothesis. Structural checks only."""

    allowed: bool
    lifecycle: Lifecycle | None
    channel: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerificationVerdict:
    """Whether the evidence would let the artifact be *verified* (and so rendered in ``facts`` with its bound)."""

    admissible_by_bound: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SoundnessAssessment:
    checks: tuple[CheckOutcome, ...]
    opportunity: int
    coverage: float | None
    confidence_lower: float | None
    failure_upper: float | None
    resolved_only_failure_rate: float | None  # labelled diagnostic k/(s+k); it never decides anything
    insufficient_evidence: bool
    creation: CreationVerdict
    verification: VerificationVerdict


def _check_method(method: MethodRef, registered: Callable[[MethodRef], bool]) -> CheckOutcome:
    fields = (method.method_id, method.version, method.params_hash)
    if not all(isinstance(f, str) and f.strip() for f in fields):
        return CheckOutcome("method_registered", False, "method_reference_incomplete")
    if not registered(method):
        return CheckOutcome("method_registered", False, "method_not_registered")
    return CheckOutcome("method_registered", True)


def assess_soundness(
    candidate: SoundnessCandidate,
    *,
    tau_confidence: float,
    tau_coverage: float,
    method_registered: Callable[[MethodRef], bool],
) -> SoundnessAssessment:
    """Apply the soundness policy. ``tau_confidence`` and ``tau_coverage`` are required (``V2-D30`` is open)."""
    attempts = candidate.refutation_attempts
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise AdmissionError("refutation_attempts_invalid")
    if not isinstance(candidate.negative_control_clean, bool):
        raise AdmissionError("negative_control_flag_must_be_boolean")
    for tau in (tau_confidence, tau_coverage):
        if not isinstance(tau, float) or not 0 < tau < 1:
            raise AdmissionError("threshold_out_of_range")

    counts = candidate.ledger
    o = counts.opportunity
    checks = (
        CheckOutcome(
            "has_source",
            bool(candidate.source_ids) and all(isinstance(s, str) and s for s in candidate.source_ids),
            None if candidate.source_ids else "at_least_one_source_required",
        ),
        _check_method(candidate.method, method_registered),
        CheckOutcome("ledger_recorded", o > 0, None if o > 0 else "insufficient_evidence"),
    )
    creation_reasons = tuple(c.reason for c in checks if not c.passed and c.reason is not None)
    creation = (
        CreationVerdict(True, "inferred", "hypotheses", ())
        if not creation_reasons
        else CreationVerdict(False, None, None, creation_reasons)
    )

    if o == 0:
        return SoundnessAssessment(
            checks, 0, None, None, None, None, True, creation, VerificationVerdict(False, ("insufficient_evidence",))
        )

    # The one place `confidence_lower` is computed: k + u in the numerator, always (the r3 correction).
    bounded: Assessment = assess(
        Ledger(counts.support, counts.counter, counts.undetermined),
        refutation_attempts=attempts,
        negative_control_clean=candidate.negative_control_clean,
        tau_confidence=tau_confidence,
        tau_coverage=tau_coverage,
    )
    resolved = counts.support + counts.counter
    diagnostic = counts.counter / resolved if resolved else None
    return SoundnessAssessment(
        checks,
        o,
        bounded.coverage,
        bounded.confidence_lower,
        bounded.failure_upper,
        diagnostic,
        False,
        creation,
        VerificationVerdict(bounded.admissible, bounded.reasons),
    )


# --- recording (the only database effect) --------------------------------------------------------------


async def record_soundness_assessment(
    connection: AsyncConnection,
    *,
    user_id: str,
    candidate: SoundnessCandidate,
    assessment: SoundnessAssessment,
    tau_confidence: float,
    tau_coverage: float,
    schema_name: str = "cme_engine",
) -> str:
    """Persist one content-free ``audit_event`` for a soundness decision and return its id.

    The details contain counts, thresholds, reason codes and the method reference; never statement text, source
    content or an evidence span (the table's own ``CHECK`` forbids those keys). The caller owns the transaction and
    ``app.user_id``.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        row = await cursor.fetchone()
        if row is None or row[0] != user_id:
            raise InsufficientPrivilege("admission_user_context_mismatch")
        event_id = f"audit-{uuid4().hex}"
        details = {
            "policy": "soundness",
            "source_count": len(candidate.source_ids),
            "method_id": candidate.method.method_id,
            "method_version": candidate.method.version,
            "params_hash": candidate.method.params_hash,
            "support": candidate.ledger.support,
            "counter": candidate.ledger.counter,
            "undetermined": candidate.ledger.undetermined,
            "opportunity": assessment.opportunity,
            # floats are not audit-safe values (RL-072); six decimals as an identifier-shaped token
            "coverage": f"{assessment.coverage:.6f}",
            "confidence_lower": f"{assessment.confidence_lower:.6f}",
            "tau_confidence": f"{tau_confidence:.6f}",
            "tau_coverage": f"{tau_coverage:.6f}",
            "creation_allowed": assessment.creation.allowed,
            "creation_reasons": list(assessment.creation.reasons),
            "admissible_by_bound": assessment.verification.admissible_by_bound,
            "verification_reasons": list(assessment.verification.reasons),
        }
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                "VALUES (%s, %s, %s, 'soundness_assessed', pg_catalog.transaction_timestamp(), %s)"
            ).format(a=sql.Identifier(schema_name, "audit_event")),
            (event_id, user_id, uuid4().hex, Jsonb(content_free_details(details))),
        )
    return event_id


__all__ = [
    "DEFAULT_CHECKS",
    "AdmissionError",
    "CheckOutcome",
    "CreationVerdict",
    "GroundingCandidate",
    "GroundingVerdict",
    "LedgerCounts",
    "MethodRef",
    "SoundnessAssessment",
    "SoundnessCandidate",
    "VerificationVerdict",
    "assess_soundness",
    "cp_upper",
    "ground",
    "record_soundness_assessment",
]
