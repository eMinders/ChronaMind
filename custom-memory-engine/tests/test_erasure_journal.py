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
    check_recovery_point,
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


def test_a_journal_older_than_the_restored_backup_is_refused_and_equal_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    write_journal(path, [entry()], schema_name="cme_engine", key=KEY)
    original_lines = path.read_text().splitlines()
    header = json.loads(original_lines[0])
    header["exported_at"] = "2026-11-26T00:00:00+00:00"
    path.write_text("\n".join([json.dumps(header), *original_lines[1:]]) + "\n")
    loaded = read_journal(path)

    with pytest.raises(JournalError) as refused:
        check_recovery_point(loaded, "2026-11-26T00:00:01+00:00")
    assert refused.value.code == "journal_older_than_backup"
    assert "2026-11-26T00:00:00+00:00" in str(refused.value)
    assert "2026-11-26T00:00:01+00:00" in str(refused.value)
    assert "hmac-sha256:aa" not in str(refused.value)

    check_recovery_point(loaded, "2026-11-26T00:00:00+00:00")


def test_the_recovery_point_is_compared_as_an_instant_whatever_the_offset(tmp_path: Path) -> None:
    """`+02:00` and `Z` name the same instant, one microsecond decides the refusal, and a naive value is refused.

    The test above uses UTC on both sides, so a comparison of the text would pass it too. This one fails unless the
    two values are parsed as instants, which is what lets an operator quote the backup's start in their own zone.
    """
    path = tmp_path / "journal.jsonl"
    write_journal(path, [entry()], schema_name="cme_engine", key=KEY)
    lines = path.read_text().splitlines()
    header = json.loads(lines[0]) | {"exported_at": "2026-11-26T02:00:00+02:00"}  # the instant of 00:00:00Z
    path.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")
    loaded = read_journal(path)

    check_recovery_point(loaded, "2026-11-26T00:00:00Z")
    with pytest.raises(JournalError) as refused:
        check_recovery_point(loaded, "2026-11-26T00:00:00.000001+00:00")
    assert refused.value.code == "journal_older_than_backup"

    with pytest.raises(JournalError) as invalid:
        check_recovery_point(loaded, "2026-11-26T00:00:00")  # naive: refused, never assumed to be UTC
    assert invalid.value.code == "journal_timestamp_invalid"
    assert "restored_from_backup_at" in str(invalid.value)  # the operator is told which of the two inputs is wrong


def test_a_malformed_journal_timestamp_is_named_and_never_echoed(tmp_path: Path) -> None:
    """The refusal says which timestamp is unparseable and still prints no text taken from the file."""
    with pytest.raises(JournalError) as invalid:
        check_recovery_point(Journal((), None, "yesterday"), "2026-11-26T00:00:00+00:00")
    assert invalid.value.code == "journal_timestamp_invalid"
    assert "journal_timestamp" in str(invalid.value)
    assert "yesterday" not in str(invalid.value)


def test_a_journal_header_whose_exported_at_is_not_a_string_is_refused(tmp_path: Path) -> None:
    """A number or an object where the export wrote a timestamp is refused at read time, not compared as text."""
    path = tmp_path / "journal.jsonl"
    write_journal(path, [entry()], schema_name="cme_engine", key=KEY)
    lines = path.read_text().splitlines()
    header = json.loads(lines[0]) | {"exported_at": 1764115200}
    path.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")

    with pytest.raises(JournalError) as refused:
        read_journal(path)
    assert refused.value.code == "journal_header_invalid"


