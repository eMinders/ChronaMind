"""Transactional deletion through cognition (RL-070, E-037; ``V2-D09``, ``V2-D50``, ``V2-D51``, ``T-29``/``S-08``).

One transaction, global lock order (``V2-D51``), one deletion-epoch increment, unbounded closure:

1. Acquire scope locks in global order (``scope.locks.lock_order``) and the per-user advisory lock so concurrent
   deletions serialise without deadlock (F13).
2. Allocate ``deletion_epoch`` and record ``DELETION_REQUEST`` with ``target_ref`` stored as a **keyed HMAC-SHA256
   digest** (``T-29``), never plaintext — preserving idempotent retries while keeping deleted topics out of receipts.
3. Walk ``MEMORY_DERIVATION`` upward with **no depth bound** (``WITH RECURSIVE ... UNION``, ``V2-D09``, cycle-safe F9).
4. **Phase A (classify, sources-first):** read all derivation edges *before* removing any edge (F3), then classify
   every dependent as ``delete``, ``retire``, ``rederive`` or ``alive`` via
   :func:`~custom_memory_engine.deletion.closure.classify_deletion_closure` with parameterized ``threshold``.
5. **Phase B (apply, dependents-first):** remove edges, clear ``memory_conflict.existing_version_id``, physically
   delete ``to_delete`` dependents-first, tombstone ``to_retire`` via :func:`retire_version` (removing content,
   rendered bounds and vectors in the same transaction, ``V2-D50`` rule 3), rebuild ``to_rederive`` sources-first via
   :func:`rederive_summary` / :func:`rederive_version`, purge queued ``JOB`` rows, and (when requested, ``S-08``)
   delete exclusive evidence ``turn`` rows through ``delete_exclusive_turns`` in the same transaction.
6. Write one content-free ``AUDIT_EVENT``: counts only, never content, hashes, subjects or predicates.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from secrets import token_bytes
from typing import Literal
from uuid import uuid4

import structlog
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_none

from custom_memory_engine.clock import ClockTick, allocate_clock
from custom_memory_engine.deletion.closure import (
    MAX_LINEAGE_DEPTH,
    classify_deletion_closure,
    should_follow_lineage,
)
from custom_memory_engine.deletion.rederive import (
    SummaryBuilder,
    rederive_summary,
    rederive_version,
    retire_version,
)
from custom_memory_engine.engine.transactions import AblationMode
from custom_memory_engine.scope.locks import lock_order, must_retry
from custom_memory_engine.scope.paths import user_scope
from custom_memory_engine.security.audit_guard import content_free_details

logger = structlog.get_logger(__name__)

type DeletionScope = Literal["memory", "topic", "all"]
type RetireHook = Callable[[AsyncConnection, str, str, Sequence[str]], Awaitable[None]]
type BeforeLockHook = Callable[[int], Awaitable[None]]

DEFAULT_MAX_LOCK_RETRIES = 4

# Deferrable constraint triggers that judge a final state: Invariant 10 (migration 1) and the lifecycle records
# (migrations 10 and 14), so deletion defers them and re-checks them before it returns.
ARCHIVAL_GUARD_TRIGGERS = (
    "trg_memory_version_archival_invariant",
    "trg_memory_derivation_archival_coverage",
    "trg_memory_version_transition_records",
    "trg_memory_version_record_of_creation",
    "trg_memory_version_record_of_entry",
)

RECEIPT_DIGEST_PREFIX = "hmac_sha256:"
_RECEIPT_DOMAIN = "del-target-v1"
# Deployment key from environment when configured; otherwise an ephemeral per-process 256-bit key (never a hardcoded
# secret) so idempotent retries within one process/test run match without storing plaintext targets (T-29).
_PROCESS_RECEIPT_KEY: bytes = (
    os.environ["CME_DELETION_RECEIPT_KEY"].encode("utf-8")
    if os.environ.get("CME_DELETION_RECEIPT_KEY")
    else token_bytes(32)
)


def target_ref_digest(key: bytes, user_id: str, scope: DeletionScope, target_ref: str) -> str:
    """Compute the keyed HMAC-SHA256 digest stored in ``deletion_request.target_ref`` (``T-29``, ``RL-072``).

    Why a keyed digest instead of plaintext: storing the deleted ``memory_key`` or topic name in ``deletion_request``
    tells anyone reading receipts *what* was erased. A keyed HMAC-SHA256 digest is irreversible without the deployment
    key, binds ``(scope, user_id, target_ref)`` so two scopes/tenants cannot collide, and stays deterministic across
    idempotent retries for the same ``deletion_epoch``.
    """
    if not isinstance(key, bytes) or not key:
        raise DeletionError("deletion_receipt_key_required")
    payload = f"{_RECEIPT_DOMAIN}:{scope}:{user_id}:{target_ref}".encode()
    return RECEIPT_DIGEST_PREFIX + hmac.new(key, payload, sha256).hexdigest()


class DeletionError(InvalidParameterValue):
    """Static deletion contract error (SQLSTATE 22023) without private content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExternalExclusion:
    """An external source the deleted versions cited, which the engine cannot erase (D-ENG-04).

    Identified by its SHA-256 ``content_hash`` (content-free), so the caller can find the artifact it holds. It is
    returned to the caller on the original call only, never audited. Like WAL and backups, it lies outside the
    deletion boundary; the receipt lists it so nothing pretends it was erased.
    """

    source_type: str
    content_hash: str


