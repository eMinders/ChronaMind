"""Exclusive run directories and canonical JSONL; a manifest commits success last."""

import hashlib
import os
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import IO

from evaluation.harness.config import canonical_json
from evaluation.harness.paths import directory, run_destination


class ArtifactWriter:
    """Never reuse a run directory or overwrite a successful/failed run artifact."""

    def __init__(self, root: Path, run_id: str, config_id: str, suite_id: str) -> None:
        self.destination = run_destination(root, run_id, config_id, suite_id)
        directory(self.destination.parent)
        self.destination.mkdir(exist_ok=False)
        self._streams: dict[str, IO[bytes]] = {}
        self._complete = False
        try:
            for name in ("answers", "scores", "per_cluster"):
                self._streams[name] = (self.destination / f"{name}.jsonl").open("xb")
        except BaseException:
            self._close()
            raise

    def __enter__(self) -> "ArtifactWriter":
        return self

    def append(self, name: str, value: object) -> None:
        if self._complete:
            raise ValueError("run_already_finalized")
        if name not in ("answers", "scores", "per_cluster", "adapter_events"):
            raise ValueError("unsupported_artifact_stream")
        encoded = canonical_json(value)
        if name not in self._streams:
            self._streams[name] = (self.destination / f"{name}.jsonl").open("xb")
        self._streams[name].write(encoded)

    def _close(self) -> None:
        for stream in self._streams.values():
            if not stream.closed:
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()

    def finish(self, manifest: dict[str, object]) -> None:
        self._close()
        manifest["artifact_sha256"] = {
            f"{name}.jsonl": hashlib.sha256((self.destination / f"{name}.jsonl").read_bytes()).hexdigest()
            for name in self._streams
        }
        temporary = self.destination / ".manifest.tmp"
        with temporary.open("xb") as stream:
            stream.write(canonical_json(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        # A hard-link publication is atomic and refuses an existing destination
        # on both Windows and POSIX. Never use replace() on an immutable artifact.
        os.link(temporary, self.destination / "manifest.json")
        self._complete = True
        try:
            temporary.unlink()
        except OSError:
            # Publication succeeded; a leftover staging link is not a failed run.
            pass

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self._close()
        if not self._complete:
            # Exception messages may contain provider credentials or private content.
            failure = {"status": "failed", "exception_type": exc_type.__name__ if exc_type else "IncompleteRun"}
            with (self.destination / "FAILED.json").open("xb") as stream:
                stream.write(canonical_json(failure))


def percentile(values: Iterable[float], fraction: float) -> float | None:
    """Linear interpolated descriptive percentile; empty samples remain missing."""
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