def test_a_principal_who_has_not_deleted_since_before_the_backup_does_not_refuse_the_restore(
    tmp_path: Path,
) -> None:
    """A quiet principal is not evidence of a lost deletion: refusing on it would block a restore the pair covers.

    The control is the counterfactual: comparing each principal's newest ``requested_at`` against the backup start
    refuses this journal, and the only remaining path for an operator is ``--mark-reconciled``, which replays nothing.
    """
    path = tmp_path / "journal.jsonl"
    quiet = dataclasses.replace(entry(user="quiet", request="del-quiet"), requested_at="2026-01-01T00:00:00+00:00")
    write_journal(path, [entry(user="busy"), quiet], schema_name="cme_engine", key=KEY)
    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    header["exported_at"] = "2026-11-26T00:10:00+00:00"  # exported ten minutes AFTER the backup's start
    path.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")

    check_recovery_point(read_journal(path), "2026-11-26T00:00:00+00:00")


def test_a_journal_with_no_recovery_point_is_refused_rather_than_assumed_recent(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    write_journal(path, [entry()], schema_name="cme_engine", key=KEY)
    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    del header["exported_at"]
    path.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")

    with pytest.raises(JournalError) as refused:
        check_recovery_point(read_journal(path), "2026-11-26T00:00:00+00:00")
    assert refused.value.code == "journal_older_than_backup"
    assert "missing" in str(refused.value)


def test_the_file_round_trips_is_private_and_holds_no_key(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    entries = [entry(), entry(user="bob", request="del-b")]
    write_journal(path, entries, schema_name="cme_engine", key=KEY)

    loaded = read_journal(path)
    assert loaded.entries == tuple(sorted(entries))
    assert loaded.key_check == key_check(KEY)
    assert loaded.exported_at is not None
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


def _cli(*arguments: str, **environment: str) -> subprocess.CompletedProcess[str]:
    """Run ``scripts/erasure_journal.py`` as the operator does: a test may not import ``scripts/`` (`test_boundaries`).

    Both DSNs point at an address nothing listens on, so a connection attempt fails loudly with ``OperationalError``.
    A refusal that names a static code therefore proves the refusal came first, before the replay connected or wrote.
    """
    settings = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "CME_DELETION_RECEIPT_KEY": "k" * 32,
        "CME_DATABASE_URL": "postgresql://unused@127.0.0.1:1/unused",
        "CME_JOURNAL_DATABASE_URL": "postgresql://unused@127.0.0.1:1/unused",
        "CME_RUNTIME_ROLE": "unused",
    }
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "erasure_journal.py"),
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**settings, **environment},
    )


def test_a_missing_journal_is_not_an_empty_success(tmp_path: Path) -> None:
    """A rebuilt host without the off-host copy must fail, not report zero entries and exit 0 (`M1`).

    Run as a subprocess: a test may not import `scripts/` (`test_boundaries.py`), and the script must be exercised as
    the operator runs it anyway.
    """
    result = _cli("replay", "--journal", str(tmp_path / "absent.jsonl"))
    assert result.returncode == 1
    assert "journal_missing" in result.stderr
    assert "unused" not in result.stderr  # never the DSN or the role


def test_a_replay_without_the_backup_recovery_point_is_refused_before_connecting(tmp_path: Path) -> None:
    """#263: the recovery point is not skippable by omission -- replay exits 1 and names a static code.

    The unit tests below pin `check_recovery_point` itself; this pins the operator's path to it. Without the flag the
    check would never run, which is exactly the "skip by accident" the acceptance criteria name.
    """
    journal = tmp_path / "journal.jsonl"
    write_journal(journal, [entry()], schema_name="cme_engine", key=KEY)

    result = _cli("replay", "--journal", str(journal))

    assert result.returncode == 2, "a missing input is 'missing configuration' (2), not a refused journal (1)"
    assert "restored_from_backup_at_required" in result.stderr
    assert "OperationalError" not in result.stderr  # the refusal came before any connection attempt


