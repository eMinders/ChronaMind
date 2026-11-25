"""``export``: a durable, epoch-bound manifest of everything one caller could read (``API_V2.md`` §4.9, F11).

``API_V2.md`` says: "user-facing data export of the caller's read set; excludes content whose deletion is pending. A
snapshot made before a deletion must not be served after it (channel 11)". Three separate claims, and only the first
is obvious.

**What a snapshot is.** A manifest: the ids the caller could read, in sorted order, a SHA-256 digest over that order,
the read set's own scope paths, and the deletion epoch it was captured under. Stored as a row in ``export_snapshot``
(migration 25) under the caller's own row-level security. It is **not** a copy of the memories, and
that is the central design decision: a second copy of content is exactly what a deletion cannot reach, which is how an
export turns into a leak. Serving a snapshot re-reads through the ordinary read path, so every deletion, retraction and
expiry lands in the served result as it lands everywhere else.

**Channel 11, enforced.** ``captured_epoch`` is compared with the caller's *current* epoch (``MAX`` over the
append-only ``deletion_request`` log) on every serve. A deletion advances it, so every snapshot taken before it stops
being servable in the same instant the deletion commits — no trigger, no cache to invalidate, no window. The refusal
is a static code, never a hint about what the deletion removed.

**Bounded.** ``resource_limit`` is the contract's error for this operation, and it is real: a read set with more
artifacts than :data:`MAX_EXPORT_ARTIFACTS` is refused rather than materialised. The count is stated in the error
rather
than the ids, so a refusal reveals nothing the caller did not already have.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.deletion.closure import current_deletion_epoch, require_current_deletion_epoch
from custom_memory_engine.scope.access import ReadSet, read_predicate_named
from custom_memory_engine.scope.paths import ScopeError, parse_scope


class ExportError(ValueError):
    """A static code: never an id, a path or content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


#: How many artifacts one export may name. High enough for a normal personal corpus, low enough that the manifest is a
#: row rather than a table: the alternative is an export that quietly returns a prefix of the caller's memory.
MAX_EXPORT_ARTIFACTS = 10_000
#: How many selectors one export may name. The same bound the host applies to a read (`MAX_READ_SELECTORS`); repeated
#: here so the engine refuses it too, rather than trusting every transport to have done so.
MAX_EXPORT_SELECTORS = 8


@dataclass(frozen=True, slots=True)
class ExportManifest:
    """The answer. Counts, a digest and paths — never an id of a memory and never content."""

    snapshot_id: str
    captured_epoch: int
    artifact_count: int
    manifest_digest: str
    scope_paths: tuple[str, ...]
    captured_at: datetime
    #: ``True`` only on the first call; a replay of the same (read set, epoch) returns ``False`` and the same id.
    created: bool = True


def manifest_digest(ids: Sequence[str]) -> str:
    """SHA-256 over the sorted ids, newline-joined. Deterministic, and independent of the order the read produced."""
    digest = hashlib.sha256()
    for artifact_id in sorted(ids):
        digest.update(artifact_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def selectors(value: object) -> tuple[str, ...]:
    """The request's ``selectors``: 1..8 scope paths. A selector is a *request*, never a permission."""
    if value is None:
        raise ExportError("selectors_required")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ExportError("selectors_invalid")
    if not 1 <= len(value) <= MAX_EXPORT_SELECTORS:
        raise ExportError("selectors_invalid")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ExportError("selectors_invalid")
        try:
            paths.append(parse_scope(item).text)
        except ScopeError:
            raise ExportError("selector_invalid_scope") from None
    return tuple(dict.fromkeys(paths))


async def _readable_ids(connection: AsyncConnection, read_set: ReadSet, schema_name: str) -> tuple[str, ...]:
    """Every artifact the read set admits, sorted, bounded by one past the cap so truncation is *detected*.

    One statement, no channels, no ranking: an export reports what exists, so the read predicate is the whole of the
    filter (a relevance query here would export a page, not a corpus). The retired and tombstoned rows are excluded by
    the same predicate every read uses, so an export can never contain something ``query`` would not return.
    """
    read_filter, parameters = read_predicate_named(read_set, "m.scope_path", "m.visibility")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT m.id FROM {v} AS m WHERE {where} ORDER BY m.id LIMIT %(cap)s").format(
                v=sql.Identifier(schema_name, "memory_version"), where=read_filter
            ),
            {**parameters, "cap": MAX_EXPORT_ARTIFACTS + 1},
        )
        return tuple(str(row[0]) for row in await cursor.fetchall())


