"""The minimal ``infer`` handler (C-021 minimal; V2-D66, issue #158).

Same contract as :mod:`api.ingest_handlers`: reached only through :meth:`api.entry.Engine.call`, run as the caller
under the restricted role. The entry point has already enforced the operation's conditions before this runs: Tier B
only, ``scope_writable``, ``registered_method`` (the ``METHOD`` registry, ``I3``) and ``targets_readable`` (every
source is in the caller's read set; absent and unreadable answer the same ``forbidden``).

``infer`` stores an ``abstraction`` as an ``inferred`` hypothesis (``API_V2.md`` 4.4). Admission is the **soundness
policy**, :func:`engine.admission.assess_soundness`, and only its *creation* verdict decides here: ``V2-D40`` places
the finite-sample bound on verification and on rendering in ``facts``, never on creation, because a candidate must be
stored to be tested on held-out data. A failed structural check (no source, unregistered method, an empty ledger) is
a recorded :class:`Rejected`, never an exception. The assessment is recorded either way, content-free.

What the database adds, at commit (migrations 5, 10, 14): the ``persona`` layer derives ``kind='abstraction'`` and
``lifecycle='inferred'``; an inferred row must carry a derivation row and its registered method; an abstraction must
carry its ledger (``I15``), which the insert trigger creates at zero and this handler then fills from the request.

Under a profile with ``retention.promote_to_durable``, each ``session`` source of the new abstraction is promoted to
``durable`` and the promotion is recorded (``V2-D70``): durable knowledge must not rest on sources that expire.

Not here (W5, full ``C-021``): the statistical verifier, dependence correction, ``verify``/``refute``. The optional
``predicate`` stays deferred (``V2-D43``). ``refutation_attempts`` and ``negative_control_clean`` are not inputs of
``infer``, so they are passed as ``0`` and ``False``: they affect only the verification verdict, which they keep
conservative. ``tau_confidence``/``tau_coverage`` have no defaults (``V2-D30`` is open): whoever wires the handler
states them.
"""

from collections.abc import Mapping, Sequence
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Receipt, Rejected, Runtime, new_id, now, remember, replayed
from custom_memory_engine.engine.admission import (
    AdmissionError,
    LedgerCounts,
    MethodRef,
    SoundnessCandidate,
    assess_soundness,
    record_soundness_assessment,
)
from custom_memory_engine.engine.transactions import CandidateVersionInput, DerivationInput, commit_initial_version
from custom_memory_engine.model.axes import Retention
from custom_memory_engine.profile import Profile
from custom_memory_engine.scope.paths import ScopeError, parse_scope
from custom_memory_engine.security.audit_guard import content_free_details

_STATEMENT_LIMIT = 20_000
_MAX_SOURCES = 256
_LEDGER_FIELDS = ("support", "counter", "undetermined", "opportunity")


