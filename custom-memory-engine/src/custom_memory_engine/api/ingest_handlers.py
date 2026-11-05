"""Handlers for the W1 write operations: ``ingest_episode``, ``assert``, ``ingest_artifact`` (E-039; E-013, E-015).

Same contract as :mod:`api.handlers`: reached only through :meth:`api.entry.Engine.call`, run as the caller under
the restricted role, static errors only.

* ``ingest_episode`` records an ``observed`` episode of a declared source: a ``turn`` of the caller's own, an
  ``external`` reference, or one of the coding-profile source types ``tool``, ``file``, or ``event`` (V2-D66,
  V2-D41 extension). No gate runs: an episode asserts nothing, it records that a source said something at a time
  (``ARCHITECTURE_V2.md`` 2.3). The optional ``scope`` argument (``API_V2.md`` §4) is propagated to
  ``CandidateVersionInput.scope_path`` so repository-scoped episodes persist under the requested path (issue #155).
* ``assert`` / ``ingest_artifact`` send a claim or procedure through the **v1 validation gate**
  (``engine.ingest.apply_candidate``) and answer ``asserted``, ``contested`` or a ``Rejected`` result; the
  decision is recorded either way. ``kind='procedure'`` is now accepted in addition to ``kind='claim'``
  (V2-D66, issue #157). Only ``turn`` evidence can be grounded through the gate; ``external``/``tool``/``file``/
  ``event`` evidence carries a hash, not captured text. The ``scope`` argument is propagated as above.

Under a bound profile, a source or evidence type that no extractor of the profile declares is refused
(``validation_error/source_type_not_enabled_by_profile``, ``V2-D69``); without one, the declared types above apply.

Both write under the generic predicates of migration 13 (multi-valued), so they never supersede one another.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, cast

from psycopg import AsyncConnection, sql
from psycopg.errors import ForeignKeyViolation
from psycopg.rows import tuple_row

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Receipt, Rejected, Runtime, new_id, now, remember, replayed
from custom_memory_engine.engine.ingest import apply_candidate
from custom_memory_engine.engine.transactions import CandidateVersionInput, EvidenceInput, commit_initial_version
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.model.axes import Kind, Retention
from custom_memory_engine.profile import Profile
from custom_memory_engine.scope.paths import ScopeError, parse_scope

# V2-D41 + V2-D66: tool/file/event are the coding-profile source types; turn and external are the W1 originals.
_SOURCE_TYPES = ("turn", "external", "tool", "file", "event")
# Kinds that the assert / ingest_artifact path accepts.  Abstractions come from `infer` (C-021).
_ASSERTABLE_KINDS = frozenset({"claim", "procedure"})
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


def _scope_path(raw: object, user: str) -> str | None:
    """The canonical text of the caller's explicit ``scope`` argument (``V2-D68``: full paths, never expanded).

    The path is used exactly as given: no profile ``scope_root`` is applied and nothing is constructed from it.
    Through :meth:`Engine.call` the argument is always present, because the entry point's ``scope_writable``
    condition refuses an omitted scope (``scope_required``) and one outside ``/u/<user>/`` (``forbidden``) before
    this runs; the namespace check here is defence in depth. ``None`` is reachable only by an in-process caller
    that bypasses the entry point.
    """
    if raw is None:
        return None
    try:
        path = parse_scope(raw)
    except ScopeError:
        raise EntryError("validation_error", "scope_invalid") from None
    if not path.text.startswith(f"/u/{user}/"):
        raise EntryError("forbidden")
    return path.text


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


def _evidence_for_source(source: Mapping[str, object], span: str) -> EvidenceInput:
    """Build an ``EvidenceInput`` from a validated source mapping.

    ``turn`` sources link to a turn row; every other declared source type is a reference row stored under the type
    the caller declared, with its content hash (migration 17). The turn-grounding gate runs separately in
    ``_assert`` for claim/procedure writes.
    """
    ref = str(source["ref"])
    if source["type"] == "turn":
        # Caller pre-validated the hash matches the turn text before calling this helper.
        return EvidenceInput(turn_id=ref, evidence_span=span)
    content_hash = str(source["content_hash"])
    # `_source` admitted only `_SOURCE_TYPES`, so a non-turn type is one of these four
    reference_type = cast(Literal["external", "tool", "file", "event"], source["type"])
    return EvidenceInput(
        source_type=reference_type,
        source_ref=ref,
        content_hash=content_hash,
        evidence_span=span,
    )


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


async def _original_answer(
    runtime: Runtime, connection: AsyncConnection, user: str, version_id: str, effect: str
) -> Receipt | Rejected:
    """Rebuild the answer a replayed key first got: the gate's first recorded decision and the original belief time.

    A later adjudication of a held candidate does not change what the original call answered. An episode has no
    decision and answers ``effect``.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT c.decision, c.reason_code, lower(v.recorded_range) FROM {v} AS v LEFT JOIN LATERAL ("
                "SELECT decision, reason_code FROM {c} WHERE candidate_version_id = v.id AND user_id = v.user_id "
                "ORDER BY decided_at, id LIMIT 1) AS c ON true WHERE v.id = %s AND v.user_id = %s"
            ).format(v=runtime.table("memory_version"), c=runtime.table("memory_conflict")),
            (version_id, user),
        )
        row = await cursor.fetchone()
    if row is None:  # the artifact was deleted since: its id is still the answer, its state is gone
        return Receipt(version_id, effect, await now(connection))
    decision, reason_code, recorded = row
    if decision == "reject":
        return Rejected(str(reason_code))
    if decision == "hold":
        effect = "contested"
    return Receipt(version_id, effect, recorded if isinstance(recorded, datetime) else await now(connection))