async def export_snapshot(
    connection: AsyncConnection,
    *,
    user_id: str,
    read_set: ReadSet,
    selected: tuple[str, ...],
    schema_name: str = "cme_engine",
) -> ExportManifest:
    """Capture the manifest, or return the existing one for an unchanged read set at the same epoch.

    The replay rule is what makes a retried export cheap and honest: the same read set at the same deletion epoch
    produces an identical manifest, so the row already there *is* the answer and re-inserting would mint a second id
    for the same content-free fact. A new deletion epoch always produces a new row, because the old one is no longer
    servable.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ExportError("nonblank_user_id_required")
    ids = await _readable_ids(connection, read_set, schema_name)
    if len(ids) > MAX_EXPORT_ARTIFACTS:
        raise ExportError("export_exceeds_artifact_limit")
    epoch = await current_deletion_epoch(connection, user_id, schema_name=schema_name)
    digest = manifest_digest(ids)
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id, captured_epoch, artifact_count, manifest_digest, scope_paths, captured_at"
                " FROM {t} WHERE user_id = %s AND captured_epoch = %s AND manifest_digest = %s"
                " ORDER BY captured_at DESC, id DESC LIMIT 1"
            ).format(t=sql.Identifier(schema_name, "export_snapshot")),
            (user_id, epoch, digest),
        )
        found = await cursor.fetchone()
    if found is not None:
        return ExportManifest(
            snapshot_id=str(found[0]),
            captured_epoch=int(found[1]),
            artifact_count=int(found[2]),
            manifest_digest=str(found[3]),
            scope_paths=tuple(str(path) for path in found[4]),
            captured_at=found[5],
            created=False,
        )
    snapshot_id = "exp-" + digest[:32]
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {t} (id, user_id, captured_epoch, artifact_count, manifest_digest, artifact_ids,"
                " scope_paths) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (id) DO NOTHING RETURNING captured_at"
            ).format(t=sql.Identifier(schema_name, "export_snapshot")),
            (
                snapshot_id,
                user_id,
                epoch,
                len(ids),
                digest,
                # The manifest's own `artifact_ids` column (a JSON array, CHECKed by the table), not an audit row:
                # the export's subject matter, stored under the caller's RLS, never in `audit_event`.
                Jsonb(list(ids)),
                list(selected),
            ),
        )
        inserted = await cursor.fetchone()
        if inserted is None:
            # The id is content-addressed over (epoch, digest), so this only happens on a concurrent identical export:
            # the answer is that row, not an error.
            await cursor.execute(
                sql.SQL("SELECT captured_at FROM {t} WHERE user_id = %s AND id = %s").format(
                    t=sql.Identifier(schema_name, "export_snapshot")
                ),
                (user_id, snapshot_id),
            )
            row = await cursor.fetchone()
            captured_at = row[0] if row is not None else None
        else:
            captured_at = inserted[0]
    if captured_at is None:
        raise ExportError("snapshot_not_recorded")
    return ExportManifest(snapshot_id, epoch, len(ids), digest, selected, captured_at)


async def serve_snapshot(
    connection: AsyncConnection,
    *,
    user_id: str,
    snapshot_id: str,
    schema_name: str = "cme_engine",
) -> tuple[ExportManifest, tuple[str, ...]]:
    """Read one manifest back, refusing it if a deletion has intervened (channel 11). Returns ``(manifest, ids)``.

    ``require_current_deletion_epoch`` is the same guard a verification commit uses (``F5``): it takes the per-user
    advisory lock and compares, so a serve racing a deletion either completes before it or is refused after it. There
    is no third outcome, which is what makes this a guarantee rather than a check.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT captured_epoch, artifact_count, manifest_digest, scope_paths, captured_at, artifact_ids"
                " FROM {t} WHERE user_id = %s AND id = %s"
            ).format(t=sql.Identifier(schema_name, "export_snapshot")),
            (user_id, snapshot_id),
        )
        row = await cursor.fetchone()
    if row is None:
        raise ExportError("snapshot_not_found")
    try:
        await require_current_deletion_epoch(connection, user_id, int(row[0]), schema_name=schema_name)
    except Exception as error:  # noqa: BLE001 - StaleDeletionEpoch and its base both mean "not servable"
        raise ExportError("snapshot_invalidated_by_a_deletion") from error
    ids = tuple(str(item) for item in (row[5] or []))
    if manifest_digest(ids) != str(row[2]) or len(ids) != int(row[1]):
        # A manifest that does not match its own digest is not a manifest. The append-only trigger makes this
        # unreachable in practice; checking it anyway means the serve never trusts a row more than arithmetic.
        raise ExportError("snapshot_manifest_is_inconsistent")
    return (
        ExportManifest(
            snapshot_id, int(row[0]), int(row[1]), str(row[2]), tuple(str(p) for p in row[3]), row[4], False
        ),
        ids,
    )


__all__ = [
    "MAX_EXPORT_ARTIFACTS",
    "MAX_EXPORT_SELECTORS",
    "ExportError",
    "ExportManifest",
    "export_snapshot",
    "manifest_digest",
    "selectors",
    "serve_snapshot",
]
