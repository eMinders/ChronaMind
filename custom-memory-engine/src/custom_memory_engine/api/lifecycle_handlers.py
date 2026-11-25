"""The refutation path (``C-026``): a belief the engine inferred, retired by the evidence against it.

The lifecycle in migration 7 has five states and, until this module, four of them were reachable through the API:
``observed`` and ``asserted`` are written by ``assert``/``ingest_episode``, ``contested`` is opened by a contradiction
and resolved by ``adjudicate``, and ``retracted`` is what ``retract_own`` produces. ``refute`` was in the contract
(``API_V2.md`` §3, Tier B, transitions ``inferred -> contested | retired``) with **no handler**: a belief the engine
derived could be argued with only by an administrator, and the semantic ingestor's contradictions could only ever be
*staged*, never retired. That is the gap this closes.

**Why it is Tier B and why that is not unsafe.** Refutation is the one lifecycle change a caller may make against an
artifact it did not write, so it is fenced three ways: the contract requires ``own_lineage_only`` (the artifact must be
one this principal's own method or assertion produced), ``targets_readable`` (a grant cannot be used to retire someone
else's memory), and ``evidence_required`` (the call must carry counter-evidence). It also only ever moves an artifact
**out of** ``inferred``: an ``asserted`` claim is the user's own word and is retracted by the user, not refuted by a
model's opinion.

**What is recorded, and what is deliberately not.** The retirement is recorded as: the recorded-time close (branch (S)
of ``I10``, exactly as ``retract_own`` does it), the archive row, and one content-free audit event carrying the reason
code and how many pieces of counter-evidence were supplied. The counter-evidence *references* are not copied into the
audit row: a ref can be a file path or a URL, and an audit table that accumulates them is a content store with no
deletion path (``V2-D54``, ``THREAT_MODEL.md`` T-32). A ref that names a readable episode of this principal is instead
linked as a ``contradicts`` association, so the justification is traversable *and* inside the deletion closure.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.api.entry import Call, Engine, EntryError
from custom_memory_engine.api.handlers import Handlers
from custom_memory_engine.api.runtime import Receipt, new_id, now
from custom_memory_engine.clock import allocate_clock
from custom_memory_engine.deletion.closure import current_deletion_epoch
from custom_memory_engine.engine.transactions import archive_version
from custom_memory_engine.engine.verification import (
    VerificationError,
    VerificationRecord,
    record_verification,
    windows_from,
)
from custom_memory_engine.security.audit_guard import content_free_details

_CODE = re.compile(r"[a-z0-9_]{1,64}")


@dataclass(frozen=True, slots=True)
class Constraint:
    """One negative constraint, as ``constraints`` reports it: the element's names and two instants, never content."""

    constraint_id: str
    scope_path: str
    predicate_key: str
    method_family: str
    dissented_at: datetime
    lifted_at: datetime | None
    lift_evidence_version_id: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class ConstraintList:
    """A page of the caller's own constraints, active first (``V2-D105``)."""

    constraints: tuple[Constraint, ...]

    def __len__(self) -> int:
        return len(self.constraints)

    def __iter__(self) -> Iterator[Constraint]:
        return iter(self.constraints)
_SOURCE_TYPES = frozenset({"turn", "tool", "file", "paper", "event", "external"})


def _reason(call: Call) -> str:
    """The retirement reason: a declared code, never free text (audit rows are content-free)."""
    value = call.arguments.get("reason")
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        raise EntryError("validation_error", "reason_must_be_a_code")
    return value


def _counter_evidence(call: Call) -> tuple[Mapping[str, object], ...]:
    """Validate the shape of the counter-evidence without reading or storing it.

    The entry point's ``evidence_required`` condition has already refused an empty or non-sequence value. What is left
    is the element shape, which is the same ``SourceRef`` shape ``assert`` accepts, so a client sends one kind of
    citation to both operations.
    """
    value = call.arguments.get("counter_evidence")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or len(value) > 32:
        raise EntryError("validation_error", "counter_evidence_invalid")
    refs: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise EntryError("validation_error", "counter_evidence_invalid")
        if item.get("type") not in _SOURCE_TYPES:
            raise EntryError("validation_error", "counter_evidence_source_type_invalid")
        ref = item.get("ref")
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 256 or "\x00" in ref:
            raise EntryError("validation_error", "counter_evidence_ref_invalid")
        refs.append(item)
    return tuple(refs)


