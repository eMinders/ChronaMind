"""Lineage closure, two-phase classification and epoch guards for deletion (RL-070, E-037; ``V2-D09``, ``V2-D50``).

**Why the closure is unbounded (``V2-D09``, ``DELETION_ORDER.md`` O3).**
In v1, ``MAX_LINEAGE_DEPTH = 3`` silently stopped the walk at depth 3, leaving depth-4+ dependents alive with deleted
ancestors in their closure. Under V2, depth is never a correctness bound: :func:`lineage_closure` defaults to
``max_depth=None`` (unbounded) and walks until the frontier is empty.

**How a cycle terminates (``DELETION_ORDER.md`` F9).**
Write-time cycles are rejected by ``prevent_derivation_cycle()`` (migration 22). Even if a cycle is injected directly
with triggers disabled, :func:`lineage_closure` tracks every visited node in a ``visited`` set and never re-enqueues a
seen node, and :func:`classify_deletion_closure` finishes any cyclic component deterministically after Kahn's
topological pass, so neither function can hang.

**Two-phase ordering (``V2-D50`` rule 4).**
Phase A (:func:`classify_deletion_closure`) visits nodes **sources-first** (topological order from the seeds) because a
dependent's fate is a function of the *final* fates of its sources. Phase B applies physical removal
**dependents-first** (reverse topological order, edges before rows) so restrictive source foreign keys never fail.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from re import fullmatch
from typing import Literal, Protocol

from psycopg import AsyncConnection, sql
from psycopg.errors import InvalidParameterValue
from psycopg.rows import tuple_row

#: Historical v1 depth cap, retained as an optional parameter and reference constant (never a correctness cap).
MAX_LINEAGE_DEPTH = 3
type DeletionAblation = Literal["P", "A1", "A2", "A2b", "A8"]
type NodeFate = Literal["alive", "delete", "rederive", "retire"]

SURVIVING_FATES: frozenset[NodeFate] = frozenset({"alive", "rederive"})


class DeletionPolicyError(InvalidParameterValue):
    """Raised when a deletion policy or epoch does not satisfy its contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StaleDeletionEpoch(DeletionPolicyError):
    """Raised when a worker, verification or export snapshot started before a committed deletion."""

    def __init__(self, epoch_start: int, current_epoch: int) -> None:
        super().__init__("stale_deletion_epoch")
        self.epoch_start = epoch_start
        self.current_epoch = current_epoch


class CacheInvalidationError(RuntimeError):
    """Raised when an external cache refuses an epoch-keyed invalidation."""


class CacheInvalidator(Protocol):
    """External cache contract; cache keys include epoch and invalidation preserves newer entries."""

    async def invalidate_before_epoch(self, user_id: str, deletion_epoch: int) -> None: ...


def should_follow_lineage(ablation: DeletionAblation = "P") -> bool:
    """Return whether deletion follows derivation links for the selected system."""
    if ablation not in ("P", "A1", "A2", "A2b", "A8"):
        raise DeletionPolicyError("invalid_deletion_ablation")
    return ablation != "A8"