def _text(value: object, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EntryError("validation_error", f"{field}_required")
    return value


def _method(raw: object) -> MethodRef:
    """The registered method reference; the entry point has already checked that it is in the registry."""
    if not isinstance(raw, Mapping):
        raise EntryError("validation_error", "method_required")
    return MethodRef(
        _text(raw.get("id"), "method_id", limit=256),
        _text(raw.get("version"), "method_version", limit=256),
        _text(raw.get("params_hash"), "method_params_hash", limit=256),
    )


def _ledger(raw: object) -> LedgerCounts:
    """The opportunity ledger ``{support, counter, undetermined, opportunity}`` (``STATISTICS.md``).

    A missing ledger is an empty one: ``o = 0`` is "insufficient evidence", which the soundness policy rejects (and
    records) rather than this parser. A ledger that is present must be complete and must balance.
    """
    if raw is None:
        return LedgerCounts(0, 0, 0)
    if not isinstance(raw, Mapping) or any(name not in raw for name in _LEDGER_FIELDS):
        raise EntryError("validation_error", "ledger_incomplete")
    try:
        counts = LedgerCounts(raw["support"], raw["counter"], raw["undetermined"])  # type: ignore[arg-type]
    except AdmissionError:
        raise EntryError("validation_error", "ledger_counts_invalid") from None
    opportunity = raw["opportunity"]
    if isinstance(opportunity, bool) or opportunity != counts.opportunity:
        raise EntryError("validation_error", "ledger_unbalanced")
    return counts


def _sources(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise EntryError("validation_error", "sources_required")
    if len(raw) > _MAX_SOURCES:
        raise EntryError("validation_error", "too_many_sources")
    # duplicates name one derivation edge; order is kept for a stable record
    return tuple(dict.fromkeys(_text(item, "source", limit=256) for item in raw))


class InferHandlers:
    """The ``infer`` handler, bound to one :class:`Runtime` and to stated soundness thresholds."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        tau_confidence: float,
        tau_coverage: float,
        profile: Profile | None = None,
    ) -> None:
        self.runtime = runtime
        self.tau_confidence = tau_confidence
        self.tau_coverage = tau_coverage
        self.profile = profile

    def _retention(self) -> Retention:
        """The profile's retention default for abstractions (durable without one); the database enforces floors."""
        return "durable" if self.profile is None else self.profile.retention.defaults.get("abstraction", "durable")

    async def infer(self, call: Call) -> Receipt | Rejected:
        user = call.ctx.principal
        key = _text(call.arguments.get("idempotency_key"), "idempotency_key", limit=256)
        if self.profile is not None and "abstraction" not in self.profile.kinds:
            raise EntryError("validation_error", "kind_not_enabled_by_profile")
        statement = _text(call.arguments.get("statement"), "statement", limit=_STATEMENT_LIMIT)
        if call.arguments.get("predicate") is not None:
            raise EntryError("validation_error", "predicate_not_supported")  # the optional predicate is V2-D43
        try:
            scope = parse_scope(call.arguments.get("scope")).text
        except ScopeError:
            raise EntryError("validation_error", "scope_invalid") from None
        if not scope.startswith(f"/u/{user}/"):
            raise EntryError("forbidden")  # defence in depth: `scope_writable` already decided this
        method, ledger, sources = (
            _method(call.arguments.get("method")),
            _ledger(call.arguments.get("ledger")),
            _sources(call.arguments.get("sources")),
        )
        candidate = SoundnessCandidate(
            source_ids=sources, method=method, ledger=ledger, refutation_attempts=0, negative_control_clean=False
        )
        # The registry was consulted by the entry point (`registered_method`); a call that reaches here is registered.
        assessment = assess_soundness(
            candidate,
            tau_confidence=self.tau_confidence,
            tau_coverage=self.tau_coverage,
            method_registered=lambda _ref: True,
        )
        async with self.runtime.session(user) as connection:
            if (found := await replayed(self.runtime, connection, user, "infer", key)) is not None:
                return await self._receipt(connection, user, found)
            await record_soundness_assessment(
                connection,
                user_id=user,
                candidate=candidate,
                assessment=assessment,
                tau_confidence=self.tau_confidence,
                tau_coverage=self.tau_coverage,
                schema_name=self.runtime.schema_name,
            )
            if not assessment.creation.allowed:
                return Rejected(assessment.creation.reasons[0])
            if not await self._all_own(connection, user, sources):
                # a derivation is same-tenant (migration 4): a source readable through a grant cannot be derived from
                raise EntryError("forbidden")
            version_id, recorded = new_id("v"), await now(connection)
            await commit_initial_version(
                connection,
                CandidateVersionInput(
                    version_id=version_id,
                    memory_key=new_id("ab"),
                    user_id=user,
                    scope_path=scope,
                    method_id=method.method_id,
                    subject_key="self",
                    predicate_key="statement",
                    single_valued=False,
                    content=statement,
                    valid_from=recorded,
                    valid_from_basis="inferred",
                    layer="persona",  # derives kind='abstraction', lifecycle='inferred' (migration 5)
                    confidence=None,  # the bound is the confidence of an inference, and it is not computed here
                    derivations=tuple(
                        DerivationInput(source_version_id=source, derivation_type="consolidation")
                        for source in sources
                    ),
                ),
                schema_name=self.runtime.schema_name,
            )
            await self._fill_ledger(connection, user, version_id, ledger)
            await self._promote_sources(connection, user, sources)
            await self._stamp(connection, call, version_id)
            await remember(self.runtime, connection, user, "infer", key, version_id)
            return await self._receipt(connection, user, version_id)

    async def _promote_sources(self, connection: AsyncConnection, user: str, sources: Sequence[str]) -> None:
        """The promotion event of ``V2-D70``: durable knowledge must not rest on sources that expire.

        When the bound profile sets ``retention.promote_to_durable``, each ``session`` source of the new (durable)
        abstraction becomes ``durable`` in this transaction, and each promotion is recorded as a content-free
        ``retention_promoted`` audit event with a static reason (``V2-D10``: promotion carries a recorded reason).
        ``working`` sources keep their own purge semantics (``E-026``); nothing is ever demoted here.
        """
        if self.profile is None or not self.profile.retention.promote_to_durable:
            return
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "UPDATE {t} SET retention = 'durable' WHERE id = ANY(%s) AND user_id = %s "
                    "AND retention = 'session' RETURNING id, kind"
                ).format(t=self.runtime.table("memory_version")),
                (list(sources), user),
            )
            promoted = await cursor.fetchall()
            for version_id, kind in promoted:
                details = content_free_details({"reason_code": "derived_into_durable", "kind": str(kind)})
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                        "VALUES (%s, %s, %s, 'retention_promoted', pg_catalog.transaction_timestamp(), %s)"
                    ).format(a=self.runtime.table("audit_event")),
                    (f"audit-{uuid4().hex}", user, str(version_id), Jsonb(details)),
                )

    async def _all_own(self, connection: AsyncConnection, user: str, sources: Sequence[str]) -> bool:
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL("SELECT count(*) FROM {t} WHERE id = ANY(%s) AND user_id = %s").format(
                    t=self.runtime.table("memory_version")
                ),
                (list(sources), user),
            )
            row = await cursor.fetchone()
        return row is not None and row[0] == len(sources)

    async def _fill_ledger(
        self, connection: AsyncConnection, user: str, version_id: str, ledger: LedgerCounts
    ) -> None:
        """Replace the zero ledger the insert trigger created with the request's counts (``I15``)."""
        async with connection.cursor() as cursor:
            await cursor.execute(
                sql.SQL(
                    "UPDATE {t} SET support_count = %s, counter_count = %s, undetermined_count = %s, "
                    "opportunity_count = %s WHERE version_id = %s AND user_id = %s"
                ).format(t=self.runtime.table("abstraction_stats")),
                (ledger.support, ledger.counter, ledger.undetermined, ledger.opportunity, version_id, user),
            )
            if cursor.rowcount != 1:
                raise EntryError("validation_error", "ledger_not_recorded")

    async def _stamp(self, connection: AsyncConnection, call: Call, version_id: str) -> None:
        """Creator, tier (migration 12) and the profile's retention default, set once."""
        retention = self._retention()
        async with connection.cursor() as cursor:
            await cursor.execute(
                sql.SQL(
                    "UPDATE {t} SET created_by = %s, created_tier = %s WHERE id = %s AND user_id = %s "
                    "AND created_by IS NULL"
                ).format(t=self.runtime.table("memory_version")),
                (call.ctx.principal, call.ctx.tier, version_id, call.ctx.principal),
            )
            if retention != "durable":
                await cursor.execute(
                    sql.SQL("UPDATE {t} SET retention = %s WHERE id = %s AND user_id = %s").format(
                        t=self.runtime.table("memory_version")
                    ),
                    (retention, version_id, call.ctx.principal),
                )

    async def _receipt(self, connection: AsyncConnection, user: str, version_id: str) -> Receipt:
        """The answer is read back from what was recorded, exactly as a replay reads it."""
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL("SELECT lower(recorded_range) FROM {t} WHERE id = %s AND user_id = %s").format(
                    t=self.runtime.table("memory_version")
                ),
                (version_id, user),
            )
            row = await cursor.fetchone()
        recorded = row[0] if row is not None and row[0] is not None else await now(connection)
        return Receipt(version_id, "inferred", recorded)


def register_infer_handlers(
    engine: Engine,
    runtime: Runtime,
    *,
    tau_confidence: float,
    tau_coverage: float,
    profile: Profile | None = None,
) -> InferHandlers:
    """Wire ``infer`` onto ``engine``. The thresholds are required: ``V2-D30`` has not fixed their values."""
    handlers = InferHandlers(runtime, tau_confidence=tau_confidence, tau_coverage=tau_coverage, profile=profile)
    table: Mapping[str, Handler] = {"infer": handlers.infer}
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["InferHandlers", "register_infer_handlers"]
