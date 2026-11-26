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
  plaintext target. A target the restored database never held is recorded with nothing to erase; a user the backup
  predates is re-provisioned first, as the host would. The replayed request keeps its original id, digest and epoch,
  so a second replay does nothing, and the next export merges it into the original entry.

**What a deletion erased besides its target** (``T-46``, ``V2-D125``). A target is not the whole effect of a
deletion: the turns that evidenced the deleted claim, their extraction jobs, and the idempotency identities of the
erased writes come back with an older backup too, and each of them re-creates the claim on its own (the worker
re-extracts the turn; a client's resend of the same key writes the claim again). Format 2 therefore carries each
request's erasure markers (:mod:`custom_memory_engine.deletion.markers`), and the replay records them and erases every
turn they name -- including for a target the restored database never held, which is exactly the case where nothing
else would. A format-1 journal is still read; its entries carry no markers.

After a replay that leaves nothing unresolved, :func:`custom_memory_engine.deletion.markers.record_reconciliation`
records this database's identity; until then the worker refuses to claim work (``restore_not_reconciled``).

What it does not cover is stated in ``deploy/README.md``: deletions after the newest journal export (its RPO), and a
journal lost together with the database.
"""

from __future__ import annotations

import dataclasses
import fcntl
import hmac
import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import structlog
from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.api.delete_handlers import DeleteHandlers
from custom_memory_engine.deletion.markers import ErasureMarker, MarkerError, markers_of
from custom_memory_engine.engine.deletion import DeletionScope, target_ref_digest

logger = structlog.get_logger(__name__)

#: The first line of every journal file, so a wrong file is refused before anything is replayed.
JOURNAL_FORMAT = "cme-erasure-journal"
JOURNAL_VERSION = 2
#: Versions this code reads. Version 1 predates erasure markers (``V2-D125``): its entries simply carry none.
READABLE_VERSIONS = frozenset({1, 2})
_KEY_CHECK_MESSAGE = b"cme-erasure-journal:key-check:v1"

type ReplayOutcome = Literal["already_present", "replayed", "recorded_unresolved"]


class JournalError(ValueError):
    """A static, content-free refusal code."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, order=True)
class JournalEntry:
    """One completed deletion request, exactly as ``deletion_request`` records it. Content-free."""

    user_id: str
    deletion_epoch: int
    request_id: str
    scope: DeletionScope
    target_ref: str
    requested_at: str
    #: What the request erased besides its target (``V2-D125``), in a canonical order. Part of its identity.
    markers: tuple[ErasureMarker, ...] = ()


@dataclass(frozen=True)
class Journal:
    """A journal file: entries, its export recovery point and its deletion-key check."""

    entries: tuple[JournalEntry, ...]
    key_check: str | None
    exported_at: str | None = None


def key_check(key: bytes) -> str:
    """A one-way fingerprint of the deletion key: equal keys, equal checks; it reveals nothing about the key."""
    return hmac.new(key, _KEY_CHECK_MESSAGE, "sha256").hexdigest()


def _timestamp(value: str) -> datetime:
    """Parse an aware ISO-8601 timestamp used by the recovery-point check."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def check_recovery_point(journal: Journal, restored_from_backup_at: str) -> None:
    """Refuse a journal whose header or newest per-user entry predates the restored backup."""
    try:
        backup = _timestamp(restored_from_backup_at)
        header = _timestamp(journal.exported_at) if journal.exported_at is not None else None
        newest_by_user: dict[str, str] = {}
        for entry in journal.entries:
            previous = newest_by_user.get(entry.user_id)
            if previous is None or _timestamp(entry.requested_at) > _timestamp(previous):
                newest_by_user[entry.user_id] = entry.requested_at
        points = [journal.exported_at, *newest_by_user.values()]
        for point in points:
            if point is None or _timestamp(point) < backup:
                displayed = "missing" if point is None else point
                raise JournalError(
                    "journal_older_than_backup",
                    detail=f"journal_timestamp={displayed} restored_from_backup_at={restored_from_backup_at}",
                )
        if header is None:
            raise JournalError(
                "journal_older_than_backup",
                detail=f"journal_timestamp=missing restored_from_backup_at={restored_from_backup_at}",
            )
    except JournalError:
        raise
    except (TypeError, ValueError):
        raise JournalError("journal_timestamp_invalid") from None


@dataclass(frozen=True)
class ReplayReport:
    """Counts and recovery evidence from one replay."""

    already_present: int
    replayed: int
    recorded_unresolved: int
    #: Journalled users the restored database did not hold (created after the backup), re-provisioned so their
    #: requests and epochs are recorded and their next deletion continues after them.
    users_reprovisioned: int
    #: The newest journal epoch per user, compared with the restored database before replay writes it.
    journal_newest_epochs: dict[str, int] = dataclasses.field(default_factory=dict)
    #: The newest completed deletion epoch per user in the restored database before replay.
    restored_newest_epochs: dict[str, int | None] = dataclasses.field(default_factory=dict)
    #: Users whose restored history contradicts the journal. Their entries were skipped and every other user was still
    #: replayed: a conflict in one tenant must not leave the rest of the deployment unerased. The run is not a success.
    divergent_users: tuple[str, ...] = ()

    def refusal_reason(self, *, accept_unresolved: bool) -> str | None:
        """The static reason this replay is not a success, or ``None``.

        Two results look successful while having erased nothing: entries whose target the restored database does not
        hold (which is also exactly what a wrong deletion key produces, since no candidate digest matches), and users
        whose restored history contradicts the journal. The caller decides whether the first is expected; the second
        never is. This lives here, not in the script, so the rule is testable without a connection.
        """
        if self.recorded_unresolved > 0 and not accept_unresolved:
            return "unresolved_entries_remain"
        if self.divergent_users:
            return "divergent_users_remain"
        return None


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
    markers = await markers_of(connection, request_ids=[str(row[2]) for row in rows], schema_name=schema_name)
    return [
        JournalEntry(
            user_id=str(user_id),
            deletion_epoch=int(epoch),
            request_id=str(request_id),
            scope=cast(DeletionScope, str(scope)),
            target_ref=str(target_ref),
            requested_at=cast(datetime, requested_at).astimezone(UTC).isoformat(),
            markers=markers.get(str(request_id), ()),
        )
        for user_id, epoch, request_id, scope, target_ref, requested_at in rows
    ]


def _identity(entry: JournalEntry) -> tuple[str, int, str, str, str, tuple[ErasureMarker, ...]]:
    """What makes two entries one request. Not ``requested_at``: a replay re-records the request at replay time."""
    return entry.user_id, entry.deletion_epoch, entry.request_id, entry.scope, entry.target_ref, entry.markers


def merge_journal(existing: Iterable[JournalEntry], exported: Iterable[JournalEntry]) -> list[JournalEntry]:
    """The union of both, by request id, keeping the first-seen (original) entry.

    Never drops an entry; two entries under one id that differ in identity (user, epoch, scope or target) are refused.
    A replayed request differs only in ``requested_at`` (the replay's own time) and merges into the original.
    """
    merged: dict[str, JournalEntry] = {}
    for entry in (*existing, *exported):
        previous = merged.setdefault(entry.request_id, entry)
        if _identity(previous) != _identity(entry):
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
    try:
        header = json.loads(lines[0])
        if header.get("format") != JOURNAL_FORMAT or header.get("version") not in READABLE_VERSIONS:
            raise JournalError("journal_format_unsupported")
        declared = header.get("entries")
        entries: list[JournalEntry] = []
        for line in lines[1:]:
            if not line.strip():
                continue
            fields = json.loads(line)
            if fields.get("scope") not in ("memory", "topic", "all"):
                raise JournalError("journal_entry_invalid")
            raw_markers = fields.pop("markers", [])
            if not isinstance(raw_markers, list):
                raise JournalError("journal_entry_invalid")
            markers = tuple(sorted({ErasureMarker(*marker) for marker in raw_markers}))
            if len(markers) != len(raw_markers):
                raise JournalError("journal_entry_invalid")  # a duplicated marker is not something an export writes
            entries.append(JournalEntry(**fields, markers=markers))
        # an off-host copy cut at a line boundary would otherwise load as a valid, shorter journal and the missing
        # deletions would never be replayed (`M2`, review of #259)
        if isinstance(declared, int) and declared != len(entries):
            raise JournalError("journal_truncated")
    except JournalError:
        raise
    except (ValueError, TypeError, AttributeError, MarkerError):  # malformed JSON, a bad field or marker, a non-object
        raise JournalError("journal_entry_invalid") from None
    check = header.get("key_check")
    exported_at = header.get("exported_at")
    if exported_at is not None and not isinstance(exported_at, str):
        raise JournalError("journal_header_invalid")
    return Journal(
        tuple(merge_journal([], entries)),
        check if isinstance(check, str) else None,
        exported_at,
    )


def _record(entry: JournalEntry) -> dict[str, object]:
    """One entry as a JSON object; a marker is a ``[kind, digest, result_id]`` triple."""
    record = asdict(entry)
    record["markers"] = [[marker.kind, marker.digest, marker.result_id] for marker in entry.markers]
    return record


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
    body = "\n".join([json.dumps(header), *(json.dumps(_record(entry), sort_keys=True) for entry in entries)]) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        # the rename must reach the disk: without it a crash can leave the older, shorter journal behind, losing
        # exactly the newest deletions the journal exists to hold
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@contextmanager
def _exclusive(path: Path) -> Iterator[None]:
    """An exclusive lock on a sidecar file, so two exports never interleave their read-merge-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path.with_name(f".{path.name}.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


async def export_to_file(
    connection: AsyncConnection, path: Path, *, key: bytes, schema_name: str = "cme_engine"
) -> int:
    """Export, merge into ``path`` and write it back. Returns the journal's entry count.

    ``key`` is the deployment's deletion key; only its key check is written. An existing journal made under another
    key is refused rather than mixed.
    """
    exported = await export_journal(connection, schema_name=schema_name)
    with _exclusive(path):
        existing = read_journal(path)
        if existing.key_check is None and existing.entries:
            # entries this file cannot vouch for: stamping the current key on them would make a mixed or hand-made
            # journal look authentic (`L2`, review of #259)
            raise JournalError("journal_key_check_missing")
        if existing.key_check is not None and not hmac.compare_digest(existing.key_check, key_check(key)):
            raise JournalError("journal_key_mismatch")
        merged = merge_journal(existing.entries, exported)
        write_journal(path, merged, schema_name=schema_name, key=key)
    logger.info("erasure_journal_exported", entries=len(merged))
    return len(merged)


# ------------------------------------------------------------------------------------------------------ replay


async def _restored_requests(
    handlers: DeleteHandlers, user_id: str
) -> tuple[dict[int, tuple[str, str, str]], dict[str, int], bool]:
    """``epoch -> (request id, scope, digest)``, ``request id -> epoch``, and whether the user was re-provisioned.

    Both maps come from one read, and both carry ``status``: a journalled request is only ``already_present`` when the
    restored database holds it *completed*. A non-completed row under that epoch, or the same request id under another
    epoch, is a different history and is refused (``journal_diverges_from_database``) rather than treated as done.


    A journalled user the backup predates is created exactly as the host creates one (``HostService._provision``):
    otherwise their journalled epochs stay unrecorded, the host re-creates them on their next call, their next
    deletion takes epoch 1 again, and every later export and replay is refused as a conflict.
    """
    runtime = handlers.runtime
    async with runtime.session(user_id) as connection, connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {u} (id, external_ref, created_at) VALUES (%s, %s, {clock}()) "
                "ON CONFLICT DO NOTHING RETURNING id"
            ).format(u=runtime.table("app_user"), clock=sql.Identifier(runtime.schema_name, "clock_now")),
            (user_id, user_id),
        )
        provisioned = await cursor.fetchone() is not None
        if not provisioned:
            # a conflict on another unique column (an operator-provisioned `external_ref`) skips the insert without
            # creating the id, and the request insert below would then fail its foreign key: refuse with a static code
            await cursor.execute(
                sql.SQL("SELECT 1 FROM {u} WHERE id = %s").format(u=runtime.table("app_user")), (user_id,)
            )
            if await cursor.fetchone() is None:
                raise JournalError("journal_user_provision_failed")
        await cursor.execute(
            sql.SQL("SELECT deletion_epoch, id, scope, target_ref, status FROM {r} WHERE user_id = %s").format(
                r=runtime.table("deletion_request")
            ),
            (user_id,),
        )
        rows = await cursor.fetchall()
    by_epoch: dict[int, tuple[str, str, str]] = {}
    by_request: dict[str, int] = {}
    for epoch, rid, scope, ref, status in rows:
        by_request[str(rid)] = int(epoch)
        # a request that is not `completed` erased nothing, so it cannot stand in for the journalled one
        by_epoch[int(epoch)] = (str(rid), str(scope), str(ref)) if status == "completed" else ("", "", "")
    return by_epoch, by_request, provisioned


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


async def replay_entry(handlers: DeleteHandlers, entry: JournalEntry) -> tuple[ReplayOutcome, bool, int | None]:
    """Re-apply one request and return its pre-replay newest completed database epoch."""
    restored, by_request, provisioned = await _restored_requests(handlers, entry.user_id)
    newest_restored_epoch = max((epoch for epoch, value in restored.items() if value[0]), default=None)
    held_at = by_request.get(entry.request_id)
    if held_at is not None and held_at != entry.deletion_epoch:
        # the same request id under another epoch: two different histories, not a replay of this one
        raise JournalError("journal_diverges_from_database")
    present = restored.get(entry.deletion_epoch)
    if present is not None:
        if present != (entry.request_id, entry.scope, entry.target_ref):
            # the restored database recorded a different (or an incomplete) deletion under this epoch
            raise JournalError("journal_diverges_from_database")
        return "already_present", provisioned, newest_restored_epoch
    target = await _resolve_target(handlers, entry)
    await handlers.replay(
        entry.user_id,
        scope=entry.scope,
        target_ref=target,
        request_id=entry.request_id,
        stored_target_ref=entry.target_ref,
        deletion_epoch=entry.deletion_epoch,
        erasure_markers=entry.markers,
    )
    outcome = "replayed" if target or entry.scope == "all" else "recorded_unresolved"
    return outcome, provisioned, newest_restored_epoch


async def replay_journal(handlers: DeleteHandlers, journal: Journal) -> ReplayReport:
    """Re-apply every journalled request the restored database lacks, per user in epoch order. Idempotent.

    Refused before anything is written when the journal was made under a different deletion key.
    """
    if journal.entries and (
        journal.key_check is None
        or not hmac.compare_digest(journal.key_check, key_check(handlers.runtime.deletion_key))
    ):
        raise JournalError("journal_key_mismatch")
    counts: dict[ReplayOutcome, int] = {"already_present": 0, "replayed": 0, "recorded_unresolved": 0}
    reprovisioned = 0
    divergent: set[str] = set()
    journal_epochs: dict[str, int] = {}
    restored_epochs: dict[str, int | None] = {}
    for entry in merge_journal([], journal.entries):
        journal_epochs[entry.user_id] = max(journal_epochs.get(entry.user_id, 0), entry.deletion_epoch)
        try:
            outcome, provisioned, restored_epoch = await replay_entry(handlers, entry)
            restored_epochs.setdefault(entry.user_id, restored_epoch)
        except JournalError as error:
            if error.code != "journal_diverges_from_database":
                raise
            # fail closed for this user, and only this user: the operator gets the name and the count, and the entries
            # of every other tenant are still applied
            divergent.add(entry.user_id)
            continue
        counts[outcome] += 1
        reprovisioned += int(provisioned)
    report = ReplayReport(
        **counts,
        users_reprovisioned=reprovisioned,
        journal_newest_epochs=journal_epochs,
        restored_newest_epochs=restored_epochs,
        divergent_users=tuple(sorted(divergent)),
    )
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
    "check_recovery_point",
    "key_check",
    "merge_journal",
    "read_journal",
    "replay_entry",
    "replay_journal",
    "write_journal",
]
