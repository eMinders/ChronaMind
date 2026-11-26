"""Claim, ask, write: one slow-lane job at a time, under the tenant's own role."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import structlog
from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.clock import ClockTick
from custom_memory_engine.deletion.closure import current_deletion_epoch, require_current_deletion_epoch
from custom_memory_engine.deletion.markers import TurnErased, is_reconciled, turn_is_erased
from custom_memory_engine.engine.index_maintenance import JOB_KIND, logical_index, perform_inline, reindex_concurrently
from custom_memory_engine.engine.ingest import TurnInput
from custom_memory_engine.queue.contracts import Job
from custom_memory_engine.security.scoping import tenant_scope
from custom_memory_engine.semantic import judgements
from custom_memory_engine.semantic.contracts import (
    Embedder,
    ExtractionRequest,
    ExtractionResult,
    RelationAdjudicator,
    StructuredExtractor,
)
from custom_memory_engine.semantic.entities import EntityAdjudicator
from custom_memory_engine.semantic.ingestor import apply_extraction
from custom_memory_engine.semantic.slow_lane import claim_one, complete_one, recover_stale

logger = structlog.get_logger(__name__)

#: The job kinds this worker serves. ``index_rebuild`` is enqueued by ``rebuild_index`` (``A-014``); a worker claiming
#: only extraction would leave every maintenance job queued for ever.
KINDS = ("semantic_extraction", JOB_KIND)


@dataclass(frozen=True)
class WorkerSettings:
    """What the loop needs. Model adapters are injected, so the loop never names a provider or a model."""

    role: str
    users: tuple[str, ...]
    extractor: StructuredExtractor
    schema: str = "cme_engine"
    embedder: Embedder | None = None
    adjudicator: RelationAdjudicator | None = None
    entity_adjudicator: EntityAdjudicator | None = None
    #: Seconds between polls when nothing is due. Also the recovery cadence: an idle cycle returns expired leases.
    idle_poll: float = 5.0


class _Rollback(Exception):  # noqa: N818 - a control-flow signal, not an error
    """Raised to end a dry run's transaction with a rollback."""


async def load_turn(connection: AsyncConnection, settings: WorkerSettings, user_id: str, turn_id: str) -> TurnInput:
    """The verbatim turn a job refers to, read under the tenant's own role and row-level security."""
    turn, _ = await _load_turn(connection, settings, user_id, turn_id)
    return turn


async def _load_turn(
    connection: AsyncConnection, settings: WorkerSettings, user_id: str, turn_id: str
) -> tuple[TurnInput, int]:
    """The turn and the deletion epoch it was read at, in one snapshot: the epoch the write must still be at (F5).

    A turn a deletion marked (``erased_turn``, ``T-46``) raises :class:`TurnErased` before any model sees it. The flag
    is read *after* the epoch: a deletion that commits in between either is visible here, or advanced the epoch the
    write is checked against, so no interleaving lets a marked turn be extracted and written.
    """
    async with tenant_scope(connection, user_id, role=settings.role):
        epoch = await current_deletion_epoch(connection, user_id, schema_name=settings.schema)
        if await turn_is_erased(connection, user_id=user_id, turn_id=turn_id, schema_name=settings.schema):
            raise TurnErased
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT id, user_id, session_id, sequence_no, role, content, occurred_at "
                    "FROM {t} WHERE id = %s AND user_id = %s"
                ).format(t=sql.Identifier(settings.schema, "turn")),
                (turn_id, user_id),
            )
            row = await cursor.fetchone()
    if row is None:
        raise LookupError("turn_missing")  # failed, backed off, eventually dead-lettered: visible, not silent
    return _turn(row), epoch


def _turn(row: tuple[object, ...]) -> TurnInput:
    return TurnInput(
        turn_id=str(row[0]),
        user_id=str(row[1]),
        session_id=str(row[2]),
        sequence_no=int(row[3]),  # type: ignore[arg-type]
        role="assistant" if str(row[4]) == "assistant" else "user",  # the column is constrained to these two
        content=str(row[5]),
        occurred_at=row[6],  # type: ignore[arg-type]
    )


async def run_once(connection: AsyncConnection, user_id: str, settings: WorkerSettings) -> str:
    """One claim and the work it names. Returns ``idle`` or ``<status>:<job id>``, for the log and for a test.

    Nothing is claimed in a restored database the erasure journal has not been replayed into (``T-46``, ``V2-D125``):
    the backup still holds the turns and jobs of deletions made after it, and extracting them would re-derive what was
    deleted. That answers ``unreconciled`` until ``scripts/erasure_journal.py replay`` records this database.
    """
    async with tenant_scope(connection, user_id, role=settings.role):
        reconciled = await is_reconciled(connection, schema_name=settings.schema)
    if not reconciled:
        logger.warning("slow_lane_restore_not_reconciled", user_id=user_id)
        return "unreconciled"
    job = await claim_one(connection, user_id, schema_name=settings.schema, kinds=KINDS, role=settings.role)
    if job is None:
        return "idle"
    if job.kind == JOB_KIND:
        return await _rebuild(connection, user_id, job, settings)
    return await _extract(connection, user_id, job, settings)


