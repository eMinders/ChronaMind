"""``deletion/journal.py``: the erasure journal file, without a database (``V2-D122``).

The database half (export from the live database, replay into a restored older one, the leak scan) is
``tests/integration/test_backup_restore_db.py``, over a real ``pg_dump``/``pg_restore``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import stat
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from custom_memory_engine.deletion.journal import (
    JOURNAL_FORMAT,
    Journal,
    JournalEntry,
    JournalError,
    ReplayReport,
    key_check,
    merge_journal,
    read_journal,
    replay_journal,
    write_journal,
)
from custom_memory_engine.deletion.markers import ErasureMarker

KEY = b"k" * 32


def entry(user: str = "alice", epoch: int = 1, request: str = "del-1", target: str = "hmac-sha256:aa") -> JournalEntry:
    return JournalEntry(user, epoch, request, "memory", target, "2026-11-26T00:00:00+00:00")


def test_a_merge_keeps_every_entry_and_never_shrinks_the_journal() -> None:
    journal = [entry(request="del-1"), entry(epoch=2, request="del-2")]
    # an export from a restored, older database holds only the first: the second must survive the merge
    assert merge_journal(journal, [entry(request="del-1")]) == journal
    assert merge_journal([], journal[::-1]) == journal  # ordered by user, then epoch


def test_the_written_journal_is_durable_in_its_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crash after the rename must not leave the older, shorter journal: the directory itself is fsynced."""
    synced: list[bool] = []  # was the fsynced descriptor a directory?
    real_fsync = os.fsync

    def record(descriptor: int) -> None:
        synced.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record)
    path = tmp_path / "journal.jsonl"
    write_journal(path, [entry()], schema_name="cme_engine", key=KEY)
    # the temporary file, then the directory the rename landed in, so the rename itself is durable
    assert synced == [False, True]
    assert read_journal(path).entries == (entry(),)


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


@pytest.mark.parametrize(
    "line",
    ["not json", '{"user_id": "alice"}', '{"unknown": 1, "scope": "all"}', "[1, 2]"],
    ids=["invalid-json", "missing-fields", "unknown-field", "not-an-object"],
)
def test_a_malformed_entry_is_refused_with_a_static_code(tmp_path: Path, line: str) -> None:
    path = tmp_path / "journal.jsonl"
    write_journal(path, [entry()], schema_name="cme_engine", key=KEY)
    path.write_text(path.read_text() + line + "\n")
    with pytest.raises(JournalError) as refused:
        read_journal(path)
    assert refused.value.code == "journal_entry_invalid"


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


def test_a_truncated_journal_is_refused_rather_than_read_as_a_shorter_one(tmp_path: Path) -> None:
    """An off-host copy cut at a line boundary must not load as a valid journal with fewer deletions (`M2`)."""
    path = tmp_path / "journal.jsonl"
    write_journal(path, [entry(request="del-1"), entry(epoch=2, request="del-2")], schema_name="cme_engine", key=KEY)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(JournalError) as refused:
        read_journal(path)
    assert refused.value.code == "journal_truncated"


def test_a_journal_with_entries_but_no_key_check_is_never_stamped_with_the_current_key(tmp_path: Path) -> None:
    """Stamping a key on entries the file cannot vouch for would make a mixed journal look authentic (`L2`)."""
    path = tmp_path / "journal.jsonl"
    header = {"format": JOURNAL_FORMAT, "version": 1, "schema": "cme_engine", "entries": 1}
    body = json.dumps(asdict(entry()), sort_keys=True)
    path.write_text(json.dumps(header) + "\n" + body + "\n", encoding="utf-8")
    assert read_journal(path).key_check is None
    # the refusal itself is in `export_to_file` (it needs the deployment key); asserted against a real database in
    # `tests/integration/test_backup_restore_db.py`


def test_a_missing_journal_is_not_an_empty_success(tmp_path: Path) -> None:
    """A rebuilt host without the off-host copy must fail, not report zero entries and exit 0 (`M1`).

    Run as a subprocess: a test may not import `scripts/` (`test_boundaries.py`), and the script must be exercised as
    the operator runs it anyway.
    """
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "CME_DELETION_RECEIPT_KEY": "k" * 32,
        "CME_DATABASE_URL": "postgresql://unused/unused",
        "CME_RUNTIME_ROLE": "unused",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "erasure_journal.py"),
            "replay",
            "--journal",
            str(tmp_path / "absent.jsonl"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=environment,
    )
    assert result.returncode == 1
    assert "journal_missing" in result.stderr
    assert "unused" not in result.stderr  # never the DSN or the role