@dataclass(frozen=True)
class DeletionReceipt:
    """Content-free deletion outcome suitable for receipts and structured logs (``T-29``, ``RL-072``)."""

    request_id: str
    scope: DeletionScope
    deletion_epoch: int
    deleted_versions: int
    dependents: int
    rederived_dependents: int
    purged_jobs: int
    external_exclusions: tuple[ExternalExclusion, ...] = ()
    # always set; an idempotent replay reports this count only, because the audit log never holds hashes (RL-072)
    external_exclusion_count: int = 0
    retired_dependents: int = 0
    deleted_turns: int = 0


class _ClosureGrewError(Exception):
    """Internal signal that the deletion closure gained new scopes under the locks (``V2-D51`` step 3)."""

    def __init__(self, scopes: frozenset[str]) -> None:
        super().__init__("closure_grew_under_lock")
        self.scopes = scopes


async def _verify_user_context(connection: AsyncConnection, user_id: str) -> None:
    if not isinstance(user_id, str) or not user_id.strip():
        raise DeletionError("nonblank_user_id_required")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        row = await cursor.fetchone()
        if row is None or row[0] != user_id:
            raise InsufficientPrivilege("deletion_user_context_mismatch")


async def _acquire_deletion_locks(
    connection: AsyncConnection, user_id: str, scopes: Sequence[str] = ()
) -> tuple[tuple[str, ...], frozenset[str]]:
    """Acquire owner-namespace and per-scope advisory locks in global UTF-8 byte order (``V2-D51``, F13)."""
    owner_keys = lock_order([user_scope(user_id).text, *scopes])
    scope_paths = tuple(sorted({s for s in scopes if s}, key=lambda item: item.encode("utf-8")))
    async with connection.cursor(row_factory=tuple_row) as cursor:
        for key in owner_keys:
            await cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (key,))
        for path in scope_paths:
            await cursor.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (f"scope:{path}",)
            )
        # Retain v1 per-user key shared with queue workers.
        await cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (user_id,))
    return owner_keys, frozenset(scope_paths)


async def _table_has_column(cursor: object, schema_name: str, table_name: str, column_name: str) -> bool:
    await cursor.execute(  # type: ignore[attr-defined]
        "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid = %s::pg_catalog.regclass "
        "AND attname = %s AND NOT attisdropped)",
        (f"{schema_name}.{table_name}", column_name),
    )
    row = await cursor.fetchone()  # type: ignore[attr-defined]
    return bool(row[0]) if row else False