async def _fail(
    connection: AsyncConnection, user_id: str, job: Job, settings: WorkerSettings, error: Exception
) -> str:
    """Record a failure that happened *before* the write, with the same backoff and dead-letter rule as one during it.

    Without this an extraction that raised (a provider outage, a malformed answer) left its job ``running`` until the
    lease expired ten minutes later, and the attempt budget was spent on waiting rather than on retrying.
    """

    async def reraise(conn: AsyncConnection, claimed: Job, tick: ClockTick) -> None:
        del conn, claimed, tick
        raise error

    result = await complete_one(connection, user_id, job, reraise, schema_name=settings.schema, role=settings.role)
    return f"{result.status}:{job.id}"


async def _extract(connection: AsyncConnection, user_id: str, job: Job, settings: WorkerSettings) -> str:
    """``semantic_extraction``: read the turn, call every model with no transaction open, then write."""
    if job.turn_id is None:
        return await _fail(connection, user_id, job, settings, LookupError("job_without_turn"))
    scope = f"/u/{user_id}/"
    try:
        turn, epoch = await _load_turn(connection, settings, user_id, job.turn_id)
        extraction: ExtractionResult = await settings.extractor.extract(
            ExtractionRequest(text=turn.content, occurred_at=turn.occurred_at.isoformat(), scope_path=scope)
        )

        async def dry_run(
            embedder: Embedder | None, relations: RelationAdjudicator | None, entities: EntityAdjudicator | None
        ) -> None:
            try:
                async with tenant_scope(connection, user_id, role=settings.role):
                    await apply_extraction(
                        connection,
                        user_id=user_id,
                        scope_path=scope,
                        turn=turn,
                        episode_id=None,
                        extraction=extraction,
                        embedder=embedder,
                        adjudicator=relations,
                        entity_adjudicator=entities,
                        schema_name=settings.schema,
                    )
                    raise _Rollback
            except _Rollback:
                pass

        sheet = await judgements.prepare(
            extraction,
            dry_run,
            embedder=settings.embedder,
            adjudicator=settings.adjudicator,
            entity_adjudicator=settings.entity_adjudicator,
        )
    except Exception as error:  # noqa: BLE001 - a failed job is data, recorded with backoff
        return await _fail(connection, user_id, job, settings, error)

    async def write(conn: AsyncConnection, claimed: Job, tick: ClockTick) -> None:
        del claimed
        # F5: everything above was read and judged at `epoch`. A deletion that committed since makes this write a
        # resurrection, so it is refused here, inside the write transaction, and `complete_one` dead-letters the job.
        await require_current_deletion_epoch(conn, user_id, epoch, schema_name=settings.schema)
        await apply_extraction(
            conn,
            user_id=user_id,
            scope_path=scope,
            turn=turn,
            episode_id=None,  # the fast lane already wrote it
            extraction=extraction,
            # replay only: inside this transaction no adapter may call a model
            embedder=sheet.embedder(),
            adjudicator=sheet.replay_relations() if settings.adjudicator is not None else None,
            entity_adjudicator=sheet.replay_entities() if settings.entity_adjudicator is not None else None,
            schema_name=settings.schema,
            clock_tick=tick,
        )

    result = await complete_one(connection, user_id, job, write, schema_name=settings.schema, role=settings.role)
    return f"{result.status}:{job.id}"


async def _rebuild(connection: AsyncConnection, user_id: str, job: Job, settings: WorkerSettings) -> str:
    """``rebuild_index``: two shapes that cannot share a transaction boundary.

    ``fts``/``vector`` run ``REINDEX ... CONCURRENTLY``, which PostgreSQL refuses inside a transaction block and which
    needs the index *owner*, so it runs on the worker's own autocommit connection as the connecting role — maintenance
    is the one thing the restricted role cannot do — and the job is then completed by a writer with nothing to write.
    ``closure_hash`` recomputes a derived column, so it runs inside the job's tenant transaction like other writes.
    """
    payload = job.payload if isinstance(job.payload, dict) else {}
    try:
        index = logical_index(payload.get("index"))
    except Exception as error:  # noqa: BLE001 - a malformed payload is a failed job, not a crashed worker
        return await _fail(connection, user_id, job, settings, error)
    if index == "closure_hash":

        async def reseal(conn: AsyncConnection, claimed: Job, tick: ClockTick) -> None:
            del tick
            await perform_inline(conn, claimed, schema_name=settings.schema)

        result = await complete_one(connection, user_id, job, reseal, schema_name=settings.schema, role=settings.role)
        return f"{result.status}:{job.id}"
    try:
        outcome = await reindex_concurrently(connection, index, schema_name=settings.schema)
    except Exception as error:  # noqa: BLE001
        return await _fail(connection, user_id, job, settings, error)
    logger.info("index_rebuild_completed", index=index, rebuilt=len(outcome.rebuilt), absent=len(outcome.absent))

    async def nothing(conn: AsyncConnection, claimed: Job, tick: ClockTick) -> None:
        del conn, claimed, tick

    result = await complete_one(connection, user_id, job, nothing, schema_name=settings.schema, role=settings.role)
    return f"{result.status}:{job.id}"


