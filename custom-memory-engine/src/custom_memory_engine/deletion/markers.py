"""Erasure markers: what a deletion erased besides its target, surviving a restore (``T-46``, ``V2-D125``).

A deletion's journal entry (``V2-D122``) names its *target* by an HMAC digest. That is enough to re-apply it to a
backup that still holds the target, and nothing more: a restore also brings back everything else the deletion had
erased, and two of those things re-create the deleted memory on their own --

* **a turn and its extraction job.** A turn ingested before the backup whose claim was extracted and deleted after it
  comes back with its queued job, and the worker extracts the claim again;
* **a write's idempotency identity.** A claim written and deleted after the backup has no ``api_idempotency`` row in
  the restored database, so a client's resend of the same key -- the app resends its whole history every turn --
  writes it again.

So every deletion records, per request and in its own transaction, keyed digests of both: ``turn`` for each evidence
turn of a physically deleted version (for ``all``, every turn the user has), and ``write`` for each recorded
idempotency identity whose result it erased, with that result's engine-generated id. The journal exports them; a replay
inserts them again, uses the ``turn`` digests to find the turns the restored database still holds and erases them with
their jobs, and from then on :func:`replayed_write` answers a resend with the erased result -- exactly what the live
database answered, because there the ``api_idempotency`` row had survived the deletion.

Digests, never plaintext, for the reason ``deletion_request.target_ref`` is one (``T-29``): the journal leaves the
host. They are keyed with the deployment's deletion key and bound to the user and the marker's kind, so two tenants or
two kinds never collide and a digest names nothing without the key.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, LiteralString

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

type MarkerKind = Literal["write", "turn"]

MARKER_KINDS: tuple[MarkerKind, ...] = ("write", "turn")

#: What a reconciliation row records about *how* the database was reconciled (`T-49`, `V2-D128`). A fresh install and
#: an operator's assertion reconcile nothing; a replay is told what kind of restore it is reconciling after.
type ReconciledBy = Literal["fresh-install", "operator-assertion", "replay"]

#: The restore kinds a replay can be told about (`scripts/erasure_journal.py --restored-mode`). `in-place` is the
#: upgrade that restored nothing; the other three are the paths `deploy/README.md`'s restore procedure names.
type RestoredMode = Literal["logical", "data-only", "physical", "in-place"]

RESTORED_MODES: tuple[RestoredMode, ...] = ("logical", "data-only", "physical", "in-place")

_DOMAIN = "cme-erasure-marker-v1"


class MarkerError(ValueError):
    """A static, content-free refusal code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, order=True, slots=True)
class ErasureMarker:
    """One thing a deletion erased: its kind, a keyed digest of its identity and (``write`` only) the erased result."""

    kind: MarkerKind
    digest: str
    result_id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in MARKER_KINDS:
            raise MarkerError("erasure_marker_kind_invalid")
        if len(self.digest) != 64 or any(character not in "0123456789abcdef" for character in self.digest):
            raise MarkerError("erasure_marker_digest_invalid")
        if (self.kind == "write") != bool(self.result_id) or len(self.result_id) > 256:
            raise MarkerError("erasure_marker_result_invalid")


def _digest(key: bytes, user_id: str, kind: MarkerKind, *parts: str) -> str:
    if not isinstance(key, bytes) or not key:
        raise MarkerError("deletion_receipt_key_required")
    # a unit separator cannot occur in a validated user id, and the parts are length-prefixed: no two identities share
    # a payload
    body = "\x1f".join(f"{len(part)}:{part}" for part in (user_id, *parts))
    return hmac.new(key, f"{_DOMAIN}\x1f{kind}\x1f{body}".encode(), sha256).hexdigest()


def turn_digest(key: bytes, user_id: str, turn_id: str) -> str:
    """The digest a ``turn`` marker carries for ``turn_id``."""
    return _digest(key, user_id, "turn", turn_id)


def write_digest(key: bytes, user_id: str, operation: str, idempotency_key: str) -> str:
    """The digest a ``write`` marker carries for one recorded ``(operation, idempotency_key)``."""
    return _digest(key, user_id, "write", operation, idempotency_key)


async def record_markers(
    connection: AsyncConnection,
    *,
    user_id: str,
    request_id: str,
    markers: Iterable[ErasureMarker],
    schema_name: str,
) -> int:
    """Append ``markers`` under ``request_id`` (inside the deletion's transaction). Idempotent; returns the count."""
    unique = sorted(set(markers))
    if not unique:
        return 0
    async with connection.cursor() as cursor:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {m} (request_id, user_id, kind, digest, result_id) "
                "SELECT %s, %s, kind, digest, result_id "
                "FROM ROWS FROM (pg_catalog.unnest(%s::text[]), pg_catalog.unnest(%s::text[]), "
                "pg_catalog.unnest(%s::text[])) AS m(kind, digest, result_id) "
                "ON CONFLICT DO NOTHING"
            ).format(m=sql.Identifier(schema_name, "erasure_marker")),
            (
                request_id,
                user_id,
                [marker.kind for marker in unique],
                [marker.digest for marker in unique],
                [marker.result_id for marker in unique],
            ),
        )
    return len(unique)


