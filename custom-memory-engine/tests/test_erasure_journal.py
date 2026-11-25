"""``deletion/journal.py``: the erasure journal file, without a database (``V2-D122``).

The database half (export from the live database, replay into a restored older one, the leak scan) is
``tests/integration/test_backup_restore_db.py``, over a real ``pg_dump``/``pg_restore``.
"""

from __future__ import annotations

import dataclasses
import json
import stat
from pathlib import Path

import pytest

from custom_memory_engine.deletion.journal import (
    JOURNAL_FORMAT,
    Journal,
    JournalEntry,
    JournalError,
    key_check,
    merge_journal,
    read_journal,
    replay_journal,
    write_journal,
)

KEY = b"k" * 32


def entry(user: str = "alice", epoch: int = 1, request: str = "del-1", target: str = "hmac-sha256:aa") -> JournalEntry:
    return JournalEntry(user, epoch, request, "memory", target, "2026-11-26T00:00:00+00:00")


def test_a_merge_keeps_every_entry_and_never_shrinks_the_journal() -> None:
    journal = [entry(request="del-1"), entry(epoch=2, request="del-2")]
    # an export from a restored, older database holds only the first: the second must survive the merge
    assert merge_journal(journal, [entry(request="del-1")]) == journal
    assert merge_journal([], journal[::-1]) == journal  # ordered by user, then epoch


def test_a_replayed_request_merges_into_the_original_and_keeps_its_time() -> None:
    """A replay re-records the request at replay time; the timer's next export must merge it, not refuse it."""
    original = entry()
    replayed = dataclasses.replace(original, requested_at="2026-11-27T00:00:00+00:00")
    assert merge_journal([original], [replayed]) == [original]


def test_two_different_entries_under_one_request_id_or_one_epoch_are_refused() -> None:
    with pytest.raises(JournalError) as same_id:
        merge_journal([entry(target="hmac-sha256:aa")], [entry(target="hmac-sha256:bb")])
    assert same_id.value.code == "journal_entry_conflict"
    with pytest.raises(JournalError) as same_epoch:
        merge_journal([entry(request="del-1")], [entry(request="del-9")])
    assert same_epoch.value.code == "journal_epoch_conflict"


def test_the_file_round_trips_is_private_and_holds_no_key(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    entries = [entry(), entry(user="bob", request="del-b")]
    write_journal(path, entries, schema_name="cme_engine", key=KEY)

    loaded = read_journal(path)
    assert loaded == Journal(tuple(sorted(entries)), key_check(KEY))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    header = json.loads(path.read_text().splitlines()[0])
    assert header["format"] == JOURNAL_FORMAT and header["entries"] == 2
    assert KEY.decode() not in path.read_text()
    # a missing file is an empty journal (the first export)
    assert read_journal(tmp_path / "absent.jsonl") == Journal((), None)


def test_a_file_that_is_not_a_journal_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "other.jsonl"
    path.write_text('{"format": "something-else", "version": 1}\n')
    with pytest.raises(JournalError) as wrong_format:
        read_journal(path)
    assert wrong_format.value.code == "journal_format_unsupported"


def test_the_key_check_tells_keys_apart_and_is_not_the_key() -> None:
    assert key_check(KEY) == key_check(KEY)
    assert key_check(KEY) != key_check(b"w" * 32)
    assert KEY.hex() not in key_check(KEY)


class _NoDatabaseHandlers:
    """A replay must refuse a wrong key before it touches the database: any attribute but the key fails the test."""

    class runtime:  # noqa: N801 - mirrors DeleteHandlers.runtime
        deletion_key = b"w" * 32


async def test_a_replay_under_a_different_key_is_refused_before_anything_is_written() -> None:
    journal = Journal((entry(),), key_check(KEY))
    with pytest.raises(JournalError) as refused:
        await replay_journal(_NoDatabaseHandlers(), journal)  # type: ignore[arg-type]
    assert refused.value.code == "journal_key_mismatch"
    # a journal without a key check is refused too: it cannot prove its digests match this deployment
    with pytest.raises(JournalError):
        await replay_journal(_NoDatabaseHandlers(), Journal((entry(),), None))  # type: ignore[arg-type]