def test_a_replay_is_a_failure_when_anything_is_unresolved_or_divergent() -> None:
    """The rule lives in the library (`ReplayReport`), so a test can pin it without a connection or the CLI."""
    clean = ReplayReport(already_present=3, replayed=0, recorded_unresolved=0, users_reprovisioned=0)
    assert clean.refusal_reason(accept_unresolved=False) is None
    unresolved = ReplayReport(already_present=0, replayed=3, recorded_unresolved=1, users_reprovisioned=0)
    assert unresolved.refusal_reason(accept_unresolved=False) == "unresolved_entries_remain"
    assert unresolved.refusal_reason(accept_unresolved=True) is None
    divergent = ReplayReport(
        already_present=0, replayed=3, recorded_unresolved=0, users_reprovisioned=0, divergent_users=("alice",)
    )
    assert divergent.refusal_reason(accept_unresolved=True) == "divergent_users_remain"


def marked(*markers: tuple[str, str, str]) -> JournalEntry:
    return JournalEntry(
        "alice",
        1,
        "del-1",
        "memory",
        "hmac-sha256:aa",
        "2026-11-26T00:00:00+00:00",
        markers=tuple(ErasureMarker(kind, digest, result) for kind, digest, result in markers),  # type: ignore[arg-type]
    )


def test_a_request_carries_what_it_erased_besides_its_target_through_the_file(tmp_path: Path) -> None:
    """T-46 (V2-D125): a marker survives the round trip, in a canonical order, and names nothing in plaintext."""
    path = tmp_path / "journal.jsonl"
    written = marked(("turn", "b" * 64, ""), ("write", "a" * 64, "v_1"))
    write_journal(path, [written], schema_name="cme_engine", key=KEY)
    loaded = read_journal(path)
    # the file lists them as the export wrote them; reading them yields the canonical order, sorted by kind
    assert loaded.entries == (marked(("turn", "b" * 64, ""), ("write", "a" * 64, "v_1")),)
    body = path.read_text().splitlines()[1]
    assert json.loads(body)["markers"] == [["turn", "b" * 64, ""], ["write", "a" * 64, "v_1"]]
    # a digest carries nothing: only the key can tell what a marker names
    assert "b" * 64 in body and KEY.hex() not in body


def test_a_version_one_journal_is_still_read_and_carries_no_markers(tmp_path: Path) -> None:
    """A journal exported before V2-D125 is read, and its entries have nothing to erase besides the target."""
    path = tmp_path / "journal.jsonl"
    write_journal(path, [entry()], schema_name="cme_engine", key=KEY)
    header, body = path.read_text().splitlines()
    header_fields = json.loads(header) | {"version": 1}
    fields = {key: value for key, value in json.loads(body).items() if key != "markers"}
    path.write_text("\n".join([json.dumps(header_fields), json.dumps(fields)]) + "\n")
    loaded = read_journal(path)
    assert loaded.entries == (entry(),)
    assert loaded.entries[0].markers == ()


@pytest.mark.parametrize(
    "markers",
    [
        [["turn", "z" * 64, ""]],  # not a digest
        [["erase", "a" * 64, ""]],  # not a kind
        [["write", "a" * 64, ""]],  # a write marker without its result
        [["turn", "a" * 64, "v_1"]],  # a turn marker with one
        [["turn", "a" * 64, ""], ["turn", "a" * 64, ""]],  # an export never writes the same marker twice
        "not-a-list",
    ],
    ids=["bad-digest", "bad-kind", "write-without-result", "turn-with-result", "duplicate", "not-a-list"],
)
def test_a_journal_entry_with_a_malformed_marker_is_refused(tmp_path: Path, markers: object) -> None:
    path = tmp_path / "journal.jsonl"
    write_journal(path, [entry()], schema_name="cme_engine", key=KEY)
    header, body = path.read_text().splitlines()
    path.write_text("\n".join([header, json.dumps(json.loads(body) | {"markers": markers})]) + "\n")
    with pytest.raises(JournalError) as refused:
        read_journal(path)
    assert refused.value.code == "journal_entry_invalid"


def test_two_entries_under_one_request_differ_in_what_they_erased_are_refused() -> None:
    """The markers are part of a request's identity: they cannot silently change under one id."""
    with pytest.raises(JournalError) as conflict:
        merge_journal([marked(("turn", "a" * 64, ""))], [marked(("turn", "b" * 64, ""))])
    assert conflict.value.code == "journal_entry_conflict"
    # and an entry with no markers merges into one that has them only if it is the same request
    assert merge_journal([marked(("turn", "a" * 64, ""))], [marked(("turn", "a" * 64, ""))]) == [
        marked(("turn", "a" * 64, ""))
    ]
