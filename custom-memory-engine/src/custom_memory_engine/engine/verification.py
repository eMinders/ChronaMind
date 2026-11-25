"""Held-out verification: the record that lets an abstraction's status say ``verified`` (``I11``, ``API_V2.md`` §4.4).

``abstraction_stats.verification_status`` could always *say* ``verified``; `guard_abstraction_stats` refuses the word
unless a matching ``verification`` row exists, and ``verification`` is append-only with no delete path. What was
missing
was the writer: ``verify`` was in the contract with no handler, so `I11` was **PARTIAL** and the status could only ever
be ``unverified``.

**The disjointness algorithm** (the part ``INVARIANT_LEDGER.md`` recorded as "not built"). ``I11`` requires the held-
out
split to be genuinely held out, which means the record must prove two things the schema alone cannot check:

1. the support and held-out **windows** do not overlap — a split that re-checks the same time range measures the same
   evidence twice, and a ``verified`` built on it is a tautology;
2. the two sides do not share a **context key** — a held-out item from the same context is a near-duplicate of a
support
   item, which is the classic leakage that makes a finite-sample bound meaningless.

Either failure is a refusal, not a warning: the outcome is what the status will claim, so a leaky split must not be
recorded at all. Deciding it is deterministic and needs no model, which is why it lives here rather than behind the
extractor seam.

**Nothing is deleted and nothing is edited.** ``verification`` refuses ``UPDATE``, ``DELETE`` and ``TRUNCATE``; the
status
can move ``unverified -> verified|refuted`` but never back (`verification_cannot_be_withdrawn`). A second verification
of
the same artifact is a second record, and it is refused when it renames a split that already exists: re-running one
split
until it agrees is not verification.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Literal
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.deletion.closure import guard_verification_commit
from custom_memory_engine.engine.staleness import compute_closure
from custom_memory_engine.scope.access import ReadSet, row_visible
from custom_memory_engine.security.audit_guard import content_free_details
from custom_memory_engine.soundness.admission import (
    DEFAULT_TAU_CONFIDENCE,
    DEFAULT_TAU_COVERAGE,
    AdmissionError,
    Ledger,
)
from custom_memory_engine.soundness.admission import (
    assess as assess_soundness_admission,
)

type Outcome = Literal["verified", "refuted"]

#: The two outcomes ``abstraction_stats.verification_status`` accepts (the third value, ``unverified``, is the absence
#: of a record and is never posted).
OUTCOMES: tuple[Outcome, ...] = ("verified", "refuted")


class VerificationError(ValueError):
    """A static code; never a split id, a window or a context key."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SplitWindow:
    """One side of a split: when it was measured, and under which context. Half-open ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.start, datetime) or not isinstance(self.end, datetime):
            raise VerificationError("split_window_not_a_datetime")
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise VerificationError("split_window_must_carry_a_timezone")
        if self.end <= self.start:
            raise VerificationError("split_window_is_empty")


def _validate_artifact_tuple(raw: object, role: str) -> tuple[str, ...]:
    if isinstance(raw, str | bytes) or not isinstance(raw, Sequence):
        raise VerificationError(f"{role}_artifact_ids_invalid")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip() or "\x00" in item:
            raise VerificationError(f"{role}_artifact_id_invalid")
        if item in seen:
            raise VerificationError(f"{role}_artifacts_duplicate")
        seen.add(item)
        cleaned.append(item)
    return tuple(cleaned)


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    """What ``verify`` posts: the six facts ``I11`` requires, pinned artifact ids, and the outcome."""

    held_out_split_id: str
    negative_control_slice_id: str
    support_window: SplitWindow
    held_out_window: SplitWindow
    support_context_key: str
    held_out_context_key: str
    outcome: Outcome
    support_artifact_ids: tuple[str, ...] = ()
    held_out_artifact_ids: tuple[str, ...] = ()
    negative_control_clean: bool = True
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE
    tau_coverage: float = DEFAULT_TAU_COVERAGE

    def __post_init__(self) -> None:
        for name in ("held_out_split_id", "negative_control_slice_id", "support_context_key", "held_out_context_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise VerificationError(f"{name}_required")
        if self.outcome not in OUTCOMES:
            raise VerificationError("outcome_invalid")
        if self.held_out_split_id == self.negative_control_slice_id:
            raise VerificationError("control_slice_must_differ_from_the_split")
        object.__setattr__(
            self, "support_artifact_ids", _validate_artifact_tuple(self.support_artifact_ids, "support")
        )
        object.__setattr__(
            self, "held_out_artifact_ids", _validate_artifact_tuple(self.held_out_artifact_ids, "held_out")
        )
        if not isinstance(self.negative_control_clean, bool):
            raise VerificationError("negative_control_clean_invalid")
        for threshold in (self.tau_confidence, self.tau_coverage):
            if isinstance(threshold, bool) or not isinstance(threshold, int | float) or not isfinite(threshold):
                raise VerificationError("threshold_invalid")
            if not 0 < float(threshold) < 1:
                raise VerificationError("threshold_invalid")


def assess_split(record: VerificationRecord) -> str | None:
    """``None`` when the split is disjoint and usable, else the static code that says why not.

    Checks context disjointness, temporal window disjointness, and pinned support/held-out artifact non-emptiness
    and disjointness in fixed order.
    """
    if record.support_context_key == record.held_out_context_key:
        return "split_shares_a_context_key"
    if (
        record.support_window.start < record.held_out_window.end
        and record.held_out_window.start < record.support_window.end
    ):
        return "split_windows_overlap"
    if not record.support_artifact_ids:
        return "support_artifacts_required"
    if not record.held_out_artifact_ids:
        return "held_out_artifacts_required"
    if set(record.support_artifact_ids) & set(record.held_out_artifact_ids):
        return "split_artifacts_overlap"
    return None


async def _own_artifact(
    connection: AsyncConnection, user_id: str, artifact_id: str, schema_name: str
) -> tuple[str, str, str | None]:
    """``(kind, lifecycle, method_id)`` for a readable artifact, or ``verification_target_not_readable``."""
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT kind, lifecycle, method_id FROM {v} WHERE user_id = %s AND id = %s").format(
                v=sql.Identifier(schema_name, "memory_version")
            ),
            (user_id, artifact_id),
        )
        row = await cursor.fetchone()
    if row is None:
        raise VerificationError("verification_target_not_readable")
    return str(row[0]), str(row[1]), None if row[2] is None else str(row[2])


async def record_verification(
    connection: AsyncConnection,
    *,
    user_id: str,
    artifact_id: str,
    record: VerificationRecord,
    read_set: ReadSet | None = None,
    schema_name: str = "cme_engine",
    epoch_start: int | None = None,
) -> str:
    """Record a held-out verification, pin support/held-out artifacts, and enforce admission bounds (``I11``).

    Refused before anything is written if ``artifact_id`` is not an inferred abstraction owned by ``user_id``, if
    :func:`assess_split` finds leakage or missing/overlapping artifact pins, if any pinned artifact is unreadable, if
    ``held_out_split_id`` was already recorded, or if ``outcome == 'verified'`` fails ``soundness.admission``.
    """
    if epoch_start is not None:
        await guard_verification_commit(connection, user_id, epoch_start, schema_name=schema_name)
    kind, lifecycle, _method = await _own_artifact(connection, user_id, artifact_id, schema_name)
    if kind != "abstraction" or lifecycle != "inferred":
        raise VerificationError("verification_requires_an_inferred_abstraction")
    if (code := assess_split(record)) is not None:
        raise VerificationError(code)

    pinned_ids = tuple(dict.fromkeys((*record.support_artifact_ids, *record.held_out_artifact_ids)))
    verification_id = f"ver-{uuid4().hex}"
    admitted_confidence: float | None = None
    verified_method_id: str | None = None
    counter_absence_basis: str | None = None
    closed_over_hash: str | None = None

    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT id, scope_path, visibility FROM {mv} WHERE user_id = %s AND id = ANY(%s)").format(
                mv=sql.Identifier(schema_name, "memory_version")
            ),
            (user_id, list(pinned_ids)),
        )
        readable_ids = {
            str(row[0]) for row in await cursor.fetchall() if read_set is None or row_visible(read_set, row[1], row[2])
        }
        if len(readable_ids) != len(pinned_ids):
            raise VerificationError("verification_artifact_unreadable")

        await cursor.execute(
            sql.SQL(
                "SELECT EXISTS (SELECT 1 FROM {t} WHERE user_id = %s AND version_id = %s AND held_out_split_id = %s)"
            ).format(t=sql.Identifier(schema_name, "verification")),
            (user_id, artifact_id, record.held_out_split_id),
        )
        exists = await cursor.fetchone()
        if exists is not None and exists[0]:
            raise VerificationError("split_already_verified")

        if record.outcome == "verified":
            await cursor.execute(
                sql.SQL(
                    "SELECT s.support_count, s.counter_count, s.undetermined_count,"
                    " s.opportunity_count, s.refutation_attempts, v.method_id"
                    " FROM {s} AS s JOIN {v} AS v ON v.user_id = s.user_id AND v.id = s.version_id"
                    " WHERE s.user_id = %s AND s.version_id = %s"
                ).format(
                    s=sql.Identifier(schema_name, "abstraction_stats"),
                    v=sql.Identifier(schema_name, "memory_version"),
                ),
                (user_id, artifact_id),
            )
            stats_row = await cursor.fetchone()
            if stats_row is None:
                raise VerificationError("verification_target_has_no_ledger")
            support = int(stats_row[0])
            counter = int(stats_row[1])
            undetermined = int(stats_row[2])
            opportunity = int(stats_row[3])
            refutation_attempts = int(stats_row[4])
            verified_method_id = None if stats_row[5] is None else str(stats_row[5])
            try:
                ledger = Ledger(support=support, counter=counter, undetermined=undetermined)
                assessment = assess_soundness_admission(
                    ledger,
                    refutation_attempts=refutation_attempts,
                    negative_control_clean=record.negative_control_clean,
                    tau_confidence=record.tau_confidence,
                    tau_coverage=record.tau_coverage,
                )
            except AdmissionError:
                raise VerificationError("admission_bound_unmet") from None
            if not assessment.admissible:
                raise VerificationError("admission_bound_unmet")
            admitted_confidence = assessment.confidence_lower
            if counter == 0:
                counter_absence_basis = "high_opportunity" if opportunity >= 36 else "low_opportunity"
            closure = await compute_closure(connection, user_id, artifact_id, schema_name=schema_name)
            closed_over_hash = closure.digest

        await cursor.execute(
            sql.SQL(
                "INSERT INTO {t} (id, version_id, user_id, held_out_split_id, verified_on, "
                "negative_control_slice_id, support_window, held_out_window, support_context_key, "
                "held_out_context_key, outcome, artifact_pinning_status) "
                "VALUES (%s, %s, %s, %s, pg_catalog.transaction_timestamp(), %s,"
                " tstzrange(%s, %s, '[)'), tstzrange(%s, %s, '[)'), %s, %s, %s, 'pinned')"
            ).format(t=sql.Identifier(schema_name, "verification")),
            (
                verification_id,
                artifact_id,
                user_id,
                record.held_out_split_id,
                record.negative_control_slice_id,
                record.support_window.start,
                record.support_window.end,
                record.held_out_window.start,
                record.held_out_window.end,
                record.support_context_key,
                record.held_out_context_key,
                record.outcome,
            ),
        )
        # The schema checks this too, but a generated `false` would be a stored lie rather than a refusal; the attempt
        # to record a leaky split must fail loudly at the write, which is why `assess_split` refuses before the insert.
        cursor.rowcount  # noqa: B018 - the row is inserted; the guard above is what makes it a usable split
        va_table = sql.Identifier(schema_name, "verification_artifact")
        for role, artifact_group in (
            ("support", record.support_artifact_ids),
            ("held_out", record.held_out_artifact_ids),
        ):
            for pinned_artifact_id in artifact_group:
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {va} (verification_id, user_id, role, artifact_id) VALUES (%s, %s, %s, %s)"
                    ).format(va=va_table),
                    (verification_id, user_id, role, pinned_artifact_id),
                )

        if record.outcome == "verified":
            await cursor.execute(
                sql.SQL(
                    "UPDATE {t} SET verification_status = %s, confidence = %s, confidence_method_id = %s,"
                    " counter_absence_basis = %s, closed_over_hash = %s"
                    " WHERE user_id = %s AND version_id = %s"
                ).format(t=sql.Identifier(schema_name, "abstraction_stats")),
                (
                    record.outcome,
                    admitted_confidence,
                    verified_method_id,
                    counter_absence_basis,
                    closed_over_hash,
                    user_id,
                    artifact_id,
                ),
            )
        else:
            await cursor.execute(
                sql.SQL("UPDATE {t} SET verification_status = %s WHERE user_id = %s AND version_id = %s").format(
                    t=sql.Identifier(schema_name, "abstraction_stats")
                ),
                (record.outcome, user_id, artifact_id),
            )
        if cursor.rowcount != 1:
            raise VerificationError("verification_target_has_no_ledger")
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                "VALUES (%s, %s, %s, %s, pg_catalog.transaction_timestamp(), %s)"
            ).format(a=sql.Identifier(schema_name, "audit_event")),
            (
                f"audit-{uuid4().hex}",
                user_id,
                artifact_id,
                "verification_recorded",
                # Content-free (V2-D54): the outcome code and pinning status only.
                Jsonb(content_free_details({"reason_code": record.outcome, "pinning_status": "pinned"})),
            ),
        )
    return verification_id


def windows_from(support: object, held_out: object) -> tuple[SplitWindow, SplitWindow]:
    """Parse the request's two windows, which are ``[start, end]`` pairs of ISO instants.

    Shape only: a window that is empty, naive or not two instants is ``validation_error``/``verification_invalid``
    at the
    boundary. Whether the two *overlap* is :func:`assess_split`'s decision, because that is a property of the pair.
    """

    def one(raw: object, name: str) -> SplitWindow:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise VerificationError(f"{name}_invalid")
        start, end = (_instant(raw[0], name), _instant(raw[1], name))
        return SplitWindow(start, end)

    return one(support, "support_window"), one(held_out, "held_out_window")


def _instant(value: object, name: str) -> datetime:
    from datetime import UTC

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise VerificationError(f"{name}_invalid") from None
    else:
        raise VerificationError(f"{name}_invalid")
    if parsed.tzinfo is None:
        raise VerificationError(f"{name}_must_carry_a_timezone")
    return parsed.astimezone(UTC)


#: How far a verification record's clock may run ahead of the engine's own, in the same spirit as `expire`'s bound:
#: a caller cannot date its claim into the future. Kept here so the number has one home.
MAX_CLOCK_SKEW = timedelta(seconds=0)


__all__ = [
    "MAX_CLOCK_SKEW",
    "OUTCOMES",
    "Outcome",
    "SplitWindow",
    "VerificationError",
    "VerificationRecord",
    "assess_split",
    "record_verification",
    "windows_from",
]