class IngestHandlers:
    """The W1 write handlers, bound to one :class:`Runtime`."""

    def __init__(self, runtime: Runtime, profile: Profile | None = None) -> None:
        self.runtime = runtime
        self.profile = profile

    # -- profile configuration (E-019): data, never a branch on the profile's name ------------------------------

    def _require_kind(self, kind: Kind) -> None:
        if self.profile is not None and kind not in self.profile.kinds:
            raise EntryError("validation_error", "kind_not_enabled_by_profile")

    def _require_source_type(self, source_type: object) -> None:
        """A bound profile admits only the source types its extractors declare (``V2-D69``, #164).

        Without a profile nothing is narrowed: the declared ``SourceRef`` types of ``_SOURCE_TYPES`` apply, as before.
        """
        if self.profile is None:
            return
        if not any(source_type in extractor.source_types for extractor in self.profile.extractors):
            raise EntryError("validation_error", "source_type_not_enabled_by_profile")

    def _retention(self, kind: Kind) -> Retention:
        """The profile's retention default for ``kind`` (durable without one); the database enforces the floors."""
        return "durable" if self.profile is None else self.profile.retention.defaults.get(kind, "durable")

    async def _apply_retention(self, connection: AsyncConnection, user: str, version_id: str, kind: Kind) -> None:
        retention = self._retention(kind)
        if retention != "durable":
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql.SQL("UPDATE {t} SET retention = %s WHERE id = %s AND user_id = %s").format(
                        t=self.runtime.table("memory_version")
                    ),
                    (retention, version_id, user),
                )

    async def ingest_episode(self, call: Call) -> Receipt:
        user, key = call.ctx.principal, _text(call.arguments.get("idempotency_key"), "idempotency_key", limit=256)
        content = _text(call.arguments.get("content"), "content", limit=_CONTENT_LIMIT)
        source = _source(call.arguments.get("source"))
        self._require_source_type(source["type"])
        event_time = _when(call.arguments.get("event_time"), "event_time")
        scope = _scope_path(call.arguments.get("scope"), user)
        self._require_kind("episode")
        span = _text(source.get("span") or source["ref"], "source_span")
        try:
            async with self.runtime.session(user) as connection:
                if (found := await replayed(self.runtime, connection, user, "ingest_episode", key)) is not None:
                    answer = await _original_answer(self.runtime, connection, user, found, "observed")
                    assert isinstance(answer, Receipt)  # an episode is never gated, so never rejected
                    return answer
                if source["type"] == "turn":
                    turn_id = str(source["ref"])
                    turns = await _turns(self.runtime, connection, user, [turn_id])
                    if turn_id not in turns:
                        raise EntryError("forbidden")  # absent and another user's are the same answer
                    if source["content_hash"] != canonical_hash(turns[turn_id][1]):
                        raise EntryError("validation_error", "content_hash_mismatch")
                evidence = _evidence_for_source(source, span)
                version_id = new_id("v")
                recorded = await now(connection)
                candidate = CandidateVersionInput(
                    version_id=version_id,
                    memory_key=new_id("ep"),
                    user_id=user,
                    scope_path=scope,
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
                await self._apply_retention(connection, user, version_id, "episode")
                await remember(self.runtime, connection, user, "ingest_episode", key, version_id)
                answer = await _original_answer(self.runtime, connection, user, version_id, "observed")
                assert isinstance(answer, Receipt)
                return answer
        except ForeignKeyViolation:
            raise EntryError("forbidden") from None

    async def _assert(self, call: Call, content: str, evidence: object, event_range: object) -> Receipt | Rejected:
        user, key = call.ctx.principal, _text(call.arguments.get("idempotency_key"), "idempotency_key", limit=256)
        kind = call.arguments.get("kind")
        if kind not in _ASSERTABLE_KINDS:
            raise EntryError(
                "validation_error", "kind_not_supported"
            )  # abstractions come from `infer` (C-021); episodes are never asserted
        self._require_kind(kind)  # type: ignore[arg-type]
        if not isinstance(evidence, Sequence) or isinstance(evidence, str | bytes) or not evidence:
            raise EntryError("validation_error", "evidence_required")
        sources = [_source(item) for item in evidence]
        for source in sources:
            self._require_source_type(source["type"])
        if any(source["type"] != "turn" for source in sources):
            # An external/tool/file/event reference carries a hash, not the captured text: nothing to ground a span.
            raise EntryError("validation_error", "external_evidence_cannot_be_grounded")
        spans = [_text(source.get("span"), "source_span") for source in sources]
        scope = _scope_path(call.arguments.get("scope"), user)
        start, end = (None, None)
        if event_range is not None:
            if not isinstance(event_range, Sequence) or isinstance(event_range, str | bytes) or len(event_range) != 2:
                raise EntryError("validation_error", "event_range_invalid")
            start, end = _when(event_range[0], "event_range"), _when(event_range[1], "event_range")
        async with self.runtime.session(user) as connection:
            if (found := await replayed(self.runtime, connection, user, "assert", key)) is not None:
                return await _original_answer(self.runtime, connection, user, found, "asserted")
            turn_ids = [str(source["ref"]) for source in sources]
            turns = await _turns(self.runtime, connection, user, turn_ids)
            if any(turn_id not in turns for turn_id in turn_ids):
                raise EntryError("forbidden")  # a missing turn and another user's turn are the same answer
            for source in sources:
                if source["content_hash"] != canonical_hash(turns[str(source["ref"])][1]):
                    raise EntryError("validation_error", "content_hash_mismatch")
            version_id, recorded = new_id("v"), await now(connection)
            # layer: procedures and claims both use the semantic layer (the axes trigger allows 'procedure' as an
            # override kind when the layer is semantic; see schema/005_axes.sql)
            candidate = CandidateVersionInput(
                version_id=version_id,
                memory_key=new_id("st"),
                user_id=user,
                scope_path=scope,
                kind=str(kind) if kind == "procedure" else None,  # only supply when the trigger cannot infer it
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
            await apply_candidate(
                connection,
                candidate,
                evidence_turns=[turns[turn_id] for turn_id in dict.fromkeys(turn_ids)],
                schema_name=self.runtime.schema_name,
            )
            await _stamp_creator(self.runtime, connection, call, version_id)
            await self._apply_retention(connection, user, version_id, kind)  # type: ignore[arg-type]
            await remember(self.runtime, connection, user, "assert", key, version_id)
            # the answer is read back from what was recorded, exactly as a replay reads it
            return await _original_answer(self.runtime, connection, user, version_id, "asserted")

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


def register_ingest_handlers(engine: Engine, runtime: Runtime, profile: Profile | None = None) -> IngestHandlers:
    """Wire ``ingest_episode``, ``assert`` and ``ingest_artifact`` onto ``engine`` (configured by ``profile``)."""
    handlers = IngestHandlers(runtime, profile)
    table: Mapping[str, Handler] = {
        "ingest_episode": handlers.ingest_episode,
        "assert": handlers.assert_,
        "ingest_artifact": handlers.ingest_artifact,
    }
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["IngestHandlers", "register_ingest_handlers"]