def test_a_journal_older_than_the_backup_refuses_the_replay_before_connecting(tmp_path: Path) -> None:
    """#263: static code, non-zero exit, both timestamps, and never the key check, a user id, a target or the DSN."""
    journal = tmp_path / "journal.jsonl"
    write_journal(journal, [entry()], schema_name="cme_engine", key=KEY)
    lines = journal.read_text().splitlines()
    header = json.loads(lines[0]) | {"exported_at": "2026-11-26T00:00:00+00:00"}
    journal.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")

    result = _cli(
        "replay",
        "--restored-from-backup-at",
        "2026-11-27T00:00:00+00:00",
        "--restored-mode",
        "logical",
        "--journal",
        str(journal),
    )

    assert result.returncode == 1
    assert "journal_older_than_backup" in result.stderr
    assert "journal_timestamp=2026-11-26T00:00:00+00:00" in result.stderr
    assert "restored_from_backup_at=2026-11-27T00:00:00+00:00" in result.stderr
    # the refusal happens before the replay connects, which `deploy/README.md` states as a property of the procedure
    assert "OperationalError" not in result.stderr
    assert "alice" not in result.stderr  # never a user id
    assert "hmac-sha256" not in result.stderr  # never a target digest
    assert "127.0.0.1" not in result.stderr and "unused" not in result.stderr  # never the DSN or the role


def test_mark_reconciled_and_export_do_not_need_the_backup_recovery_point(tmp_path: Path) -> None:
    """The new required replay input must not close the two paths that have no recovery point to compare."""
    journal = tmp_path / "journal.jsonl"
    write_journal(journal, [entry()], schema_name="cme_engine", key=KEY)

    marked = _cli("replay", "--mark-reconciled", "--restored-mode", "in-place", "--journal", str(journal))
    exported = _cli("export", "--journal", str(journal))

    for result in (marked, exported):
        assert "restored_from_backup_at_required" not in result.stderr
        assert "OperationalError" in result.stderr  # reached the connection: the flag was never demanded


def test_a_replay_without_the_restore_mode_is_refused_before_connecting(tmp_path: Path) -> None:
    """#262: which restore this database is, is an input the replay cannot infer, so it is not skippable by omission.

    Recording it with the reconciliation is the acknowledgement a restore the identity cannot see (a data-only load, a
    PITR) needs where the operator acts (`T-49`), and the same class of missing input as the recovery point: exit 2, a
    static token, and no connection attempt.
    """
    journal = tmp_path / "journal.jsonl"
    write_journal(journal, [entry()], schema_name="cme_engine", key=KEY)

    result = _cli("replay", "--restored-from-backup-at", "2026-11-27T00:00:00+00:00", "--journal", str(journal))
    marked = _cli("replay", "--mark-reconciled", "--journal", str(journal))

    for refused in (result, marked):
        assert refused.returncode == 2
        assert "restored_mode_required" in refused.stderr
        assert "OperationalError" not in refused.stderr  # refused before any connection attempt


def test_the_restore_modes_are_the_ones_the_identity_records() -> None:
    """One spelling per restore path: the CLI's choices and the column's CHECK constraint must be the same set."""
    from custom_memory_engine.deletion.markers import RESTORED_MODES

    assert RESTORED_MODES == ("logical", "data-only", "physical", "in-place")
    migration = (
        Path(__file__).resolve().parents[1] / "src/custom_memory_engine/schema/034_restore_identity_witness.sql"
    ).read_text()
    for mode in RESTORED_MODES:
        assert f"'{mode}'" in migration, mode


def test_a_mark_reconciled_run_accepts_a_mode_and_reaches_the_database(tmp_path: Path) -> None:
    """The upgrade path takes a mode too: `--mark-reconciled --restored-mode in-place` is what the procedure says."""
    journal = tmp_path / "journal.jsonl"
    write_journal(journal, [entry()], schema_name="cme_engine", key=KEY)

    result = _cli("replay", "--mark-reconciled", "--restored-mode", "in-place", "--journal", str(journal))

    assert "restored_mode_required" not in result.stderr
    assert "OperationalError" in result.stderr  # the mode was accepted; the run stopped at the connection


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