async def _discover_closure_and_scopes(
    cursor: object,
    user_id: str,
    scope: DeletionScope,
    target_ref: str,
    follows_lineage: bool,
    versions: sql.Identifier,
    derivations: sql.Identifier,
    *,
    has_scope_path: bool,
) -> tuple[list[str], list[str], frozenset[str]]:
    """Read seeds, unbounded dependents, and the canonical scope set of every node in the closure."""
    scope_expr = sql.SQL("scope_path") if has_scope_path else sql.SQL("NULL::text")
    v_scope_expr = sql.SQL("v.scope_path") if has_scope_path else sql.SQL("NULL::text")
    if scope == "all":
        await cursor.execute(  # type: ignore[attr-defined]
            sql.SQL("SELECT id, {s} FROM {v} WHERE user_id = %s ORDER BY id").format(s=scope_expr, v=versions),
            (user_id,),
        )
    elif scope == "topic":
        await cursor.execute(  # type: ignore[attr-defined]
            sql.SQL(
                "SELECT id, {s} FROM {v} WHERE user_id = %s AND (memory_key = %s OR subject_key = %s) ORDER BY id"
            ).format(s=scope_expr, v=versions),
            (user_id, target_ref, target_ref),
        )
    else:
        await cursor.execute(  # type: ignore[attr-defined]
            sql.SQL("SELECT id, {s} FROM {v} WHERE user_id = %s AND memory_key = %s ORDER BY id").format(
                s=scope_expr, v=versions
            ),
            (user_id, target_ref),
        )
    seed_rows = await cursor.fetchall()  # type: ignore[attr-defined]
    seeds = [str(row[0]) for row in seed_rows]
    scopes: set[str] = {str(row[1]) for row in seed_rows if row[1]}
    if not seeds:
        return [], [], frozenset(scopes)

    dependents: list[str] = []
    if follows_lineage:
        await cursor.execute(  # type: ignore[attr-defined]
            sql.SQL(
                """
                WITH RECURSIVE walk(version_id) AS (
                  SELECT id FROM {v} WHERE user_id = %(user_id)s AND id = ANY(%(seeds)s)
                  UNION
                  SELECT d.derived_version_id
                  FROM walk
                  JOIN {d} AS d ON d.source_version_id = walk.version_id AND d.user_id = %(user_id)s
                )
                SELECT w.version_id, {vs}
                FROM walk AS w
                JOIN {v} AS v ON v.id = w.version_id AND v.user_id = %(user_id)s
                WHERE NOT (w.version_id = ANY(%(seeds)s))
                ORDER BY w.version_id
                """
            ).format(v=versions, d=derivations, vs=v_scope_expr),
            {"user_id": user_id, "seeds": seeds},
        )
        dep_rows = await cursor.fetchall()  # type: ignore[attr-defined]
        dependents = [str(row[0]) for row in dep_rows]
        scopes.update(str(row[1]) for row in dep_rows if row[1])
    return seeds, dependents, frozenset(scopes)