class LifecycleHandlers:
    """``refute``, ``dissent`` and ``verify``: the three ways a belief the engine derived is answered.

    ``refute`` is Tier B and evidenced; ``dissent`` is Tier C and unevidenced (the user is the authority over their own
    model, ``USER_MODEL.md`` §3); ``verify`` records the held-out check that lets a status say ``verified``.
    """

    def __init__(self, handlers: Handlers) -> None:
        self.runtime = handlers.runtime

    async def refute(self, call: Call) -> Receipt:
        artifact_id, user = str(call.arguments["artifact_id"]), call.ctx.principal
        reason = _reason(call)
        refs = _counter_evidence(call)
        async with self.runtime.session(user) as connection:
            _, lifecycle, _ = await self.own_version(connection, user, artifact_id)
            if lifecycle == "retired":
                raise EntryError("precondition_failed", "already_retired")
            if lifecycle != "inferred":
                # An `asserted` belief is the user's own word (`V2-D48` C-P1): `retract_own` withdraws it, and a
                # model's disagreement is a contradiction for adjudication, not a retirement.
                raise EntryError("precondition_failed", "artifact_not_inferable")
            tick = await allocate_clock(connection, user, schema_name=self.runtime.schema_name)
            await self._close(connection, user, artifact_id)
            await archive_version(
                connection, user_id=user, version_id=artifact_id, schema_name=self.runtime.schema_name, clock_tick=tick
            )
            linked = await self._link_counter_evidence(connection, user, artifact_id, refs)
            await self._audit(connection, user, reason, len(refs), linked)
            return Receipt(artifact_id, "retired", await now(connection))

    async def dissent(self, call: Call) -> Receipt:
        """The user rejects an inference: ``inferred -> retired`` plus the constraint that stops it re-learning.

        ``API_V2.md`` §4.6 and ``V2-D46``: Tier C only (TB2 — a cognition service must not dissent on the user's
        behalf), no evidence required, and the rejection is **recorded as a constraint** rather than only as a
        retirement. That is the difference between this and ``refute``: a refutation says "this is wrong", a dissent
        says "stop deriving this", and ``I16`` makes the second one stick —
        :meth:`api.infer_handlers.InferHandlers.infer` consults :meth:`active_constraint` and answers
        ``negative_constraint_active`` instead of writing the row again.

        The constraint's key is the triple ``(scope, predicate, method family)`` the schema enforces as unique while
        active, and the family is the method's *name* — versions of one method are one family, which is what makes the
        rejection survive a method upgrade. Lifting it needs **new grounded evidence** and is :meth:`lift`
        (``V2-D105``): ``I16`` says a denied element cannot be re-inferred *without* new evidence, which is a statement
        about what a lift requires, not a statement that a dissent is permanent. Until this module, it was.
        """
        artifact_id, user = str(call.arguments["artifact_id"]), call.ctx.principal
        reason = _optional_code(call.arguments.get("note"))
        async with self.runtime.session(user) as connection:
            _, lifecycle, _ = await self.own_version(connection, user, artifact_id)
            if lifecycle == "retired":
                raise EntryError("precondition_failed", "already_retired")
            if lifecycle != "inferred":
                raise EntryError("precondition_failed", "artifact_not_inferable")
            element = await self._dissented_element(connection, user, artifact_id)
            tick = await allocate_clock(connection, user, schema_name=self.runtime.schema_name)
            await self._close(connection, user, artifact_id)
            await archive_version(
                connection, user_id=user, version_id=artifact_id, schema_name=self.runtime.schema_name, clock_tick=tick
            )
            await self._record_constraint(connection, user, artifact_id, element, reason)
            await self._event(
                connection, user, artifact_id, "lifecycle_dissented", {"reason_code": reason or "dissent"}
            )
            return Receipt(artifact_id, "retired", await now(connection))

    async def lift(self, call: Call) -> Receipt:
        """Reopen a dissented element, but only on new grounded evidence (``I16``, ``V2-D105``).

        ``I16`` is precise about the shape of the undo: *without new grounded evidence*. So a lift is not a second
        opinion and not a change of mind — it names an artifact the caller already holds, that artifact must be
        **grounded** (``observed`` or ``asserted``, never ``inferred``: an inference cannot lift a constraint on
        inference, which would make the constraint vacuous), and the schema's
        ``negative_constraint_lift_is_complete`` constraint means the three fields move together or not at all.

        **Why the caller names the constraint and not the artifact.** The constraint is the durable thing: it survives
        the retirement, and it survives the *deletion* of the artifact that provoked it (the reference is cleared, the
        constraint stays). Naming the artifact would make a dissent on a since-deleted belief impossible to lift, and
        the element would be blocked for ever with no way to say so. :meth:`constraints` lists them, so an operator can
        always find the id.

        **What is not allowed.** A constraint that is already lifted is refused rather than re-stamped: the lift is a
        record of when the reconsideration happened, and moving it would rewrite history that ``audit_event`` already
        carries (``V2-D54``). A constraint belonging to another principal is ``forbidden``, indistinguishably from one
        that does not exist.
        """
        user = call.ctx.principal
        constraint_id = call.arguments.get("constraint_id")
        if not isinstance(constraint_id, str) or not constraint_id.strip():
            raise EntryError("validation_error", "constraint_id_required")
        evidence_id = call.arguments.get("evidence")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise EntryError("validation_error", "evidence_artifact_id_required")
        reason = _optional_code(call.arguments.get("note"))
        async with self.runtime.session(user) as connection:
            element = await self._active_constraint_element(connection, user, constraint_id)
            await self._require_grounded_evidence(connection, user, evidence_id)
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql.SQL(
                        "UPDATE {t} SET lifted_at = pg_catalog.transaction_timestamp(), lifted_by = %s,"
                        " lift_evidence_version_id = %s"
                        " WHERE id = %s AND user_id = %s AND lifted_at IS NULL"
                    ).format(t=self.runtime.table("negative_constraint")),
                    (user, evidence_id, constraint_id, user),
                )
                if cursor.rowcount != 1:
                    # The row was lifted between the read and this statement. Refused rather than re-stamped: the
                    # first lift is the record, and a second one would claim a reconsideration that did not happen.
                    raise EntryError("precondition_failed", "constraint_already_lifted")
            await self._event(
                connection,
                user,
                constraint_id,
                "constraint_lifted",
                {"reason_code": reason or "user_lift", "method_family": element[2], "method_id": element[3]},
            )
            return Receipt(constraint_id, "lifted", await now(connection))

    async def constraints(self, call: Call) -> ConstraintList:
        """The caller's own negative constraints, active first: the read that makes a dissent *liftable*.

        Without it an operator could hold a permanent block and no way to name it. What is returned is
        identifiers only — the element's predicate and method *family*, which is a registered name, and the scope,
        which the caller owns — never content (``V2-D54``).
        """
        user = call.ctx.principal
        include_lifted = call.arguments.get("include_lifted", False)
        if not isinstance(include_lifted, bool):
            raise EntryError("validation_error", "include_lifted_must_be_a_boolean")
        async with self.runtime.session(user) as connection:
            async with connection.cursor(row_factory=tuple_row) as cursor:
                await cursor.execute(
                    sql.SQL(
                        "SELECT id, scope_path, predicate_key, method_family, dissented_at, lifted_at,"
                        " lift_evidence_version_id, reason"
                        " FROM {t} WHERE user_id = %s AND (%s OR lifted_at IS NULL)"
                        " ORDER BY lifted_at IS NOT NULL, dissented_at DESC, id"
                    ).format(t=self.runtime.table("negative_constraint")),
                    (user, include_lifted),
                )
                rows = await cursor.fetchall()
        return ConstraintList(
            tuple(
                Constraint(
                    constraint_id=str(row[0]),
                    scope_path=str(row[1]),
                    predicate_key=str(row[2]),
                    method_family=str(row[3]),
                    dissented_at=row[4],
                    lifted_at=row[5],
                    lift_evidence_version_id=str(row[6]) if row[6] is not None else None,
                    reason=str(row[7]),
                )
                for row in rows
            )
        )

    async def verify(self, call: Call) -> Receipt:
        """Post a held-out verification result and let the ledger's status move (``I11``).

        The split's disjointness is decided by :func:`engine.verification.assess_split` before anything is written: a
        leaky split is refused, because the status it would justify would be a claim the data does not support. The
        commit is fenced against a concurrent deletion at the epoch this call observed, so a verification prepared
        before a deletion cannot land after it (``F5``).
        """
        artifact_id, user = str(call.arguments["artifact_id"]), call.ctx.principal
        record = _verification_record(call.arguments.get("verification"))
        async with self.runtime.session(user) as connection:
            _, lifecycle, _ = await self.own_version(connection, user, artifact_id)
            if lifecycle != "inferred":
                raise EntryError("precondition_failed", "artifact_not_inferable")
            epoch = await current_deletion_epoch(connection, user, schema_name=self.runtime.schema_name)
            try:
                await record_verification(
                    connection,
                    user_id=user,
                    artifact_id=artifact_id,
                    record=record,
                    schema_name=self.runtime.schema_name,
                    epoch_start=epoch,
                )
            except VerificationError as exc:
                raise EntryError("precondition_failed", exc.code) from exc
            return Receipt(artifact_id, record.outcome, await now(connection))

    # -- dissent's constraint ----------------------------------------------------------------------------------

    async def _dissented_element(
        self, connection: AsyncConnection, user: str, artifact_id: str
    ) -> tuple[str, str, str]:
        """``(scope_path, predicate_key, method_family)`` for an inferred artifact: what the constraint blocks."""
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT v.scope_path, v.predicate_key, m.name FROM {v} AS v"
                    " JOIN {m} AS m ON m.id = v.method_id"
                    " WHERE v.id = %s AND v.user_id = %s"
                ).format(v=self.runtime.table("memory_version"), m=self.runtime.table("method")),
                (artifact_id, user),
            )
            row = await cursor.fetchone()
        if row is None:
            # An inferred artifact without a registered method cannot exist (I1'); if it somehow does, the dissent
            # cannot be recorded against a family it does not name, and guessing one would block the wrong element.
            raise EntryError("precondition_failed", "method_family_unknown")
        return str(row[0]), str(row[1]), str(row[2])

    async def _record_constraint(
        self,
        connection: AsyncConnection,
        user: str,
        artifact_id: str,
        element: tuple[str, str, str],
        reason: str | None,
    ) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {t} (id, user_id, predicate_key, scope_path, method_family, dissented_at, reason,"
                    " dissented_version_id) "
                    "VALUES (%s, %s, %s, %s, %s, pg_catalog.transaction_timestamp(), %s, %s) "
                    "ON CONFLICT (user_id, scope_path, predicate_key, method_family) WHERE lifted_at IS NULL"
                    " DO NOTHING"
                ).format(t=self.runtime.table("negative_constraint")),
                (
                    new_id("nc"),
                    user,
                    element[1],
                    element[0],
                    element[2],
                    reason or "user_dissent",
                    artifact_id,
                ),
            )

    async def _active_constraint_element(
        self, connection: AsyncConnection, user: str, constraint_id: str
    ) -> tuple[str, str, str, str | None]:
        """``(scope, predicate, method family, dissented version)`` of an *active* constraint of this user."""
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT scope_path, predicate_key, method_family, dissented_version_id"
                    " FROM {t} WHERE id = %s AND user_id = %s AND lifted_at IS NULL"
                ).format(t=self.runtime.table("negative_constraint")),
                (constraint_id, user),
            )
            row = await cursor.fetchone()
        if row is None:
            # Absent, another principal's, and already-lifted are one answer: `constraint_already_lifted` is only
            # reachable from the update's own rowcount, which cannot race a row that never existed.
            raise EntryError("precondition_failed", "constraint_not_active")
        return str(row[0]), str(row[1]), str(row[2]), (str(row[3]) if row[3] is not None else None)

    async def _require_grounded_evidence(self, connection: AsyncConnection, user: str, artifact_id: str) -> None:
        """The lift's ground must be a grounded artifact of the caller's that still exists.

        ``grounded`` is the whole point: an ``inferred`` artifact is the engine's own guess, and letting a guess lift a
        constraint on inference would let the engine reverse a user's denial by inferring the same thing twice. The
        schema enforces the *same-tenant* part of this through the foreign key; the lifecycle half is enforced here.
        """
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL("SELECT lifecycle FROM {t} WHERE id = %s AND user_id = %s").format(
                    t=self.runtime.table("memory_version")
                ),
                (artifact_id, user),
            )
            row = await cursor.fetchone()
        if row is None:
            raise EntryError("precondition_failed", "evidence_not_readable")
        if str(row[0]) not in {"asserted", "observed"}:
            raise EntryError("precondition_failed", "evidence_not_grounded")

    async def active_constraint(
        self,
        connection: AsyncConnection,
        user: str,
        *,
        scope_path: str,
        predicate_key: str,
        method_family: str,
    ) -> bool:
        """Whether an un-lifted constraint blocks this element (`I16`). Read by ``infer``, never by a read path."""
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT EXISTS (SELECT 1 FROM {t} WHERE user_id = %s AND scope_path = %s AND predicate_key = %s"
                    " AND method_family = %s AND lifted_at IS NULL)"
                ).format(t=self.runtime.table("negative_constraint")),
                (user, scope_path, predicate_key, method_family),
            )
            row = await cursor.fetchone()
        return bool(row is not None and row[0])

    async def _event(
        self,
        connection: AsyncConnection,
        user: str,
        artifact_id: str,
        event_type: str,
        details: dict[str, object],
    ) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                    "VALUES (%s, %s, %s, %s, pg_catalog.transaction_timestamp(), %s)"
                ).format(a=self.runtime.table("audit_event")),
                (new_id("audit"), user, artifact_id, event_type, Jsonb(content_free_details(details))),
            )

    # -- helpers -----------------------------------------------------------------------------------------------

    async def own_version(self, connection: AsyncConnection, user: str, artifact_id: str) -> tuple[str, str, str]:
        """The artifact's ``(kind, lifecycle, status)``; ``forbidden`` if absent, or unreadable.

        Deliberately not ``Handlers._own_version``: that is a private method of a class this module composes rather
        than inherits, and reaching into it would make the two drift independently.
        """
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL("SELECT kind, lifecycle, status FROM {t} WHERE id = %s AND user_id = %s").format(
                    t=self.runtime.table("memory_version")
                ),
                (artifact_id, user),
            )
            row = await cursor.fetchone()
        if row is None:
            raise EntryError("forbidden")
        return str(row[0]), str(row[1]), str(row[2])

    async def _close(self, connection: AsyncConnection, user: str, artifact_id: str) -> None:
        """Close the recorded-time range: branch (S) of ``I10``, byte-identical to ``retract_own``'s close."""
        async with connection.cursor() as cursor:
            await cursor.execute(
                sql.SQL(
                    "UPDATE {t} SET recorded_range = tstzrange(lower(recorded_range), clock_now()) "
                    "WHERE id = %s AND user_id = %s AND upper_inf(recorded_range)"
                ).format(t=self.runtime.table("memory_version")),
                (artifact_id, user),
            )

    async def _link_counter_evidence(
        self,
        connection: AsyncConnection,
        user: str,
        artifact_id: str,
        refs: Sequence[Mapping[str, object]],
    ) -> int:
        """Link every counter-evidence ref that names one of this principal's readable versions.

        A ``turn`` ref is the episode id, which is also the version id (``ingest_episode`` returns it), so this is the
        common case. A ref that names nothing the caller can read is **not** an error: a counter-example may live
        outside the engine (a file, a URL, another system's row), and refusing it would make refutation impossible for
        exactly the artifacts most worth refuting. It is counted in the audit event instead.
        """
        linked = 0
        for item in refs:
            version_id = str(item["ref"])
            async with connection.cursor(row_factory=tuple_row) as cursor:
                await cursor.execute(
                    sql.SQL("SELECT 1 FROM {t} WHERE id = %s AND user_id = %s").format(
                        t=self.runtime.table("memory_version")
                    ),
                    (version_id, user),
                )
                if await cursor.fetchone() is None:
                    continue
                # The `contradicts` association is written in the orientation the read path expects: the *counter*
                # evidence is the from-side, so `open_contradiction` (V2-D48 C-P4) reads it as "this contradicts the
                # belief", and the pair is unique per (user, from, to, type) so a replay adds nothing.
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {a} (id, user_id, from_id, to_id, type, weight, created_by, origin) "
                        "VALUES (%s, %s, %s, %s, 'contradicts', 1.0, %s, 'tier_c') "
                        "ON CONFLICT (user_id, from_id, to_id, type) DO NOTHING"
                    ).format(a=self.runtime.table("association")),
                    (new_id("assoc"), user, version_id, artifact_id, user),
                )
                linked += 1
        return linked

    async def _audit(self, connection: AsyncConnection, user: str, reason: str, supplied: int, linked: int) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                    "VALUES (%s, %s, %s, 'lifecycle_refuted', pg_catalog.transaction_timestamp(), %s)"
                ).format(a=self.runtime.table("audit_event")),
                (
                    new_id("audit"),
                    user,
                    new_id("ref"),
                    # Counts and a declared code only: no ref, no path, no text (V2-D54).
                    Jsonb(
                        content_free_details(
                            {
                                "reason_code": reason,
                                "counter_evidence_supplied": supplied,
                                "counter_evidence_linked": linked,
                            }
                        )
                    ),
                ),
            )


