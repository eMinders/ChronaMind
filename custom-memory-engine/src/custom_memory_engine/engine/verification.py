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

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.deletion.closure import guard_verification_commit
from custom_memory_engine.security.audit_guard import content_free_details

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


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    """What ``verify`` posts: the six facts `I11` requires, and the outcome they support."""

    held_out_split_id: str
    negative_control_slice_id: str
    support_window: SplitWindow
    held_out_window: SplitWindow
    support_context_key: str
    held_out_context_key: str
    outcome: Outcome

    def __post_init__(self) -> None:
        for name in ("held_out_split_id", "negative_control_slice_id", "support_context_key", "held_out_context_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise VerificationError(f"{name}_required")
        if self.outcome not in OUTCOMES:
            raise VerificationError("outcome_invalid")
        if self.held_out_split_id == self.negative_control_slice_id:
            raise VerificationError("control_slice_must_differ_from_the_split")


def assess_split(record: VerificationRecord) -> str | None:
    """``None`` when the split is disjoint and usable, else the static code that says why not.

    The two checks are the ones `I11` names, and they are checked in a fixed order so a record that fails both reports
    the structural fault (leakage) rather than the temporal one: a split that shares context is not a split at all.
    """
    if record.support_context_key == record.held_out_context_key:
        return "split_shares_a_context_key"
    if (
        record.support_window.start < record.held_out_window.end
        and record.held_out_window.start < record.support_window.end
    ):
        return "split_windows_overlap"
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
    schema_name: str = "cme_engine",
    epoch_start: int | None = None,
) -> str:
    """Record one verification and move the ledger's status. Returns the verification id.

    Refusals, all ``precondition_failed`` at the boundary: a target that is not an ``inferred`` abstraction (only those
    carry a ledger, and only those can be re-derived), a split that is not disjoint (``I11``), and a split id already
    used for this artifact (re-running one split is not verification).

    ``epoch_start`` is the deletion epoch the call began under. When it is supplied the commit is serialised against a
    concurrent deletion (``F5``) and a verification prepared before one cannot be recorded after it — which matters
    precisely here, because this row is what lets a belief stay ``verified``.
    """
    if epoch_start is not None:
        await guard_verification_commit(connection, user_id, epoch_start, schema_name=schema_name)
    kind, lifecycle, _method = await _own_artifact(connection, user_id, artifact_id, schema_name)
    if kind != "abstraction" or lifecycle != "inferred":
        raise VerificationError("verification_requires_an_inferred_abstraction")
    if (code := assess_split(record)) is not None:
        raise VerificationError(code)
    verification_id = f"ver-{uuid4().hex}"
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT EXISTS (SELECT 1 FROM {t} WHERE user_id = %s AND version_id = %s AND held_out_split_id = %s)"
            ).format(t=sql.Identifier(schema_name, "verification")),
            (user_id, artifact_id, record.held_out_split_id),
        )
        exists = await cursor.fetchone()
        if exists is not None and exists[0]:
            raise VerificationError("split_already_verified")
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {t} (id, version_id, user_id, held_out_split_id, verified_on, "
                "negative_control_slice_id, support_window, held_out_window, support_context_key, "
                "held_out_context_key, outcome) "
                "VALUES (%s, %s, %s, %s, pg_catalog.transaction_timestamp(), %s,"
                " tstzrange(%s, %s, '[)'), tstzrange(%s, %s, '[)'), %s, %s, %s)"
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
                # Content-free (V2-D54): the outcome code and nothing else. A split id is caller-supplied text and the
                # verification row already carries it; an audit table that accumulated it would be a content store.
                Jsonb(content_free_details({"reason_code": record.outcome})),
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