async def delete_memory(
    connection: AsyncConnection,
    user_id: str,
    *,
    scope: DeletionScope = "memory",
    target_ref: str = "",
    ablation: AblationMode = "P",
    deletion_epoch: int | None = None,
    summary_builder: SummaryBuilder | None = None,
    rederivation_builder: SummaryBuilder | None = None,
    threshold: int = 1,
    delete_exclusive_sources: bool = False,
    receipt_key: bytes | None = None,
    retire_hook: RetireHook | None = None,
    before_lock_hook: BeforeLockHook | None = None,
    max_lock_retries: int = DEFAULT_MAX_LOCK_RETRIES,
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
) -> DeletionReceipt:
    """Delete a memory target through its entire unbounded cognitive closure in one transaction.

    * **Unbounded closure (``V2-D09``):** traverses ``memory_derivation`` with ``WITH RECURSIVE ... UNION`` and no
      depth limit; terminates even on an injected cycle (F9).
    * **Two-phase ordering (``V2-D50``):** classifies dependents sources-first after reading all edges (F1, F3),
      applies deletes dependents-first, tombstones retired dependents (removing content, bounds and vectors), and
      re-derives surviving dependents sources-first (so transitive dependents see rebuilt content).
    * **Threshold (F8):** parameterized ``threshold >= 1`` (default ``1``); dependents with ``>= threshold``
      surviving sources and an available builder are re-derived, while dependents below ``threshold`` or without a
      builder are retired and tombstoned.
    * **``T-29``/``S-08``:** ``deletion_request.target_ref`` stores an HMAC-SHA256 keyed digest (never plaintext);
      when ``delete_exclusive_sources=True``, turns cited exclusively by deleted versions are removed in the same
      transaction through ``delete_exclusive_turns``.
    * **``A8``:** positive-control ablation that intentionally skips lineage following.
    """
    if scope not in ("memory", "topic", "all"):
        raise DeletionError("invalid_deletion_scope")
    if scope != "all" and (not isinstance(target_ref, str) or not target_ref.strip()):
        raise DeletionError("nonblank_target_ref_required")
    follows_lineage = should_follow_lineage(ablation)
    if deletion_epoch is not None and (
        isinstance(deletion_epoch, bool) or not isinstance(deletion_epoch, int) or deletion_epoch < 1
    ):
        raise DeletionError("positive_deletion_epoch_required")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        raise DeletionError("positive_rederivation_threshold_required")

    if isinstance(max_lock_retries, bool) or not isinstance(max_lock_retries, int) or max_lock_retries < 1:
        raise DeletionError("positive_lock_retries_required")

    key = receipt_key if receipt_key is not None else _PROCESS_RECEIPT_KEY
    stored_target_ref = target_ref_digest(key, user_id, scope, target_ref)

    await _verify_user_context(connection, user_id)

    versions = sql.Identifier(schema_name, "memory_version")
    derivations = sql.Identifier(schema_name, "memory_derivation")
    evidence = sql.Identifier(schema_name, "memory_evidence")
    audit = sql.Identifier(schema_name, "audit_event")
    requests = sql.Identifier(schema_name, "deletion_request")
    jobs = sql.Identifier(schema_name, "job")
    conflicts = sql.Identifier(schema_name, "memory_conflict")
    exclusive_turns_fn = sql.Identifier(schema_name, "delete_exclusive_turns")
    archival_guards = sql.SQL(", ").join(sql.Identifier(schema_name, name) for name in ARCHIVAL_GUARD_TRIGGERS)

    accumulated_scopes: set[str] = set()
    attempt_number = 0
    try:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(_ClosureGrewError),
            stop=stop_after_attempt(max_lock_retries),
            wait=wait_none(),
            reraise=True,
        ):
            with attempt:
                attempt_number += 1
                return await _delete_memory_once(
                    connection,
                    user_id,
                    scope=scope,
                    target_ref=target_ref,
                    stored_target_ref=stored_target_ref,
                    follows_lineage=follows_lineage,
                    deletion_epoch=deletion_epoch,
                    summary_builder=summary_builder,
                    rederivation_builder=rederivation_builder,
                    threshold=threshold,
                    delete_exclusive_sources=delete_exclusive_sources,
                    retire_hook=retire_hook,
                    before_lock_hook=before_lock_hook,
                    attempt_number=attempt_number,
                    accumulated_scopes=accumulated_scopes,
                    schema_name=schema_name,
                    clock_tick=clock_tick,
                    replay_time=replay_time,
                    versions=versions,
                    derivations=derivations,
                    evidence=evidence,
                    audit=audit,
                    requests=requests,
                    jobs=jobs,
                    conflicts=conflicts,
                    exclusive_turns_fn=exclusive_turns_fn,
                    archival_guards=archival_guards,
                )
    except _ClosureGrewError:
        raise DeletionError("closure_growth_retry_exhausted") from None

    raise DeletionError("closure_growth_retry_exhausted")


