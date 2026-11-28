"""Fetch the pinned model files a deployment or a benchmark needs, and prove they are the pinned bytes.

Three reasons this is a script and not a paragraph in a README:

* **Reproducibility.** A measurement is only a finding if the same bytes produce it. Every file here is fetched from an
  immutable Hugging Face *revision* and then checked against a recorded SHA-256, so a re-run either produces the exact
  cache a report was measured on or fails loudly.
* **The tests depend on it.** The embedding and reranking tests skip when their assets are absent, and their skip
  reasons name this script. A missing fetch path is how a suite quietly stops covering its most expensive properties.
* **It is entirely optional at runtime.** Nothing in `src/` imports this. A deployment that has no need for the dense
  arm or the judge runs lexical-only, and says so (`V2-D98`, `V2-D104`).

Usage::

    python scripts/fetch_models.py --check          # verify what is cached, download nothing
    python scripts/fetch_models.py                  # fetch everything missing
    python scripts/fetch_models.py bge-m3           # fetch one asset by name
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".model-cache"


@dataclass(frozen=True, slots=True)
class Pinned:
    """One file: where it comes from, where it lands, and what it must hash to."""

    asset: str
    revision: str
    remote: str
    local: str
    sha256: str
    #: Bytes, so a truncated download is caught before the hash is even computed (a partial file from a dropped
    #: connection is the failure mode an unattended fetch actually meets).
    size: int


#: The pinned set. Every hex string below is a *public* digest or git revision of an open model file, marked for the
#: secret scanner line by line because it cannot tell a SHA-256 from a key. Revisions are commit SHAs, not branch
#: names: a branch moves, and a moved branch would silently invalidate every number in `reports/`. Digests are of the
#: *file*, so a re-upload under the same name is caught.
PINNED: tuple[Pinned, ...] = (
    # BAAI/bge-m3 at 5617a9f: the `onnx/` subdirectory, which is the exported graph (the repository root carries the
    # PyTorch weights and a different tokenizer). `model.onnx` is a 724 KB graph whose weights live in the 2.2 GB
    # `model.onnx_data` sidecar, so both files are pinned: a graph without its data is not a model.
    Pinned(
        asset="bge-m3",
        # pragma: allowlist nextline secret
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        remote="onnx/model.onnx",
        local="model.onnx",
        # pragma: allowlist nextline secret
        sha256="f84251230831afb359ab26d9fd37d5936d4d9bb5d1d5410e66442f630f24435b",
        size=724923,
    ),
    Pinned(
        asset="bge-m3",
        # pragma: allowlist nextline secret
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        remote="onnx/model.onnx_data",
        local="model.onnx_data",
        # pragma: allowlist nextline secret
        sha256="1eebfb28493f67bba03ce0ef64bfdc7fc5a3bd9d7493f818bb1d78cd798416b4",
        size=2266820608,
    ),
    Pinned(
        asset="bge-m3",
        # pragma: allowlist nextline secret
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        remote="onnx/tokenizer.json",
        local="tokenizer.json",
        # pragma: allowlist nextline secret
        sha256="6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790",
        size=17082821,
    ),
    # onnx-community/bge-reranker-v2-m3-ONNX at 6f5ff65: the int8 export, which is what `V2-D104` measured. The token
    # budget is 512 pairs, so the graph and its tokenizer are pinned together.
    Pinned(
        asset="bge-reranker-v2-m3",
        # pragma: allowlist nextline secret
        revision="6f5ff65298512715a1e669753bc754d2bc8f367b",
        remote="onnx/model_int8.onnx",
        local="model_int8.onnx",
        # pragma: allowlist nextline secret
        sha256="912fc1215c2dbff6499700534bd8d31253af01573861abbfc43afd1fab6cce5d",
        size=570727094,
    ),
    Pinned(
        asset="bge-reranker-v2-m3",
        # pragma: allowlist nextline secret
        revision="6f5ff65298512715a1e669753bc754d2bc8f367b",
        remote="tokenizer.json",
        local="tokenizer.json",
        # pragma: allowlist nextline secret
        sha256="8bf8afbfd11306bd872018c53bfdf2e160a56f8edbcf49933324404791c148d3",
        size=17082900,
    ),
    Pinned(
        asset="bge-reranker-v2-m3",
        # pragma: allowlist nextline secret
        revision="6f5ff65298512715a1e669753bc754d2bc8f367b",
        remote="config.json",
        local="config.json",
        # pragma: allowlist nextline secret
        sha256="122e922dcfed6503c8721e6fe1daf090340c3d95ca7f3aa3a72730b321a51cfd",
        size=848,
    ),
)


def digest(path: Path) -> str:
    accumulator = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            accumulator.update(block)
    return accumulator.hexdigest()


def verify(entry: Pinned, *, report: list[str]) -> bool:
    """Whether the cached file is the pinned one. Anything else is a *failure*, never a warning."""
    target = CACHE / entry.asset / entry.local
    if not target.is_file():
        report.append(f"MISSING {entry.asset}/{entry.local}")
        return False
    actual_size = target.stat().st_size
    if actual_size != entry.size:
        report.append(f"SIZE    {entry.asset}/{entry.local}: {actual_size} != {entry.size}")
        return False
    actual = digest(target)
    if actual != entry.sha256:
        report.append(f"DIGEST  {entry.asset}/{entry.local}: {actual} != {entry.sha256}")
        return False
    report.append(f"OK      {entry.asset}/{entry.local}")
    return True


def fetch(entry: Pinned) -> None:
    """Download one file atomically: a partial download must never look like a cached asset."""
    target = CACHE / entry.asset / entry.local
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/{_repo(entry)}/resolve/{entry.revision}/{entry.remote}"
    partial = target.with_suffix(target.suffix + ".part")
    print(f"fetching {entry.asset}/{entry.local} ({entry.size / 1e6:.1f} MB)", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as handle:  # noqa: S310
        while chunk := response.read(1 << 20):
            handle.write(chunk)
    if partial.stat().st_size != entry.size or digest(partial) != entry.sha256:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"refusing to cache {entry.asset}/{entry.local}: the fetched bytes are not the pinned ones")
    partial.replace(target)


def _repo(entry: Pinned) -> str:
    return {"bge-m3": "BAAI/bge-m3", "bge-reranker-v2-m3": "onnx-community/bge-reranker-v2-m3-ONNX"}[entry.asset]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fetch and verify the pinned model cache")
    parser.add_argument("assets", nargs="*", help="asset names to act on (default: all)")
    parser.add_argument("--check", action="store_true", help="verify only; download nothing")
    options = parser.parse_args(argv)
    selected = [entry for entry in PINNED if not options.assets or entry.asset in options.assets]
    if not selected:
        print(f"no pinned asset named {options.assets}", file=sys.stderr)
        return 2
    failed = 0
    for entry in selected:
        report: list[str] = []
        if not verify(entry, report=report) and not options.check:
            fetch(entry)
            report = []
            verify(entry, report=report)
        print("\n".join(report), file=sys.stderr)
        if any(line.startswith(("MISSING", "SIZE", "DIGEST")) for line in report):
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
