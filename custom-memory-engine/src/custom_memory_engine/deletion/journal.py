"""The erasure journal: deletions survive the restore of an older backup (``V2-D122``).

**The risk.** A backup taken before a deletion still holds the deleted rows. Restoring it brings them back, and the
``deletion_request`` rows that recorded the erasure are rolled back with everything else, so nothing in the restored
database even knows a deletion happened.

**The mechanism.** ``deletion_request`` is already an append-only, content-free log of every erasure: who, which
scope, an HMAC digest of the target (``T-29``), the per-user epoch. The journal is that log *copied out of the
database*, so it does not share the backup's fate:

* :func:`export_journal` reads every completed request (a ``BYPASSRLS`` role is required, and checked: under row-level
  security a role without it would read zero rows and write an empty journal that looks valid) and
  :func:`merge_journal` folds them into the journal file. The merge never drops an entry, so exporting from a freshly
  restored, older database cannot shrink the journal; a conflicting entry is refused.
* The journal header carries a key check (an HMAC of a constant under the deletion key), so a replay with the wrong
  key is refused instead of matching no target and silently recording every deletion as already done.
* :func:`replay_journal`, after a restore and before the engine serves traffic, re-applies every journalled request the
  restored database does not hold, per user in epoch order, through the engine's own deletion path
  (:meth:`~custom_memory_engine.api.delete_handlers.DeleteHandlers.replay`) under the restricted role and the user's
  own row-level security. The target is found by recomputing the digest over the user's restored keys (``memory_key``;
  for ``topic`` also ``subject_key`` and entity ids) with the deployment's deletion key: the journal never holds a
  plaintext target. A target the restored database never held is recorded with nothing to erase. The replayed request
  keeps its original id, digest and epoch, so a second replay does nothing.

What it does not cover is stated in ``deploy/README.md``: deletions after the newest journal export (its RPO), and a
journal lost together with the database.
"""

from __future__ import annotations

import hmac
import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import structlog
from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.api.delete_handlers import DeleteHandlers
from custom_memory_engine.engine.deletion import DeletionScope, target_ref_digest

logger = structlog.get_logger(__name__)

#: The first line of every journal file, so a wrong file is refused before anything is replayed.
JOURNAL_FORMAT = "cme-erasure-journal"
JOURNAL_VERSION = 1
_KEY_CHECK_MESSAGE = b"cme-erasure-journal:key-check:v1"

type ReplayOutcome = Literal["already_present", "replayed", "recorded_unresolved", "user_absent"]


