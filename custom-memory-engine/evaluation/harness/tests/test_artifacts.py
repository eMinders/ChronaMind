"""Failure injection must never publish a false success or overwrite artifacts."""

import os
from pathlib import Path

import pytest

from evaluation.harness.artifacts import ArtifactWriter


def test_failed_manifest_fsync_never_publishes_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    fsync = os.fsync

    def fail_manifest_sync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:  # Three JSONL flushes have completed; next is the manifest.
            raise OSError("synthetic disk failure")
        fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_manifest_sync)
    with pytest.raises(OSError):
        with ArtifactWriter(tmp_path, "run", "config", "T") as writer:
            writer.finish({"status": "complete"})
    assert not (tmp_path / "run/config/T/manifest.json").exists()
    assert (tmp_path / "run/config/T/FAILED.json").exists()


def test_manifest_publication_refuses_preexisting_file(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        with ArtifactWriter(tmp_path, "run", "config", "T") as writer:
            (writer.destination / "manifest.json").write_bytes(b"unrelated-existing-data")
            writer.finish({"status": "complete"})
    assert (tmp_path / "run/config/T/manifest.json").read_bytes() == b"unrelated-existing-data"