async def serve(
    connection: AsyncConnection, settings: WorkerSettings, stop: asyncio.Event, *, once: bool = False
) -> int:
    """The loop. Finishes the job in hand when ``stop`` is set, then returns: that is the graceful stop.

    Expired leases are returned on start-up **and on every idle cycle**, so a job leased by a worker that died is
    picked up within one poll interval by whichever worker is still alive, rather than at the next restart.
    """
    await _recover(connection, settings)
    while not stop.is_set():
        worked = 0
        for user in settings.users:
            if stop.is_set():
                break
            try:
                outcome = await run_once(connection, user, settings)
            except Exception as error:  # noqa: BLE001 - one bad tick must not stop the worker
                logger.warning("slow_lane_tick_failed", user_id=user, error_type=type(error).__name__)
                continue
            worked += outcome not in ("idle", "unreconciled")
        if once:
            break
        if not worked:
            await _recover(connection, settings)
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.idle_poll)
            except TimeoutError:
                pass
    logger.info("slow_lane_stopped")
    return 0


async def _recover(connection: AsyncConnection, settings: WorkerSettings) -> None:
    for user in settings.users:
        try:
            await recover_stale(connection, user, schema_name=settings.schema, role=settings.role)
        except Exception as error:  # noqa: BLE001 - recovery failing must not stop the loop; the probe will see it
            logger.warning("slow_lane_recovery_failed", user_id=user, error_type=type(error).__name__)


def settings_from_environment(environment: Mapping[str, str]) -> WorkerSettings:
    """Build the worker from ``CME_*`` variables. Every failure names a variable and never its value.

    The adapters are constructed here, from ``integrations``, so the loop above stays provider-free; this is the same
    split ``host.service.settings_from_environment`` makes.
    """
    from custom_memory_engine.integrations.onnx_embedder import OnnxEmbedder
    from custom_memory_engine.integrations.onnx_embedder import assets_from_environment as embedding_assets
    from custom_memory_engine.integrations.openai_compatible import (
        OpenAiCompatibleAdjudicator,
        OpenAiCompatibleEntityAdjudicator,
        OpenAiCompatibleExtractor,
    )
    from custom_memory_engine.integrations.provider import client_for

    def required(name: str) -> str:
        value = environment.get(name, "").strip()
        if not value:
            raise ValueError(f"{name.lower()}_required")
        return value

    users = tuple(item.strip() for item in environment.get("CME_WORKER_USERS", "").split(",") if item.strip())
    if not users:
        raise ValueError("cme_worker_users_required")
    protocol = environment.get("CME_LLM_PROTOCOL") or None
    base_url, api_key = required("CME_LLM_BASE_URL"), required("CME_LLM_API_KEY")
    extractor_model = required("CME_EXTRACTOR_MODEL")
    adjudicator_model = environment.get("CME_ADJUDICATOR_MODEL", "").strip() or extractor_model
    # Selected separately (V2-D107): entity adjudication is scored by its false merges, and the model that wins
    # relation adjudication is not the one that merges least. Defaults to the relation adjudicator when unset.
    entity_model = environment.get("CME_ENTITY_ADJUDICATOR_MODEL", "").strip() or adjudicator_model

    def client(model: str) -> object:
        return client_for(base_url=base_url, api_key=api_key, model=model, protocol=protocol)

    assets = embedding_assets(dict(environment))
    adjudicating = environment.get("CME_ADJUDICATE", "1").strip() not in {"0", "false", "no"}
    return WorkerSettings(
        role=required("CME_RUNTIME_ROLE"),
        users=users,
        schema=environment.get("CME_SCHEMA", "cme_engine"),
        extractor=OpenAiCompatibleExtractor(client(extractor_model)),  # type: ignore[arg-type]
        embedder=OnnxEmbedder(assets) if assets is not None else None,
        adjudicator=OpenAiCompatibleAdjudicator(client(adjudicator_model)) if adjudicating else None,  # type: ignore[arg-type]
        entity_adjudicator=(
            OpenAiCompatibleEntityAdjudicator(client(entity_model)) if adjudicating else None  # type: ignore[arg-type]
        ),
        idle_poll=float(environment.get("CME_WORKER_IDLE_POLL", "5")),
    )
