"""Deterministic fetcher, SHA-256 verifier, and structural auditor for LoCoMo (RL-021)."""

import argparse
import hashlib
import json
import shutil
import urllib.request
from collections.abc import Sequence
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)
console = Console()

BENCHMARK_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = BENCHMARK_DIR / "MANIFEST.txt"
DATA_DIR = BENCHMARK_DIR / "data"
FILENAME = "locomo10.json"
CACHED_LOCOMO_PATH = BENCHMARK_DIR.parents[2] / "reference/sources/pages/locomo_data.txt"
UPSTREAM_COMMIT = "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376"
UPSTREAM_URL = f"https://raw.githubusercontent.com/snap-research/locomo/{UPSTREAM_COMMIT}/data/locomo10.json"
EXPECTED_CATEGORY_COUNTS: dict[int, int] = {1: 282, 2: 321, 3: 96, 4: 841, 5: 446}


class LoCoMoVerificationSummary(BaseModel):
    """Verified conversation, question, evidence, and category counts for locomo10.json."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    filename: str
    sha256: str
    conversations: int
    total_questions: int
    empty_evidence_questions: int
    category_counts: dict[int, int]
    license: str = "CC-BY-NC-4.0"


def load_manifest(manifest_path: Path = MANIFEST_PATH) -> dict[str, str]:
    """Parse MANIFEST.txt into a mapping of filename -> expected SHA-256 hex digest."""
    entries: dict[str, str] = {}
    for raw_line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError("invalid_manifest_line")
        entries[parts[1]] = parts[0].lower()
    return entries


def compute_sha256(file_path: Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
def _download_file(url: str, destination: Path) -> None:
    """Download a benchmark file with exponential-backoff retry."""
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as out_file:
        shutil.copyfileobj(response, out_file, length=1024 * 1024)


def ensure_locomo_file(
    *,
    data_dir: Path = DATA_DIR,
    manifest_path: Path = MANIFEST_PATH,
    allow_download: bool = True,
) -> Path:
    """Ensure locomo10.json is present in data_dir and matches MANIFEST.txt."""
    manifest = load_manifest(manifest_path)
    expected_sha256 = manifest[FILENAME]
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / FILENAME

    if target.exists() and compute_sha256(target) == expected_sha256:
        return target

    if CACHED_LOCOMO_PATH.exists():
        shutil.copyfile(CACHED_LOCOMO_PATH, target)
        if compute_sha256(target) == expected_sha256:
            return target

    if not allow_download:
        raise FileNotFoundError(f"missing_or_unverified_file: {target}")

    tmp_target = target.with_suffix(".json.part")
    logger.info("locomo_download_started", filename=FILENAME)
    _download_file(UPSTREAM_URL, tmp_target)
    actual_sha256 = compute_sha256(tmp_target)
    if actual_sha256 != expected_sha256:
        tmp_target.unlink(missing_ok=True)
        raise ValueError("sha256_mismatch")
    tmp_target.replace(target)
    logger.info("locomo_download_completed", filename=FILENAME, sha256=actual_sha256)
    return target


def verify_locomo_file(
    *,
    file_path: Path | None = None,
    manifest_path: Path = MANIFEST_PATH,
) -> LoCoMoVerificationSummary:
    """Verify locomo10.json SHA-256 and exact conversation/question/category counts."""
    manifest = load_manifest(manifest_path)
    expected_sha256 = manifest[FILENAME]
    resolved_path = file_path if file_path is not None else (DATA_DIR / FILENAME)
    if not resolved_path.exists():
        raise FileNotFoundError(f"benchmark_file_not_found: {resolved_path}")

    actual_sha256 = compute_sha256(resolved_path)
    if actual_sha256 != expected_sha256:
        raise ValueError("sha256_mismatch")

    records = json.loads(resolved_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("expected_json_array")

    conversations = len(records)
    total_questions = 0
    empty_evidence_questions = 0
    category_counts: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    for conversation in records:
        qa_list = conversation.get("qa", [])
        for qa_item in qa_list:
            total_questions += 1
            cat = int(qa_item["category"])
            category_counts[cat] = category_counts.get(cat, 0) + 1
            evidence = qa_item.get("evidence", [])
            if not evidence:
                empty_evidence_questions += 1

    if (
        conversations != 10
        or total_questions != 1986
        or empty_evidence_questions != 4
        or category_counts != EXPECTED_CATEGORY_COUNTS
    ):
        raise ValueError("unexpected_locomo_counts")

    logger.info(
        "locomo_verified",
        conversations=conversations,
        total_questions=total_questions,
        empty_evidence_questions=empty_evidence_questions,
    )
    return LoCoMoVerificationSummary(
        filename=FILENAME,
        sha256=actual_sha256,
        conversations=conversations,
        total_questions=total_questions,
        empty_evidence_questions=empty_evidence_questions,
        category_counts=category_counts,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for fetching and verifying locomo10.json."""
    parser = argparse.ArgumentParser(description="Fetch and verify LoCoMo benchmark data.")
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Do not download over the network; use local data/ or cached files only.",
    )
    args = parser.parse_args(argv)
    path = ensure_locomo_file(allow_download=not args.no_download)
    summary = verify_locomo_file(file_path=path)
    console.print_json(summary.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