def _optional_code(value: object) -> str | None:
    """A declared reason code, or ``None``. Free text is refused: audit rows are content-free (``V2-D54``)."""
    if value is None:
        return None
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        raise EntryError("validation_error", "note_must_be_a_code")
    return value


def _verification_record(raw: object) -> VerificationRecord:
    """Validate the ``verification`` argument into a :class:`VerificationRecord`.

    ``API_V2.md`` gives ``verification`` one opaque argument, so its shape is this operation's own — the same way
    ``assert``'s ``evidence`` and ``ingest_artifact``'s ``candidate`` are. It carries exactly the six facts `I11`
    names: the split id, the negative-control slice, both windows and both context keys, plus the outcome.
    """
    if not isinstance(raw, Mapping):
        raise EntryError("validation_error", "verification_invalid")
    try:
        support, held_out = windows_from(raw.get("support_window"), raw.get("held_out_window"))
        record = VerificationRecord(
            held_out_split_id=str(raw.get("held_out_split_id") or ""),
            negative_control_slice_id=str(raw.get("negative_control_slice_id") or ""),
            support_window=support,
            held_out_window=held_out,
            support_context_key=str(raw.get("support_context_key") or ""),
            held_out_context_key=str(raw.get("held_out_context_key") or ""),
            outcome=raw.get("outcome"),  # type: ignore[arg-type]
        )
    except VerificationError as exc:
        raise EntryError("validation_error", exc.code) from exc
    return record


def register_lifecycle_handlers(engine: Engine, handlers: Handlers) -> LifecycleHandlers:
    """Wire the lifecycle operations onto ``engine``; the entry point's contract owns the rest of the guards."""
    lifecycle = LifecycleHandlers(handlers)
    for name, handler in {
        "refute": lifecycle.refute,
        "dissent": lifecycle.dissent,
        "lift": lifecycle.lift,
        "constraints": lifecycle.constraints,
        "verify": lifecycle.verify,
    }.items():
        engine.register(name, handler)
    return lifecycle


__all__ = ["Constraint", "ConstraintList", "LifecycleHandlers", "register_lifecycle_handlers"]