async def _delete_memory_once(
    connection: AsyncConnection,
    user_id: str,
    *,
    scope: DeletionScope,
    target_ref: str,
    stored_target_ref: str,
    follows_lineage: bool,
    deletion_epoch: int | None,
    summary_builder: SummaryBuilder | None,
    rederivation_builder: SummaryBuilder | None,
    threshold: int,
    delete_exclusive_sources: bool,
    retire_hook: RetireHook | None,
    before_lock_hook: BeforeLockHook | None,
    attempt_number: int,
    accumulated_scopes: set[str],
    schema_name: str,
    clock_tick: ClockTick | None,
    replay_time: datetime | None,
    versions: sql.Identifier,
    derivations: sql.Identifier,
    evidence: sql.Identifier,
    audit: sql.Identifier,
    requests: sql.Identifier,
    jobs: sql.Identifier,
    conflicts: sql.Identifier,
    exclusive_turns_fn: sql.Identifier,
    archival_guards: sql.Composable,
) -> DeletionReceipt:
    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            has_scope_path = await _table_has_column(cursor, schema_name, "memory_version", "scope_path")
            has_kind = await _table_has_column(cursor, schema_name, "memory_version", "kind")
            # V2-D51 step 1: pre-lock closure discovery to collect the scope set S.
            _, _, pre_scopes = await _discover_closure_and_scopes(
                cursor,
                user_id,
                scope,
                target_ref,
                follows_lineage,
                versions,
                derivations,
                has_scope_path=has_scope_path,
            )
        target_scopes = accumulated_scopes | set(pre_scopes)
        if before_lock_hook is not None:
            await before_lock_hook(attempt_number)

        # V2-D51 step 2: acquire all scope locks in one global UTF-8 byte order before any write.
        held_owner_keys, held_scope_paths = await _acquire_deletion_locks(connection, user_id, sorted(target_scopes))

        async with connection.cursor(row_factory=tuple_row) as cursor:
            # V2-D51 step 3: recompute closure under the locks; if its scope set grew, roll back and retry.
            seeds, dependents, post_scopes = await _discover_closure_and_scopes(
                cursor,
                user_id,
                scope,
                target_ref,
                follows_lineage,
                versions,
                derivations,
                has_scope_path=has_scope_path,
            )
            if (post_scopes and must_retry(held_owner_keys, post_scopes)) or not (post_scopes <= held_scope_paths):
                accumulated_scopes |= set(post_scopes)
                logger.info(
                    "deletion_closure_grew_under_lock_retrying",
                    user_id=user_id,
                    attempt=attempt_number,
                    held_scope_count=len(held_scope_paths),
                    discovered_scope_count=len(post_scopes),
                )
                raise _ClosureGrewError(post_scopes)

            # Narrow session-local marker read by delete_exclusive_turns / clear_retired_tombstone_vectors (S-08).
            await cursor.execute("SELECT pg_catalog.set_config('app.deletion_active', '1', true)")

            if deletion_epoch is not None:
                await cursor.execute(
                    sql.SQL(
                        "SELECT id, scope, target_ref, status FROM {r} "
                        "WHERE user_id = %s AND deletion_epoch = %s ORDER BY requested_at DESC LIMIT 1"
                    ).format(r=requests),
                    (user_id, deletion_epoch),
                )
                previous = await cursor.fetchone()
                if previous is not None:
                    if (previous[1], previous[2]) != (scope, stored_target_ref) or previous[3] != "completed":
                        raise DeletionError("deletion_epoch_conflict")
                    await cursor.execute(
                        sql.SQL(
                            "SELECT details FROM {a} WHERE user_id = %s AND event_type = 'deletion_completed' "
                            "AND details->>'deletion_epoch' = %s ORDER BY event_at DESC LIMIT 1"
                        ).format(a=audit),
                        (user_id, str(deletion_epoch)),
                    )
                    audit_row = await cursor.fetchone()
                    details = audit_row[0] if audit_row else None
                    if not isinstance(details, dict):
                        raise DeletionError("completed_deletion_receipt_missing")
                    return DeletionReceipt(
                        request_id=previous[0],
                        scope=scope,
                        deletion_epoch=deletion_epoch,
                        deleted_versions=int(details.get("deleted_versions", 0)),
                        dependents=int(details.get("dependents", 0)),
                        rederived_dependents=int(details.get("re_derived_versions", 0)),
                        purged_jobs=int(details.get("purged_jobs", 0)),
                        # the audit log keeps the count only, so a replay reports how many but not which
                        external_exclusion_count=int(details.get("external_exclusions", 0)),
                        retired_dependents=int(details.get("retired_dependents", 0)),
                        deleted_turns=int(details.get("deleted_turns", 0)),
                    )

            tick = clock_tick or await allocate_clock(
                connection, user_id, schema_name=schema_name, replay_time=replay_time
            )

            await cursor.execute(
                sql.SQL("SELECT COALESCE(MAX(deletion_epoch), 0) + 1 FROM {r} WHERE user_id = %s").format(r=requests),
                (user_id,),
            )
            row = await cursor.fetchone()
            if row is None or not isinstance(row[0], int):
                raise DeletionError("deletion_epoch_allocation_failed")
            epoch: int = row[0]
            if deletion_epoch is not None and deletion_epoch != epoch:
                raise DeletionError("deletion_epoch_not_next")
            request_id = f"del-{uuid4().hex}"
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {r} (id, user_id, scope, target_ref, status, deletion_epoch, requested_at, "
                    "completed_at) VALUES (%s, %s, %s, %s, 'completed', %s, %s, %s)"
                ).format(r=requests),
                (request_id, user_id, scope, stored_target_ref, epoch, tick.instant, tick.instant),
            )

            if not seeds:
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                        "VALUES (%s, %s, %s, 'deletion_completed', %s, %s)"
                    ).format(a=audit),
                    (
                        f"audit-{uuid4().hex}",
                        user_id,
                        uuid4().hex,
                        tick.instant,
                        Jsonb(
                            content_free_details(
                                {
                                    "scope": scope,
                                    "deletion_epoch": epoch,
                                    "deleted_versions": 0,
                                    "dependents": 0,
                                    "re_derived_versions": 0,
                                    "purged_jobs": 0,
                                }
                            )
                        ),
                    ),
                )
                return DeletionReceipt(request_id, scope, epoch, 0, 0, 0, 0)

            # Phase A (F3): read ALL derivation edges and node statuses BEFORE removing any edge.
            edges_by_dependent: dict[str, list[str]] = {dep: [] for dep in dependents}
            edge_types_by_dependent: dict[str, set[str]] = {dep: set() for dep in dependents}
            active_nodes: set[str] = set()
            node_layers: dict[str, str] = {}
            node_kinds: dict[str, str | None] = {}
            if dependents:
                kind_expr = sql.SQL("derived.kind") if has_kind else sql.SQL("NULL::text")
                await cursor.execute(
                    sql.SQL(
                        "SELECT d.derived_version_id, d.source_version_id, d.derivation_type, "
                        "derived.status, derived.layer, {kind_expr}, source.status "
                        "FROM {d} AS d "
                        "JOIN {v} AS derived ON derived.id = d.derived_version_id AND derived.user_id = d.user_id "
                        "JOIN {v} AS source ON source.id = d.source_version_id AND source.user_id = d.user_id "
                        "WHERE d.user_id = %s AND d.derived_version_id = ANY(%s) "
                        "ORDER BY d.derived_version_id, d.source_version_id"
                    ).format(d=derivations, v=versions, kind_expr=kind_expr),
                    (user_id, dependents),
                )
                for (
                    derived_id,
                    source_id,
                    kind_edge,
                    derived_status,
                    derived_layer,
                    derived_kind,
                    source_status,
                ) in await cursor.fetchall():
                    d_id, s_id = str(derived_id), str(source_id)
                    edges_by_dependent.setdefault(d_id, []).append(s_id)
                    edge_types_by_dependent.setdefault(d_id, set()).add(str(kind_edge))
                    node_layers[d_id] = str(derived_layer)
                    node_kinds[d_id] = None if derived_kind is None else str(derived_kind)
                    if derived_status == "active":
                        active_nodes.add(d_id)
                    if source_status == "active":
                        active_nodes.add(s_id)

            def _builder_for(node_id: str) -> SummaryBuilder | None:
                # Re-derivation builders available: `rederivation_builder` rebuilds any active dependent;
                # `summary_builder` rebuilds semantic summaries whose edges are `summary` (v1 contract).
                if rederivation_builder is not None:
                    return rederivation_builder
                if (
                    summary_builder is not None
                    and node_layers.get(node_id) == "semantic"
                    and edge_types_by_dependent.get(node_id) == {"summary"}
                ):
                    return summary_builder
                return None

            plan = classify_deletion_closure(
                seeds,
                edges_by_dependent,
                active_nodes=active_nodes,
                threshold=threshold,
                has_builder=lambda node_id: _builder_for(node_id) is not None,
            )

            # Collect turns and external exclusions BEFORE physical deletion removes memory_evidence via FK.
            turn_ids: set[str] = set()
            external_exclusions: tuple[ExternalExclusion, ...] = ()
            evicted_ids = list(dict.fromkeys([*plan.to_delete, *plan.to_retire, *plan.to_rederive]))
            if evicted_ids:
                await cursor.execute(
                    sql.SQL("SELECT DISTINCT turn_id FROM {e} WHERE version_id = ANY(%s) AND user_id = %s").format(
                        e=evidence
                    ),
                    (evicted_ids, user_id),
                )
                turn_ids = {str(row[0]) for row in await cursor.fetchall() if row[0]}
                await cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid = %s::pg_catalog.regclass "
                    "AND attname = 'source_type' AND NOT attisdropped)",
                    (f"{schema_name}.memory_evidence",),
                )
                has_sources = bool((await cursor.fetchone() or (False,))[0])
                if has_sources:
                    await cursor.execute(
                        sql.SQL(
                            "SELECT DISTINCT source_type, content_hash FROM {e} "
                            "WHERE version_id = ANY(%s) AND user_id = %s AND source_type <> 'turn' "
                            "ORDER BY source_type, content_hash"
                        ).format(e=evidence),
                        (evicted_ids, user_id),
                    )
                    external_exclusions = tuple(
                        ExternalExclusion(str(row[0]), str(row[1])) for row in await cursor.fetchall()
                    )

            # Phase B: defer archival/lifecycle constraint triggers so they judge the committed final state.
            await cursor.execute(sql.SQL("SET CONSTRAINTS {} DEFERRED").format(archival_guards))

            # Remove derivation edges before source rows (restrictive source_version_id FK, O4).
            edges_to_unlink = list(dict.fromkeys([*plan.to_delete, *plan.to_retire, *plan.to_rederive]))
            if edges_to_unlink:
                await cursor.execute(
                    sql.SQL(
                        "DELETE FROM {d} WHERE user_id = %s AND "
                        "(source_version_id = ANY(%s) OR derived_version_id = ANY(%s))"
                    ).format(d=derivations),
                    (user_id, edges_to_unlink, edges_to_unlink),
                )

            if plan.to_delete:
                await cursor.execute(
                    sql.SQL(
                        "UPDATE {c} SET existing_version_id = NULL "
                        "WHERE user_id = %s AND existing_version_id = ANY(%s)"
                    ).format(c=conflicts),
                    (user_id, list(plan.to_delete)),
                )
                # Delete in dependents-first apply order (V2-D50 rule 4, O4).
                for version_id in plan.to_delete:
                    await cursor.execute(
                        sql.SQL("DELETE FROM {v} WHERE user_id = %s AND id = %s").format(v=versions),
                        (user_id, version_id),
                    )

        # Re-derive surviving dependents sources-first so transitive dependents read rebuilt source content (V2-D50).
        for version_id in plan.to_rederive:
            builder = _builder_for(version_id)
            assert builder is not None  # guaranteed by classify_deletion_closure's has_builder predicate
            is_v1_summary = (
                rederivation_builder is None
                and node_layers.get(version_id) == "semantic"
                and edge_types_by_dependent.get(version_id) == {"summary"}
            )
            if is_v1_summary:
                await rederive_summary(
                    connection,
                    user_id,
                    version_id,
                    plan.surviving_sources[version_id],
                    builder=builder,
                    schema_name=schema_name,
                    clock_tick=tick,
                )
            else:
                edge_types = edge_types_by_dependent.get(version_id, set())
                edge_kind = next(iter(edge_types)) if len(edge_types) == 1 else "re_derivation"
                await rederive_version(
                    connection,
                    user_id,
                    version_id,
                    plan.surviving_sources[version_id],
                    builder=builder,
                    derivation_type=edge_kind,
                    schema_name=schema_name,
                )

        # Retire dependents below threshold or lacking a builder (V2-D50 rules 2–3), tombstoning content & vectors.
        for version_id in plan.to_retire:
            kept_sources = plan.surviving_sources.get(version_id, ())
            if retire_hook is not None:
                await retire_hook(connection, user_id, version_id, kept_sources)
            else:
                await retire_version(connection, user_id, version_id, kept_sources, schema_name=schema_name)

        async with connection.cursor(row_factory=tuple_row) as cursor:
            purged_jobs = 0
            if turn_ids or evicted_ids:
                await cursor.execute(
                    sql.SQL(
                        "DELETE FROM {j} WHERE user_id = %s AND status <> 'running' AND "
                        "(turn_id = ANY(%s) OR EXISTS ("
                        "SELECT 1 FROM pg_catalog.unnest(%s::text[]) AS evicted(id) "
                        "WHERE pg_catalog.jsonb_path_exists(payload, '$.** ? (@ == $id)', "
                        "pg_catalog.jsonb_build_object('id', evicted.id))))"
                    ).format(j=jobs),
                    (user_id, sorted(turn_ids), evicted_ids),
                )
                purged_jobs = max(cursor.rowcount, 0)

            # S-08: delete exclusive evidence turns in the same transaction AFTER all version deletes & job purges
            # so a turn shared only by co-deleted versions is now exclusive and removed, while any turn still cited
            # by a surviving version or running job is retained.
            deleted_turns = 0
            if delete_exclusive_sources and turn_ids:
                await cursor.execute(
                    sql.SQL("SELECT {fn}(%s, %s)").format(fn=exclusive_turns_fn),
                    (user_id, sorted(turn_ids)),
                )
                turn_row = await cursor.fetchone()
                deleted_turns = int(turn_row[0]) if turn_row else 0

            # Retroactive check of every deferred archival/lifecycle trigger against the final state.
            await cursor.execute(sql.SQL("SET CONSTRAINTS {} IMMEDIATE").format(archival_guards))

            audit_payload: dict[str, object] = {
                "scope": scope,
                "deletion_epoch": epoch,
                "deleted_versions": len(plan.to_delete),
                "dependents": len(dependents),
                "re_derived_versions": len(plan.to_rederive),
                "purged_jobs": purged_jobs,
            }
            if external_exclusions:
                audit_payload["external_exclusions"] = len(external_exclusions)
            if plan.to_retire:
                audit_payload["retired_dependents"] = len(plan.to_retire)
            if deleted_turns:
                audit_payload["deleted_turns"] = deleted_turns

            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                    "VALUES (%s, %s, %s, 'deletion_completed', %s, %s)"
                ).format(a=audit),
                (
                    f"audit-{uuid4().hex}",
                    user_id,
                    uuid4().hex,
                    tick.instant,
                    Jsonb(content_free_details(audit_payload)),
                ),
            )

    logger.info(
        "memory_deletion_completed",
        user_id=user_id,
        scope=scope,
        deletion_epoch=epoch,
        deleted_versions=len(plan.to_delete),
        retired_dependents=len(plan.to_retire),
        re_derived_versions=len(plan.to_rederive),
        purged_jobs=purged_jobs,
        deleted_turns=deleted_turns,
        external_exclusions=len(external_exclusions),
    )
    return DeletionReceipt(
        request_id=request_id,
        scope=scope,
        deletion_epoch=epoch,
        deleted_versions=len(plan.to_delete),
        dependents=len(dependents),
        rederived_dependents=len(plan.to_rederive),
        purged_jobs=purged_jobs,
        external_exclusions=external_exclusions,
        external_exclusion_count=len(external_exclusions),
        retired_dependents=len(plan.to_retire),
        deleted_turns=deleted_turns,
    )


async def deletion_epoch(connection: AsyncConnection, user_id: str, *, schema_name: str = "cme_engine") -> int:
    """Current per-user deletion epoch, for slow-path jobs, verifications and snapshots to check."""
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT COALESCE(MAX(deletion_epoch), 0) FROM {r} WHERE user_id = %s").format(
                r=sql.Identifier(schema_name, "deletion_request")
            ),
            (user_id,),
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


__all__: Sequence[str] = (
    "BeforeLockHook",
    "DEFAULT_MAX_LOCK_RETRIES",
    "DeletionError",
    "DeletionReceipt",
    "ExternalExclusion",
    "MAX_LINEAGE_DEPTH",
    "RECEIPT_DIGEST_PREFIX",
    "RetireHook",
    "delete_memory",
    "deletion_epoch",
    "target_ref_digest",
)
