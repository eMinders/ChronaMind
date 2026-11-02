"""Handlers for the W1 write operations: ``ingest_episode``, ``assert``, ``ingest_artifact`` (E-039; E-013, E-015).

Same contract as :mod:`api.handlers`: reached only through :meth:`api.entry.Engine.call`, run as the caller under
the restricted role, static errors only.

* ``ingest_episode`` records an ``observed`` episode of a declared source: a ``turn`` of the caller's own, or an
  ``external`` reference (locator + SHA-256 of the captured text, no engine row). No gate runs: an episode asserts
  nothing, it records that a source said something at a time (``ARCHITECTURE_V2.md`` 2.3).
* ``assert`` / ``ingest_artifact`` send a claim through the **v1 validation gate** (``engine.ingest.apply_candidate``,
  whose verdicts ``engine.admission.ground`` reproduces exactly for turns) and answer ``asserted`` (accepted),
  ``contested`` (held) or a ``Rejected`` **result** with the gate's static reason code; the decision is recorded
  either way. Only ``turn`` evidence can be grounded: an external reference carries a hash, not the captured text,
  so the engine has nothing to check a span against. Asserting from external evidence alone is therefore a
  ``validation_error`` here, not a silent pass.

Both write under the generic predicates of migration 13 (multi-valued), so they never supersede one another.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from psycopg import AsyncConnection, sql
from psycopg.errors import ForeignKeyViolation
from psycopg.rows import tuple_row

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Receipt, Rejected, Runtime, new_id, now, remember, replayed
from custom_memory_engine.engine.ingest import apply_candidate
from custom_memory_engine.engine.transactions import CandidateVersionInput, EvidenceInput, commit_initial_version
from custom_memory_engine.grounding.sources import canonical_hash

_SOURCE_TYPES = ("turn", "external")  # V2-D41: the declared source types
_CONTENT_LIMIT = 20_000


def _text(value: object, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EntryError("validation_error", f"{field}_required")
    return value


def _when(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise EntryError("validation_error", f"{field}_invalid") from None
    else:
        raise EntryError("validation_error", f"{field}_invalid")
    if parsed.tzinfo is None:
        raise EntryError("validation_error", f"{field}_must_carry_a_timezone")
    return parsed.astimezone(UTC)


def _source(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise EntryError("validation_error", "source_invalid")
    if raw.get("type") not in _SOURCE_TYPES:
        raise EntryError("validation_error", "source_type_not_declared")
    _text(raw.get("ref"), "source_ref")
    digest = raw.get("content_hash")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise EntryError("validation_error", "content_hash_invalid")
    return raw


async def _turns(
    runtime: Runtime, connection: AsyncConnection, user: str, ids: Sequence[str]
) -> dict[str, tuple[str, str]]:
    """The caller's own turns, as ``{id: (role, content)}``. A turn that is absent or not theirs is simply missing."""
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT id, role, content FROM {t} WHERE id = ANY(%s) AND user_id = %s").format(
                t=runtime.table("turn")
            ),
            (list(ids), user),
        )
        return {str(r[0]): (str(r[1]), str(r[2])) for r in await cursor.fetchall()}


async def _stamp_creator(runtime: Runtime, connection: AsyncConnection, call: Call, version_id: str) -> None:
    """Record who created the artifact and as which tier (migration 12); set once."""
    async with connection.cursor() as cursor:
        await cursor.execute(
            sql.SQL(
                "UPDATE {t} SET created_by = %s, created_tier = %s "
                "WHERE id = %s AND user_id = %s AND created_by IS NULL"
            ).format(t=runtime.table("memory_version")),
            (call.ctx.principal, call.ctx.tier, version_id, call.ctx.principal),
        )