def lineage_closure(
    seed_ids: Sequence[str],
    derivation_edges: Iterable[tuple[str, str]],
    *,
    max_depth: int | None = None,
    follow_lineage: bool = True,
) -> tuple[str, ...]:
    """Return unique derived IDs reachable from ``seed_ids`` via ``(derived, source)`` edges.

    **Unbounded by default (``V2-D09``):** ``max_depth=None`` traverses the complete closure without a depth cutoff.
    Passing a non-negative integer ``max_depth`` bounds the walk as an explicit caller-requested parameter only.

    **Cycle termination (F9):** ``visited`` records every seed and discovered dependent on first sight, so an injected
    cycle never re-enters the frontier and the walk terminates in ``O(|V| + |E|)``.
    """
    if max_depth is not None and (isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0):
        raise DeletionPolicyError("invalid_lineage_depth")
    if any(not isinstance(item, str) or not item.strip() for item in seed_ids):
        raise DeletionPolicyError("invalid_lineage_seed")
    if not follow_lineage or max_depth == 0:
        return ()

    children: dict[str, list[str]] = {}
    for derived_id, source_id in derivation_edges:
        if not derived_id or not source_id:
            raise DeletionPolicyError("invalid_derivation_edge")
        children.setdefault(source_id, []).append(derived_id)

    seed_set = set(seed_ids)
    visited: set[str] = set(seed_set)
    discovered: list[str] = []
    frontier = list(dict.fromkeys(seed_ids))
    depth = 0
    while frontier and (max_depth is None or depth < max_depth):
        depth += 1
        next_frontier: list[str] = []
        for source_id in frontier:
            for derived_id in children.get(source_id, ()):
                if derived_id not in visited:
                    visited.add(derived_id)
                    discovered.append(derived_id)
                    next_frontier.append(derived_id)
        frontier = next_frontier
    return tuple(discovered)


@dataclass(frozen=True, slots=True)
class DeletionPlan:
    """Two-phase deletion plan produced by :func:`classify_deletion_closure` (``V2-D50``).

    ``classification_order`` is sources-first (Phase A). ``apply_order`` is dependents-first (Phase B).
    ``to_delete`` and ``to_retire`` follow ``apply_order`` (most-derived first); ``to_rederive`` follows
    ``classification_order`` (sources first) so transitive re-derivation regenerates a source before its dependents.
    """

    classification_order: tuple[str, ...]
    apply_order: tuple[str, ...]
    fates: Mapping[str, NodeFate]
    surviving_sources: Mapping[str, tuple[str, ...]]
    to_delete: tuple[str, ...]
    to_retire: tuple[str, ...]
    to_rederive: tuple[str, ...]


def classify_deletion_closure(
    seed_ids: Sequence[str],
    edges_by_dependent: Mapping[str, Sequence[str]],
    *,
    active_nodes: AbstractSet[str],
    threshold: int = 1,
    has_builder: Callable[[str], bool] | None = None,
) -> DeletionPlan:
    """Phase A: classify seeds and dependents sources-first (``V2-D50``, ``DELETION_ORDER.md``).

    Rules for a dependent ``x`` with direct sources ``S(x)`` and parameterized threshold ``threshold >= 1``:

    * ``x in seeds`` → ``delete``
    * ``survivors(x) = {s in S(x) : fate(s) in {alive, rederive}}`` (sources outside the closure are ``alive`` iff
      present in ``active_nodes``)
    * ``survivors(x) = empty`` → ``delete``
    * every ``s in S(x)`` has ``fate(s) == alive`` → ``alive``
    * ``|survivors(x)| >= threshold`` → ``rederive`` if ``has_builder(x)`` else ``retire`` (``V2-D50`` rule 2)
    * ``1 <= |survivors(x)| < threshold`` → ``retire`` (``reason = 'source_deleted'``)

    **Cycle safety (F9):** Kahn's algorithm orders the acyclic portion sources-first; any nodes trapped in an injected
    cycle are appended in deterministic sorted order so classification always terminates without hanging.
    """
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        raise DeletionPolicyError("positive_rederivation_threshold_required")
    seeds = tuple(dict.fromkeys(seed_ids))
    seed_set = set(seeds)
    nodes = set(seed_set) | set(edges_by_dependent)
    builder_for = has_builder if has_builder is not None else (lambda _node: False)

    # Build in-degree and forward adjacency within the closure subgraph for Kahn's sources-first order.
    in_degree: dict[str, int] = dict.fromkeys(nodes, 0)
    dependents_of: dict[str, list[str]] = {node: [] for node in nodes}
    for dependent, sources in edges_by_dependent.items():
        if dependent in seed_set:
            continue
        for source in dict.fromkeys(sources):
            if source in nodes and source != dependent:
                dependents_of[source].append(dependent)
                in_degree[dependent] += 1

    ready: deque[str] = deque(sorted(node for node, deg in in_degree.items() if deg == 0))
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for nxt in sorted(dependents_of[current]):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                ready.append(nxt)

    # If an injected cycle prevented some nodes from reaching in-degree 0, visit them deterministically (F9).
    if len(order) < len(nodes):
        remaining = sorted(nodes - set(order))
        order.extend(remaining)

    fates: dict[str, NodeFate] = {}
    surviving_sources: dict[str, tuple[str, ...]] = {}
    for node in order:
        if node in seed_set:
            fates[node] = "delete"
            continue
        sources = tuple(dict.fromkeys(edges_by_dependent.get(node, ())))
        if not sources:
            fates[node] = "alive" if node in active_nodes else "delete"
            continue
        # Only an active node with active surviving sources can survive.
        if node not in active_nodes:
            fates[node] = "delete"
            continue
        survivors: list[str] = []
        all_alive = True
        for source in sources:
            if source in fates:
                source_fate = fates[source]
            elif source in nodes:
                # Cycle member not yet classified: never treated as a surviving source.
                source_fate = "delete"
            else:
                source_fate = "alive" if source in active_nodes else "delete"
            if source_fate in SURVIVING_FATES:
                survivors.append(source)
            if source_fate != "alive":
                all_alive = False
        kept = tuple(survivors)
        if not kept:
            fates[node] = "delete"
        elif len(kept) == len(sources) and all_alive:
            fates[node] = "alive"
            surviving_sources[node] = kept
        elif len(kept) >= threshold:
            surviving_sources[node] = kept
            fates[node] = "rederive" if builder_for(node) else "retire"
        else:
            surviving_sources[node] = kept
            fates[node] = "retire"

    classification_order = tuple(order)
    apply_order = tuple(reversed(classification_order))
    return DeletionPlan(
        classification_order=classification_order,
        apply_order=apply_order,
        fates=fates,
        surviving_sources=surviving_sources,
        to_delete=tuple(node for node in apply_order if fates[node] == "delete"),
        to_retire=tuple(node for node in apply_order if fates[node] == "retire"),
        to_rederive=tuple(node for node in classification_order if fates[node] == "rederive"),
    )


