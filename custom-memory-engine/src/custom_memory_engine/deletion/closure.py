"""Lineage policy and cache/worker epoch guards for deletion (RL-070)."""

from collections.abc import Iterable, Sequence
from re import fullmatch
from typing import Literal, Protocol

from psycopg import AsyncConnection, sql
from psycopg.errors import InvalidParameterValue
from psycopg.rows import tuple_row

MAX_LINEAGE_DEPTH = 3
type DeletionAblation = Literal["P", "A1", "A2", "A2b", "A8"]


class DeletionPolicyError(InvalidParameterValue):
    """Raised when a deletion policy or epoch does not satisfy its contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StaleDeletionEpoch(DeletionPolicyError):
    """Raised when a worker started before a committed deletion."""

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
    max_depth: int = MAX_LINEAGE_DEPTH,
    follow_lineage: bool = True,
) -> tuple[str, ...]:
    """Return unique derived IDs at depths 1..``max_depth`` from ``(derived, source)`` edges."""
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
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

    depths: dict[str, int] = {}
    frontier = list(dict.fromkeys(seed_ids))
    for depth in range(1, max_depth + 1):
        next_frontier: list[str] = []
        for source_id in frontier:
            for derived_id in children.get(source_id, ()):
                if derived_id not in depths and derived_id not in seed_ids:
                    depths[derived_id] = depth
                    next_frontier.append(derived_id)
        frontier = next_frontier
        if not frontier:
            break
    return tuple(depths)


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


async def require_current_deletion_epoch(
    connection: AsyncConnection,
    user_id: str,
    epoch_start: int,
    *,
    schema_name: str = "cme_engine",
) -> None:
    """Abort stale work unless its captured deletion epoch is still current."""
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


__all__ = [
    "MAX_LINEAGE_DEPTH",
    "CacheInvalidationError",
    "CacheInvalidator",
    "DeletionPolicyError",
    "DeletionAblation",
    "StaleDeletionEpoch",
    "invalidate_cache_entries",
    "lineage_closure",
    "require_current_deletion_epoch",
    "should_follow_lineage",
]
