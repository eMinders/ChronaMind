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
from collections.abc import Mapping, Sequence

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.api.entry import Call, Engine, EntryError
from custom_memory_engine.api.handlers import Handlers
from custom_memory_engine.api.runtime import Receipt, new_id, now
from custom_memory_engine.clock import allocate_clock
from custom_memory_engine.engine.transactions import archive_version
from custom_memory_engine.security.audit_guard import content_free_details

_CODE = re.compile(r"[a-z0-9_]{1,64}")
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
    """``refute``: retire an inferred artifact against the evidence that contradicts it."""

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


def register_lifecycle_handlers(engine: Engine, handlers: Handlers) -> LifecycleHandlers:
    """Wire ``refute`` onto ``engine``; the entry point's contract owns the rest of the guards."""
    lifecycle = LifecycleHandlers(handlers)
    engine.register("refute", lifecycle.refute)
    return lifecycle


__all__ = ["LifecycleHandlers", "register_lifecycle_handlers"]