async def invalidate_cache_entries(cache: CacheInvalidator, user_id: str, deletion_epoch: int) -> None:
    """Ask an external cache to evict entries older than this committed deletion epoch.

    The operation is deliberately idempotent: cache keys include ``deletion_epoch``,
    so a repeated notification for the same epoch cannot invalidate newer entries.
    Callers should invoke this after the database transaction commits. Failures are
    surfaced because the database deletion is already committed and must be retried
    with this same epoch.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise DeletionPolicyError("nonblank_user_id_required")
    if isinstance(deletion_epoch, bool) or not isinstance(deletion_epoch, int) or deletion_epoch < 1:
        raise DeletionPolicyError("positive_deletion_epoch_required")
    try:
        await cache.invalidate_before_epoch(user_id, deletion_epoch)
    except Exception as exc:
        raise CacheInvalidationError("cache_epoch_invalidation_failed") from exc


async def current_deletion_epoch(
    connection: AsyncConnection,
    user_id: str,
    *,
    schema_name: str = "cme_engine",
) -> int:
    """The user's committed deletion epoch, or ``0`` when no deletion has happened.

    One definition of "how stale is this observation", used by every operation that captures a view and must later
    refuse it if a deletion has intervened (``F5``/``F11``): ``MAX`` over the append-only request log, which is what
    ``delete`` increments. Zero is a real value -- "nothing has been deleted yet" -- so it is never confused with a
    missing row.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT COALESCE(MAX(deletion_epoch), 0) FROM {r} WHERE user_id = %s").format(
                r=sql.Identifier(schema_name, "deletion_request")
            ),
            (user_id,),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row is not None else 0