class IngestHandlers:
    """The W1 write handlers, bound to one :class:`Runtime`."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def ingest_episode(self, call: Call) -> Receipt:
        user, key = call.ctx.principal, _text(call.arguments.get("idempotency_key"), "idempotency_key", limit=256)
        content = _text(call.arguments.get("content"), "content", limit=_CONTENT_LIMIT)
        source = _source(call.arguments.get("source"))
        event_time = _when(call.arguments.get("event_time"), "event_time")
        span = _text(source.get("span") or source["ref"], "source_span")
        try:
            async with self.runtime.session(user) as connection:
                if (found := await replayed(self.runtime, connection, user, "ingest_episode", key)) is not None:
                    return Receipt(found, "observed", await now(connection))
                if source["type"] == "turn":
                    turn_id = str(source["ref"])
                    turns = await _turns(self.runtime, connection, user, [turn_id])
                    if turn_id not in turns:
                        raise EntryError("forbidden")  # absent and another user's are the same answer
                    if source["content_hash"] != canonical_hash(turns[turn_id][1]):
                        raise EntryError("validation_error", "content_hash_mismatch")
                    evidence = EvidenceInput(turn_id=turn_id, evidence_span=span)
                else:
                    evidence = EvidenceInput(
                        source_type="external",
                        source_ref=str(source["ref"]),
                        content_hash=str(source["content_hash"]),
                        evidence_span=span,
                    )
                version_id = new_id("v")
                recorded = await now(connection)
                candidate = CandidateVersionInput(
                    version_id=version_id,
                    memory_key=new_id("ep"),
                    user_id=user,
                    subject_key="self",
                    predicate_key="episode",
                    single_valued=False,
                    content=content,
                    valid_from=event_time or recorded,
                    valid_from_basis="explicit" if event_time else "inferred",
                    layer="episodic",
                    evidence=(evidence,),
                )
                await commit_initial_version(connection, candidate, schema_name=self.runtime.schema_name)
                await _stamp_creator(self.runtime, connection, call, version_id)
                await remember(self.runtime, connection, user, "ingest_episode", key, version_id)
                return Receipt(version_id, "observed", recorded)
        except ForeignKeyViolation:
            raise EntryError("forbidden") from None

    async def _assert(self, call: Call, content: str, evidence: object, event_range: object) -> Receipt | Rejected:
        user, key = call.ctx.principal, _text(call.arguments.get("idempotency_key"), "idempotency_key", limit=256)
        kind = call.arguments.get("kind")
        if kind != "claim":
            raise EntryError(
                "validation_error", "kind_not_supported"
            )  # procedures: W3; abstractions come from `infer`
        if not isinstance(evidence, Sequence) or isinstance(evidence, str | bytes) or not evidence:
            raise EntryError("validation_error", "evidence_required")
        sources = [_source(item) for item in evidence]
        if any(source["type"] != "turn" for source in sources):
            # An external reference carries a hash, not the captured text: there is nothing to check a span against.
            raise EntryError("validation_error", "external_evidence_cannot_be_grounded")
        spans = [_text(source.get("span"), "source_span") for source in sources]
        start, end = (None, None)
        if event_range is not None:
            if not isinstance(event_range, Sequence) or isinstance(event_range, str | bytes) or len(event_range) != 2:
                raise EntryError("validation_error", "event_range_invalid")
            start, end = _when(event_range[0], "event_range"), _when(event_range[1], "event_range")
        async with self.runtime.session(user) as connection:
            if (found := await replayed(self.runtime, connection, user, "assert", key)) is not None:
                return Receipt(found, "asserted", await now(connection))
            turn_ids = [str(source["ref"]) for source in sources]
            turns = await _turns(self.runtime, connection, user, turn_ids)
            if any(turn_id not in turns for turn_id in turn_ids):
                raise EntryError("forbidden")  # a missing turn and another user's turn are the same answer
            for source in sources:
                if source["content_hash"] != canonical_hash(turns[str(source["ref"])][1]):
                    raise EntryError("validation_error", "content_hash_mismatch")
            version_id, recorded = new_id("v"), await now(connection)
            candidate = CandidateVersionInput(
                version_id=version_id,
                memory_key=new_id("st"),
                user_id=user,
                subject_key="self",
                predicate_key="statement",
                single_valued=False,
                content=content,
                valid_from=start or recorded,
                valid_until=end,
                valid_from_basis="explicit" if start else "inferred",
                layer="semantic",
                evidence=tuple(
                    EvidenceInput(turn_id=turn_id, evidence_span=span)
                    for turn_id, span in zip(turn_ids, spans, strict=True)
                ),
            )
            applied = await apply_candidate(
                connection,
                candidate,
                evidence_turns=[turns[turn_id] for turn_id in dict.fromkeys(turn_ids)],
                schema_name=self.runtime.schema_name,
            )
            await _stamp_creator(self.runtime, connection, call, version_id)
            await remember(self.runtime, connection, user, "assert", key, version_id)
            decision = applied.decision.decision
            if decision == "reject":
                return Rejected(applied.decision.reason_code)
            return Receipt(version_id, "asserted" if decision == "accept" else "contested", recorded)

    async def assert_(self, call: Call) -> Receipt | Rejected:
        return await self._assert(
            call, _text(call.arguments.get("content"), "content", limit=_CONTENT_LIMIT),
            call.arguments.get("evidence"), call.arguments.get("event_range"),
        )  # fmt: skip

    async def ingest_artifact(self, call: Call) -> Receipt | Rejected:
        candidate = call.arguments.get("candidate")
        if not isinstance(candidate, Mapping):
            raise EntryError("validation_error", "candidate_invalid")
        return await self._assert(
            call, _text(candidate.get("content"), "content", limit=_CONTENT_LIMIT),
            call.arguments.get("evidence"), candidate.get("event_range"),
        )  # fmt: skip


def register_ingest_handlers(engine: Engine, runtime: Runtime) -> IngestHandlers:
    """Wire ``ingest_episode``, ``assert`` and ``ingest_artifact`` onto ``engine``."""
    handlers = IngestHandlers(runtime)
    table: Mapping[str, Handler] = {
        "ingest_episode": handlers.ingest_episode,
        "assert": handlers.assert_,
        "ingest_artifact": handlers.ingest_artifact,
    }
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["IngestHandlers", "register_ingest_handlers"]