async def write_markers_for(
    connection: AsyncConnection,
    *,
    user_id: str,
    result_ids: Sequence[str],
    key: bytes,
    schema_name: str,
) -> list[ErasureMarker]:
    """``write`` markers for every recorded idempotency identity that answered with one of ``result_ids``."""
    if not result_ids:
        return []
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT operation, idempotency_key, result_id FROM {i} WHERE user_id = %s AND result_id = ANY(%s)"
            ).format(i=sql.Identifier(schema_name, "api_idempotency")),
            (user_id, list(result_ids)),
        )
        rows = await cursor.fetchall()
    return [
        ErasureMarker("write", write_digest(key, user_id, str(operation), str(idem)), str(result))
        for operation, idem, result in rows
    ]


async def replayed_write(
    connection: AsyncConnection,
    *,
    user_id: str,
    operation: str,
    idempotency_key: str,
    key: bytes,
    schema_name: str,
) -> str | None:
    """The recorded result of ``(operation, idempotency_key)``: the live row, else an erased one; ``None`` when new.

    One statement, two primary-key probes: the live row answers first (the common case, and the only one before any
    deletion), and a ``write`` marker answers only for a write a deletion erased -- which, after a restore, is the
    resend that would otherwise write it again.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "(SELECT result_id FROM {i} WHERE user_id = %s AND operation = %s AND idempotency_key = %s) "
                "UNION ALL "
                "(SELECT result_id FROM {m} WHERE user_id = %s AND kind = 'write' AND digest = %s LIMIT 1) "
                "LIMIT 1"
            ).format(
                i=sql.Identifier(schema_name, "api_idempotency"),
                m=sql.Identifier(schema_name, "erasure_marker"),
            ),
            (user_id, operation, idempotency_key, user_id, write_digest(key, user_id, operation, idempotency_key)),
        )
        row = await cursor.fetchone()
    return None if row is None else str(row[0])


async def resolve_turns(
    connection: AsyncConnection,
    *,
    user_id: str,
    digests: Iterable[str],
    key: bytes,
    schema_name: str,
) -> list[str]:
    """The ids of this user's stored turns whose digest is among ``digests`` (a replay's view of its ``turn`` markers).

    Every stored turn of the user is a candidate: the turn a restore brings back may be cited by nothing yet (its
    extraction had not run when the backup was taken) or by a version the restored database still holds.
    """
    wanted = frozenset(digests)
    if not wanted:
        return []
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT id FROM {t} WHERE user_id = %s").format(t=sql.Identifier(schema_name, "turn")),
            (user_id,),
        )
        candidates = [str(row[0]) for row in await cursor.fetchall()]
    return sorted(turn_id for turn_id in candidates if turn_digest(key, user_id, turn_id) in wanted)


async def flag_erased_turns(
    connection: AsyncConnection, *, user_id: str, turn_ids: Sequence[str], schema_name: str
) -> int:
    """Flag the marked turns that are still stored, so no extraction runs on them again. Returns the number flagged."""
    if not turn_ids:
        return 0
    async with connection.cursor() as cursor:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {f} (turn_id, user_id) SELECT id, user_id FROM {t} "
                "WHERE user_id = %s AND id = ANY(%s) ON CONFLICT DO NOTHING"
            ).format(f=sql.Identifier(schema_name, "erased_turn"), t=sql.Identifier(schema_name, "turn")),
            (user_id, list(turn_ids)),
        )
        return max(cursor.rowcount, 0)


async def turn_is_erased(connection: AsyncConnection, *, user_id: str, turn_id: str, schema_name: str) -> bool:
    """Whether a deletion marked this stored turn: its extraction would re-derive what the user deleted."""
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT 1 FROM {f} WHERE user_id = %s AND turn_id = %s").format(
                f=sql.Identifier(schema_name, "erased_turn")
            ),
            (user_id, turn_id),
        )
        return await cursor.fetchone() is not None


async def markers_of(
    connection: AsyncConnection, *, request_ids: Sequence[str], schema_name: str
) -> dict[str, tuple[ErasureMarker, ...]]:
    """Every marker of ``request_ids``, by request (the journal export; needs ``BYPASSRLS``)."""
    found: dict[str, list[ErasureMarker]] = {}
    if not request_ids:
        return {}
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT request_id, kind, digest, result_id FROM {m} WHERE request_id = ANY(%s) "
                "ORDER BY request_id, kind, digest"
            ).format(m=sql.Identifier(schema_name, "erasure_marker")),
            (list(request_ids),),
        )
        for request_id, kind, digest, result_id in await cursor.fetchall():
            found.setdefault(str(request_id), []).append(ErasureMarker(kind, str(digest), str(result_id)))
    # Python's order, not the database collation's: the journal compares entries by value
    return {request_id: tuple(sorted(markers)) for request_id, markers in found.items()}


_IDENTITY: LiteralString = "pg_catalog.to_regclass(%s)::oid"

#: The cluster's timeline, from the WAL segment name of the current write position: recovery to a point in time ends on
#: a new timeline, so it separates a PITR from a live database whose data the restore put back byte for byte. Read this
#: way rather than through `pg_control_checkpoint()`, whose `timeline_id` reports the checkpoint record's timeline and
#: can lag a promotion until the next checkpoint. `NULL` in recovery, where there is no write position to name.
_TIMELINE: LiteralString = (
    "CASE WHEN pg_catalog.pg_is_in_recovery() THEN NULL ELSE "
    "('x' || pg_catalog.substr(pg_catalog.pg_walfile_name(pg_catalog.pg_current_wal_lsn()), 1, 8))"
    "::pg_catalog.bit(32)::bigint END"
)

#: Serializes identity appends, so the row a replay writes can count every row a later probe will see.
_APPEND_LOCK: LiteralString = "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s, 0))"


class TurnErased(MarkerError):
    """A deletion marked this turn: extracting it would re-derive what the user deleted (``erased_turn``)."""

    def __init__(self) -> None:
        super().__init__("turn_erased_by_deletion")


class RestoreNotReconciled(MarkerError):
    """This database is a restored copy the erasure journal has not been replayed into yet."""

    def __init__(self) -> None:
        super().__init__("restore_not_reconciled")


async def is_reconciled(connection: AsyncConnection, *, schema_name: str) -> bool:
    """Whether the erasure journal was replayed into the database this row describes (``restore_reconciliation``).

    One statement, four facts about the last identity row: it names this relation (a logical ``pg_restore`` recreates
    the table and moves the OID), it came from the transaction it records (a data-only ``COPY`` gives every restored
    row the restoring transaction's ``xmin``), it is the last row this table holds (``rows_before`` is the count it saw
    when it was written), and its cluster timeline is the current one (recovery to a point in time promotes to a new
    timeline). A template clone keeps all four -- it copies the data blocks -- so a clone of a reconciled database is
    reconciled, which every test fixture and an operator's own clone depend on.
    """
    table = sql.Identifier(schema_name, "restore_reconciliation")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT EXISTS (SELECT 1 FROM {t} AS r WHERE r.relation_oid = ("
                + _IDENTITY
                + ") AND pg_catalog.xid(r.written_xid) = r.xmin AND r.rows_before = ("
                "SELECT pg_catalog.count(*) FROM {t}) - 1 AND r.timeline IS NOT DISTINCT FROM " + _TIMELINE + ")"
            ).format(t=table),
            (f"{schema_name}.restore_reconciliation",),
        )
        row = await cursor.fetchone()
    return bool(row and row[0])


async def record_reconciliation(
    connection: AsyncConnection,
    *,
    schema_name: str,
    by: ReconciledBy,
    restored_mode: RestoredMode | None = None,
) -> None:
    """Append this database's identity: the journal is reconciled with it, by ``by``, after the given restore.

    Must run in a **top-level** transaction: the row records the transaction that inserted it and the probe compares
    that with the row's own ``xmin``, which is a savepoint's transaction inside a subtransaction. The append verifies
    itself and raises ``restore_identity_not_recorded`` rather than leaving an identity no probe will accept.
    """
    if by == "fresh-install":
        if restored_mode is not None:
            raise MarkerError("restore_mode_for_a_fresh_install")
    elif restored_mode not in RESTORED_MODES:
        raise MarkerError("restore_mode_required")
    table = sql.Identifier(schema_name, "restore_reconciliation")
    async with connection.cursor() as cursor:
        await cursor.execute(_APPEND_LOCK, (f"cme:restore_reconciliation:{schema_name}",))
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {t} (relation_oid, written_xid, rows_before, timeline, reconciled_by, restored_mode) "
                "SELECT "
                + _IDENTITY
                + ", pg_catalog.pg_current_xact_id(), (SELECT pg_catalog.count(*) FROM {t}), "
                + _TIMELINE
                + ", %s, %s"
            ).format(t=table),
            (f"{schema_name}.restore_reconciliation", by, restored_mode),
        )
    if not await is_reconciled(connection, schema_name=schema_name):
        raise MarkerError("restore_identity_not_recorded")


__all__ = [
    "MARKER_KINDS",
    "RESTORED_MODES",
    "ErasureMarker",
    "MarkerError",
    "MarkerKind",
    "ReconciledBy",
    "RestoreNotReconciled",
    "RestoredMode",
    "TurnErased",
    "flag_erased_turns",
    "is_reconciled",
    "markers_of",
    "record_markers",
    "record_reconciliation",
    "replayed_write",
    "resolve_turns",
    "turn_digest",
    "turn_is_erased",
    "write_digest",
    "write_markers_for",
]