async def require_current_deletion_epoch(
    connection: AsyncConnection,
    user_id: str,
    epoch_start: int,
    *,
    schema_name: str = "cme_engine",
) -> None:
    """Abort stale work (jobs, verifications, export snapshots) unless ``epoch_start`` is still current."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise DeletionPolicyError("nonblank_user_id_required")
    if isinstance(epoch_start, bool) or not isinstance(epoch_start, int) or epoch_start < 0:
        raise DeletionPolicyError("nonnegative_epoch_start_required")
    if (
        not isinstance(schema_name, str)
        or len(schema_name) > 63
        or fullmatch(r"cme_[a-z][a-z0-9_]*", schema_name) is None
    ):
        raise DeletionPolicyError("isolated_engine_schema_identifier_required")

    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT COALESCE(MAX(deletion_epoch), 0) FROM {r} WHERE user_id = %s").format(
                r=sql.Identifier(schema_name, "deletion_request")
            ),
            (user_id,),
        )
        row = await cursor.fetchone()
    current_epoch = int(row[0]) if row else 0
    if current_epoch != epoch_start:
        raise StaleDeletionEpoch(epoch_start, current_epoch)


async def guard_verification_commit(
    connection: AsyncConnection,
    user_id: str,
    epoch_start: int,
    *,
    schema_name: str = "cme_engine",
) -> None:
    """F5 guard: serialise a verification commit against deletion and reject stale epochs.

    Acquires the same per-user advisory lock ``delete_memory`` holds before checking
    :func:`require_current_deletion_epoch`, so a verification started before a deletion cannot commit after it.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise DeletionPolicyError("nonblank_user_id_required")
    async with connection.cursor() as cursor:
        await cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (user_id,))
    await require_current_deletion_epoch(connection, user_id, epoch_start, schema_name=schema_name)


@dataclass(frozen=True, slots=True)
class ExportSnapshot:
    """Epoch-bound export snapshot (F11 / channel 11): invalidated once any deletion advances ``deletion_epoch``."""

    snapshot_id: str
    user_id: str
    captured_epoch: int
    payload: str


class ExportSnapshotStore:
    """Process-local store of epoch-bound export snapshots for F11 and channel-11 scanning.

    A snapshot captured at ``captured_epoch`` is served **only** while ``captured_epoch`` equals the user's current
    ``deletion_epoch``; any committed deletion immediately invalidates every older snapshot.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, ExportSnapshot] = {}

    def record(self, snapshot: ExportSnapshot) -> None:
        self._snapshots[snapshot.snapshot_id] = snapshot

    async def serve(
        self,
        connection: AsyncConnection,
        snapshot_id: str,
        *,
        schema_name: str = "cme_engine",
    ) -> ExportSnapshot:
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            raise DeletionPolicyError("export_snapshot_not_found")
        await require_current_deletion_epoch(
            connection, snapshot.user_id, snapshot.captured_epoch, schema_name=schema_name
        )
        return snapshot

    async def servable_payloads(
        self,
        connection: AsyncConnection,
        user_id: str,
        *,
        schema_name: str = "cme_engine",
    ) -> tuple[str, ...]:
        """Payloads of snapshots still servable for ``user_id`` at the current deletion epoch (channel 11 surface)."""
        current = await current_deletion_epoch(connection, user_id, schema_name=schema_name)
        return tuple(
            snap.payload
            for snap in self._snapshots.values()
            if snap.user_id == user_id and snap.captured_epoch == current
        )


__all__ = [
    "MAX_LINEAGE_DEPTH",
    "SURVIVING_FATES",
    "CacheInvalidationError",
    "CacheInvalidator",
    "DeletionAblation",
    "DeletionPlan",
    "DeletionPolicyError",
    "ExportSnapshot",
    "ExportSnapshotStore",
    "NodeFate",
    "StaleDeletionEpoch",
    "classify_deletion_closure",
    "current_deletion_epoch",
    "guard_verification_commit",
    "invalidate_cache_entries",
    "lineage_closure",
    "require_current_deletion_epoch",
    "should_follow_lineage",
]
