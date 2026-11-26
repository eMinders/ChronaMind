"""Export the erasure journal, or replay it after a restore (``V2-D122``, ``deploy/README.md``).

    # after every backup, on a timer, and before any restore (needs a BYPASSRLS role, read-only on deletion_request)
    CME_JOURNAL_DATABASE_URL=... CME_DELETION_RECEIPT_KEY=... \
        python scripts/erasure_journal.py export --journal /var/lib/chronamind-journal/erasure-journal.jsonl

    # after pg_restore and role re-provisioning, before the host serves traffic (the restricted runtime role)
    CME_DATABASE_URL=... CME_RUNTIME_ROLE=... CME_DELETION_RECEIPT_KEY=... \
        python scripts/erasure_journal.py replay --journal /var/lib/chronamind-journal/erasure-journal.jsonl

``CME_SCHEMA`` selects the engine schema (default ``cme_engine``). Exit codes: ``0`` success, ``1`` runtime error, a
refused journal, an unresolved entry, an incomplete key or a divergent user, ``2`` missing configuration. Output is
content-free counts; ``--json`` prints them as one JSON object.

**A replay is only a success when nothing is unresolved.** An entry whose target the restored database does not hold
is recorded with nothing erased, and that is exactly what a *wrong deletion key* also looks like: no candidate digests
to the journalled value. The script therefore exits ``1`` with ``unresolved_entries_remain`` unless the operator passes
``--accept-unresolved``, which says the entry really was a memory the backup never held. A missing journal file is not
"nothing to do" either: it means the off-host copy was not restored, so replay refuses (``journal_missing``) rather
than reporting an empty success.

**Only a successful replay reconciles the database** (``V2-D125``). Until a replay exits ``0`` here, the worker claims
nothing (``unreconciled``) and the host refuses to start and reports not-ready (``restore_not_reconciled``): a restored
copy still holds what later deletions erased.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

from psycopg import AsyncConnection, sql
from rich.console import Console

from custom_memory_engine.api.delete_handlers import DeleteHandlers
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.deletion.journal import JournalError, export_to_file, read_journal, replay_journal
from custom_memory_engine.deletion.markers import record_reconciliation
from custom_memory_engine.engine.deletion import RECEIPT_KEY_ENV
from custom_memory_engine.scope.provenance import ProvenanceKey

#: The shortest deployment key accepted, as the host requires (``host/service.py``).
MIN_KEY_BYTES = 32


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export or replay the ChronaMind erasure journal.")
    parser.add_argument("command", choices=("export", "replay"))
    parser.add_argument("--journal", required=True, type=Path, help="The journal file (JSON lines).")
    parser.add_argument("--json", action="store_true", help="Print the result as one JSON object.")
    parser.add_argument(
        "--accept-unresolved",
        action="store_true",
        help="Succeed even when journalled targets were absent from the restored database (the backup never held "
        "them). Without it any unresolved entry exits 1, because a wrong deletion key looks identical.",
    )
    return parser.parse_args(argv)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise KeyError(name)
    return value


def _deletion_key() -> bytes:
    key = _required(RECEIPT_KEY_ENV).encode("utf-8")
    if len(key) < MIN_KEY_BYTES:
        raise JournalError("deletion_receipt_key_too_short")
    return key


async def _export(journal: Path, schema: str) -> dict[str, object]:
    key = _deletion_key()
    async with await AsyncConnection.connect(
        _required("CME_JOURNAL_DATABASE_URL"), autocommit=True, connect_timeout=5
    ) as connection:
        entries = await export_to_file(connection, journal, key=key, schema_name=schema)
    return {"command": "export", "entries": entries}


async def _replay(journal: Path, schema: str, *, accept_unresolved: bool) -> dict[str, object]:
    key = _deletion_key()
    if not await asyncio.to_thread(journal.exists):
        # a rebuilt host that never received the off-host copy would otherwise "replay" zero entries and exit 0
        raise JournalError("journal_missing")
    loaded = read_journal(journal)
    provenance = os.environ.get("CME_PROVENANCE_KEY", "").strip().encode("utf-8")
    # a deletion writes no provenance signature; a random key only satisfies the runtime's shape
    provenance_key = ProvenanceKey(provenance if len(provenance) >= MIN_KEY_BYTES else os.urandom(32))
    role = _required("CME_RUNTIME_ROLE")
    async with await AsyncConnection.connect(
        _required("CME_DATABASE_URL"), autocommit=True, connect_timeout=5
    ) as connection:
        runtime = Runtime(connection, role, schema, provenance_key=provenance_key, deletion_key=key)
        report = await replay_journal(DeleteHandlers(runtime), loaded)
        refusal = report.refusal_reason(accept_unresolved=accept_unresolved)
        if refusal is None:
            # only a successful replay lets the worker and the host use this database (`V2-D125`)
            async with connection.transaction():
                await connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                await record_reconciliation(connection, schema_name=schema)
    return {
        "command": "replay",
        "entries": len(loaded.entries),
        **asdict(report),
        "reconciled": refusal is None,
        "refusal": refusal,
    }


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    schema = os.environ.get("CME_SCHEMA", "").strip() or "cme_engine"
    console = Console(stderr=True)
    try:
        result = await (
            _export(args.journal, schema)
            if args.command == "export"
            else _replay(args.journal, schema, accept_unresolved=args.accept_unresolved)
        )
    except KeyError as missing:
        console.print(f"[red]{str(missing.args[0]).lower()}_required[/red]")
        return 2
    except JournalError as error:
        console.print(f"[red]{error.code}[/red]")
        return 1
    except Exception as error:  # noqa: BLE001 - exit non-zero with a static name, never a DSN or a row
        console.print(f"[red]erasure_journal_failed: {type(error).__name__}[/red]")
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        console.print(result)
    if args.command != "replay":
        return 0
    refusal = result.get("refusal")
    if refusal is not None:
        console.print(f"[red]{refusal}[/red]")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
