"""Export the erasure journal, or replay it after a restore (``V2-D122``, ``deploy/README.md``).

    # after every backup, on a timer, and before any restore (needs a BYPASSRLS role, read-only on deletion_request)
    CME_JOURNAL_DATABASE_URL=... CME_DELETION_RECEIPT_KEY=... \
        python scripts/erasure_journal.py export --journal /var/lib/chronamind-journal/erasure-journal.jsonl

    # after pg_restore and role re-provisioning, before the host serves traffic (the restricted runtime role)
    CME_DATABASE_URL=... CME_RUNTIME_ROLE=... CME_DELETION_RECEIPT_KEY=... \
        python scripts/erasure_journal.py replay --journal /var/lib/chronamind-journal/erasure-journal.jsonl

``CME_SCHEMA`` selects the engine schema (default ``cme_engine``). Exit codes: ``0`` success, ``1`` runtime error or a
refused journal, ``2`` missing configuration. Output is content-free counts; ``--json`` prints them as one JSON object.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

from psycopg import AsyncConnection
from rich.console import Console

from custom_memory_engine.api.delete_handlers import DeleteHandlers
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.deletion.journal import JournalError, export_to_file, read_journal, replay_journal
from custom_memory_engine.engine.deletion import RECEIPT_KEY_ENV
from custom_memory_engine.scope.provenance import ProvenanceKey

#: The shortest deployment key accepted, as the host requires (``host/service.py``).
MIN_KEY_BYTES = 32


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export or replay the ChronaMind erasure journal.")
    parser.add_argument("command", choices=("export", "replay"))
    parser.add_argument("--journal", required=True, type=Path, help="The journal file (JSON lines).")
    parser.add_argument("--json", action="store_true", help="Print the result as one JSON object.")
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


async def _replay(journal: Path, schema: str) -> dict[str, object]:
    key = _deletion_key()
    loaded = read_journal(journal)
    provenance = os.environ.get("CME_PROVENANCE_KEY", "").strip().encode("utf-8")
    # a deletion writes no provenance signature; a random key only satisfies the runtime's shape
    provenance_key = ProvenanceKey(provenance if len(provenance) >= MIN_KEY_BYTES else os.urandom(32))
    async with await AsyncConnection.connect(_required("CME_DATABASE_URL"), connect_timeout=5) as connection:
        runtime = Runtime(
            connection,
            _required("CME_RUNTIME_ROLE"),
            schema,
            provenance_key=provenance_key,
            deletion_key=key,
        )
        report = await replay_journal(DeleteHandlers(runtime), loaded)
    return {"command": "replay", "entries": len(loaded.entries), **asdict(report)}


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    schema = os.environ.get("CME_SCHEMA", "").strip() or "cme_engine"
    console = Console(stderr=True)
    try:
        result = await (_export(args.journal, schema) if args.command == "export" else _replay(args.journal, schema))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