class JournalError(ValueError):
    """A static, content-free refusal code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, order=True)
class JournalEntry:
    """One completed deletion request, exactly as ``deletion_request`` records it. Content-free."""

    user_id: str
    deletion_epoch: int
    request_id: str
    scope: DeletionScope
    target_ref: str
    requested_at: str


@dataclass(frozen=True)
class Journal:
    """A journal file: its entries and the key check of the deletion key its digests were made with."""

    entries: tuple[JournalEntry, ...]
    key_check: str | None


def key_check(key: bytes) -> str:
    """A one-way fingerprint of the deletion key: equal keys, equal checks; it reveals nothing about the key."""
    return hmac.new(key, _KEY_CHECK_MESSAGE, "sha256").hexdigest()


@dataclass(frozen=True)
class ReplayReport:
    """Counts only: what the replay found and did."""

    already_present: int
    replayed: int
    recorded_unresolved: int
    user_absent: int


# ------------------------------------------------------------------------------------------------------ export


async def export_journal(connection: AsyncConnection, *, schema_name: str = "cme_engine") -> list[JournalEntry]:
    """Every completed deletion request in ``schema_name``, ordered by user and epoch. Needs ``BYPASSRLS``."""
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT rolsuper OR rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user")
        row = await cursor.fetchone()
        if row is None or not row[0]:
            raise JournalError("journal_export_requires_bypassrls")
        await cursor.execute(
            sql.SQL(
                "SELECT user_id, deletion_epoch, id, scope, target_ref, requested_at FROM {r} "
                "WHERE status = 'completed' ORDER BY user_id, deletion_epoch"
            ).format(r=sql.Identifier(schema_name, "deletion_request"))
        )
        rows = await cursor.fetchall()
    return [
        JournalEntry(
            user_id=str(user_id),
            deletion_epoch=int(epoch),
            request_id=str(request_id),
            scope=cast(DeletionScope, str(scope)),
            target_ref=str(target_ref),
            requested_at=cast(datetime, requested_at).astimezone(UTC).isoformat(),
        )
        for user_id, epoch, request_id, scope, target_ref, requested_at in rows
    ]


def merge_journal(existing: Iterable[JournalEntry], exported: Iterable[JournalEntry]) -> list[JournalEntry]:
    """The union of both, by request id. Never drops an entry; two different entries under one id are refused."""
    merged: dict[str, JournalEntry] = {}
    for entry in (*existing, *exported):
        previous = merged.setdefault(entry.request_id, entry)
        if previous != entry:
            raise JournalError("journal_entry_conflict")
    epochs: dict[tuple[str, int], str] = {}
    for entry in merged.values():
        if epochs.setdefault((entry.user_id, entry.deletion_epoch), entry.request_id) != entry.request_id:
            raise JournalError("journal_epoch_conflict")
    return sorted(merged.values())


def read_journal(path: Path) -> Journal:
    """Read a journal file; a missing file is an empty journal, a malformed one is refused."""
    if not path.exists():
        return Journal((), None)
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise JournalError("journal_header_missing")
    header = json.loads(lines[0])
    if header.get("format") != JOURNAL_FORMAT or header.get("version") != JOURNAL_VERSION:
        raise JournalError("journal_format_unsupported")
    entries: list[JournalEntry] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = json.loads(line)
        if fields.get("scope") not in ("memory", "topic", "all"):
            raise JournalError("journal_entry_invalid")
        entries.append(JournalEntry(**fields))
    check = header.get("key_check")
    return Journal(tuple(merge_journal([], entries)), check if isinstance(check, str) else None)


def write_journal(path: Path, entries: Sequence[JournalEntry], *, schema_name: str, key: bytes) -> None:
    """Write ``entries`` atomically (a temporary file in the same directory, then a rename), mode 0600."""
    header = {
        "format": JOURNAL_FORMAT,
        "version": JOURNAL_VERSION,
        "schema": schema_name,
        "exported_at": datetime.now(UTC).isoformat(),
        "entries": len(entries),
        "key_check": key_check(key),
    }
    body = "\n".join([json.dumps(header), *(json.dumps(asdict(entry), sort_keys=True) for entry in entries)]) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


async def export_to_file(
    connection: AsyncConnection, path: Path, *, key: bytes, schema_name: str = "cme_engine"
) -> int:
    """Export, merge into ``path`` and write it back. Returns the journal's entry count.

    ``key`` is the deployment's deletion key; only its key check is written. An existing journal made under another
    key is refused rather than mixed.
    """
    existing = read_journal(path)
    if existing.key_check is not None and not hmac.compare_digest(existing.key_check, key_check(key)):
        raise JournalError("journal_key_mismatch")
    merged = merge_journal(existing.entries, await export_journal(connection, schema_name=schema_name))
    write_journal(path, merged, schema_name=schema_name, key=key)
    logger.info("erasure_journal_exported", entries=len(merged))
    return len(merged)


# ------------------------------------------------------------------------------------------------------ replay


async def _restored_requests(handlers: DeleteHandlers, user_id: str) -> dict[int, tuple[str, str, str]] | None:
    """``epoch -> (request id, scope, digest)`` in the restored database; ``None`` if the user is absent."""
    runtime = handlers.runtime
    async with runtime.session(user_id) as connection, connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT 1 FROM {u} WHERE id = %s").format(u=runtime.table("app_user")), (user_id,)
        )
        if await cursor.fetchone() is None:
            return None
        await cursor.execute(
            sql.SQL("SELECT deletion_epoch, id, scope, target_ref FROM {r} WHERE user_id = %s").format(
                r=runtime.table("deletion_request")
            ),
            (user_id,),
        )
        return {int(epoch): (str(rid), str(scope), str(ref)) for epoch, rid, scope, ref in await cursor.fetchall()}


async def _resolve_target(handlers: DeleteHandlers, entry: JournalEntry) -> str:
    """The plaintext target whose digest is the entry's, among the user's restored keys; ``""`` when none is."""
    if entry.scope == "all":
        return ""
    runtime = handlers.runtime
    # explicit `user_id = %s`: the read policy also admits rows other users shared, and those are not this user's
    statement = sql.SQL("SELECT DISTINCT memory_key FROM {v} WHERE user_id = %(u)s AND memory_key IS NOT NULL")
    if entry.scope == "topic":
        statement = sql.SQL(
            "SELECT DISTINCT memory_key FROM {v} WHERE user_id = %(u)s AND memory_key IS NOT NULL "
            "UNION SELECT DISTINCT subject_key FROM {v} WHERE user_id = %(u)s AND subject_key IS NOT NULL "
            "UNION SELECT id FROM {e} WHERE user_id = %(u)s"
        )
    async with runtime.session(entry.user_id) as connection, connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            statement.format(v=runtime.table("memory_version"), e=runtime.table("entity")), {"u": entry.user_id}
        )
        candidates = [str(row[0]) for row in await cursor.fetchall()]
    for candidate in candidates:
        digest = target_ref_digest(runtime.deletion_key, entry.user_id, entry.scope, candidate)
        if hmac.compare_digest(digest, entry.target_ref):
            return candidate
    return ""


async def replay_entry(handlers: DeleteHandlers, entry: JournalEntry) -> ReplayOutcome:
    """Re-apply one journalled request unless the restored database already holds it."""
    restored = await _restored_requests(handlers, entry.user_id)
    if restored is None:
        return "user_absent"
    present = restored.get(entry.deletion_epoch)
    if present is not None:
        if present != (entry.request_id, entry.scope, entry.target_ref):
            # the restored database recorded a different deletion under this epoch: it is not this journal's history
            raise JournalError("journal_diverges_from_database")
        return "already_present"
    target = await _resolve_target(handlers, entry)
    await handlers.replay(
        entry.user_id,
        scope=entry.scope,
        target_ref=target,
        request_id=entry.request_id,
        stored_target_ref=entry.target_ref,
        deletion_epoch=entry.deletion_epoch,
    )
    return "replayed" if target or entry.scope == "all" else "recorded_unresolved"


async def replay_journal(handlers: DeleteHandlers, journal: Journal) -> ReplayReport:
    """Re-apply every journalled request the restored database lacks, per user in epoch order. Idempotent.

    Refused before anything is written when the journal was made under a different deletion key.
    """
    if journal.entries and (
        journal.key_check is None
        or not hmac.compare_digest(journal.key_check, key_check(handlers.runtime.deletion_key))
    ):
        raise JournalError("journal_key_mismatch")
    counts: dict[ReplayOutcome, int] = {
        "already_present": 0,
        "replayed": 0,
        "recorded_unresolved": 0,
        "user_absent": 0,
    }
    for entry in merge_journal([], journal.entries):
        counts[await replay_entry(handlers, entry)] += 1
    report = ReplayReport(**counts)
    logger.info("erasure_journal_replayed", **asdict(report))
    return report


__all__ = [
    "JOURNAL_FORMAT",
    "JOURNAL_VERSION",
    "Journal",
    "JournalEntry",
    "JournalError",
    "ReplayReport",
    "export_journal",
    "export_to_file",
    "key_check",
    "merge_journal",
    "read_journal",
    "replay_entry",
    "replay_journal",
    "write_journal",
]
