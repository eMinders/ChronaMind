"""Deterministic fetcher, SHA-256 verifier, and D-23 anomaly auditor for LongMemEval (RL-020)."""

import argparse
import hashlib
import json
import shutil
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from custom_memory_engine.protocols import (
    ReplaySession,
    ReplayTurn,
    ScheduledTurn,
    reference_time,
    schedule_replay,
)

logger = structlog.get_logger(__name__)
console = Console()

BENCHMARK_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = BENCHMARK_DIR / "MANIFEST.txt"
DATA_DIR = BENCHMARK_DIR / "data"
CACHED_ORACLE_PATH = BENCHMARK_DIR.parents[2] / "reference/sources/pages/lme_oracle.json"
HF_COMMIT = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
HF_BASE_URL = f"https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/{HF_COMMIT}"
LME_DATE_FORMAT = "%Y/%m/%d (%a) %H:%M"

VARIANT_FILES: dict[str, str] = {
    "oracle": "longmemeval_oracle.json",
    "s": "longmemeval_s_cleaned.json",
    "m": "longmemeval_m_cleaned.json",
}

EXPECTED_VARIANT_ANOMALIES: dict[str, tuple[int, int, int, int, int]] = {
    "oracle": (34, 43, 34, 43, 0),
    "s": (39, 44, 211, 76, 95),
}


class LongMemEvalVerificationSummary(BaseModel):
    """Verified structural and temporal-anomaly counts for a LongMemEval file."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    variant: str
    filename: str
    sha256: str
    total_questions: int
    abstention_questions: int
    single_session_assistant_questions: int
    answerable_questions: int
    primary_recall_eligible_questions: int
    evidence_out_of_order_histories: int
    evidence_question_before_last_session_histories: int
    full_haystack_out_of_order_histories: int
    full_haystack_question_before_last_session_histories: int
    tied_session_timestamp_histories: int
    replay_schedules_verified: int
    question_type_counts: dict[str, int]


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
        digest, filename = parts[0].lower(), parts[1]
        entries[filename] = digest
    return entries


def compute_sha256(file_path: Path) -> str:
    """Compute the SHA-256 hex digest of a file in 1 MiB chunks."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def parse_lme_timestamp(raw: str) -> datetime:
    """Parse LongMemEval timestamp string 'YYYY/MM/DD (Day) HH:MM' as timezone-aware UTC."""
    date_part = raw[:10]
    time_part = raw[-5:]
    parsed = datetime.strptime(f"{date_part} {time_part}", "%Y/%m/%d %H:%M")
    return parsed.replace(tzinfo=UTC)


def build_replay_schedule_for_item(item: Mapping[str, Any]) -> tuple[tuple[ScheduledTurn, ...], datetime]:
    """Build D-23 monotonic replay schedule and clamped reference time rho for one LongMemEval item."""
    qid = str(item["question_id"])
    q_dt = parse_lme_timestamp(str(item["question_date"]))
    raw_dates = [str(d) for d in item["haystack_dates"]]
    raw_sids = [str(sid) for sid in item["haystack_session_ids"]]
    raw_sessions = list(item["haystack_sessions"])

    sessions: list[ReplaySession] = []
    for s_idx, (sid, date_str, turns_raw) in enumerate(zip(raw_sids, raw_dates, raw_sessions, strict=True)):
        session_ts = parse_lme_timestamp(date_str)
        unique_sid = f"{qid}_s{s_idx:04d}_{sid}"
        replay_turns = tuple(
            ReplayTurn(turn_id=f"{unique_sid}_t{t_idx:04d}", source_timestamp=session_ts)
            for t_idx in range(len(turns_raw))
        )
        sessions.append(ReplaySession(session_id=unique_sid, timestamp=session_ts, turns=replay_turns))

    scheduled = schedule_replay(sessions)
    rho = reference_time(q_dt, scheduled)
    return scheduled, rho


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
def _download_file(url: str, destination: Path) -> None:
    """Download a benchmark file with exponential-backoff retry."""
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as out_file:
        shutil.copyfileobj(response, out_file, length=1024 * 1024)


def ensure_variant_file(
    variant: str,
    *,
    data_dir: Path = DATA_DIR,
    manifest_path: Path = MANIFEST_PATH,
    allow_download: bool = True,
) -> Path:
    """Ensure the requested LongMemEval variant exists in data_dir and matches MANIFEST.txt."""
    key = variant.lower()
    if key not in VARIANT_FILES:
        raise ValueError("unknown_variant")
    filename = VARIANT_FILES[key]
    manifest = load_manifest(manifest_path)
    expected_sha256 = manifest[filename]
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / filename

    if target.exists() and compute_sha256(target) == expected_sha256:
        return target

    if key == "oracle" and CACHED_ORACLE_PATH.exists():
        shutil.copyfile(CACHED_ORACLE_PATH, target)
        if compute_sha256(target) == expected_sha256:
            return target

    if not allow_download:
        raise FileNotFoundError(f"missing_or_unverified_file: {target}")

    url = f"{HF_BASE_URL}/{filename}"
    tmp_target = target.with_suffix(".json.part")
    logger.info("longmemeval_download_started", variant=key, filename=filename)
    _download_file(url, tmp_target)
    actual_sha256 = compute_sha256(tmp_target)
    if actual_sha256 != expected_sha256:
        tmp_target.unlink(missing_ok=True)
        raise ValueError("sha256_mismatch")
    tmp_target.replace(target)
    logger.info("longmemeval_download_completed", variant=key, filename=filename, sha256=actual_sha256)
    return target


def audit_longmemeval_records(
    records: Sequence[Mapping[str, Any]],
    *,
    variant: str,
    filename: str,
    sha256: str,
    verify_replay: bool = True,
) -> LongMemEvalVerificationSummary:
    """Compute question counts, D-23 timestamp anomalies, and optional replay-schedule checks."""
    total_questions = len(records)
    abstention_questions = 0
    single_session_assistant_questions = 0
    evidence_out_of_order = 0
    evidence_q_before_last = 0
    full_out_of_order = 0
    full_q_before_last = 0
    tied_session_histories = 0
    replay_verified = 0
    question_type_counts: dict[str, int] = {}

    for item in records:
        qid = str(item["question_id"])
        qtype = str(item["question_type"])
        question_type_counts[qtype] = question_type_counts.get(qtype, 0) + 1
        if qid.endswith("_abs"):
            abstention_questions += 1
        if qtype == "single-session-assistant":
            single_session_assistant_questions += 1

        q_dt = parse_lme_timestamp(str(item["question_date"]))
        raw_dates = [str(d) for d in item["haystack_dates"]]
        parsed_dates = [parse_lme_timestamp(d) for d in raw_dates]
        session_ids = [str(sid) for sid in item["haystack_session_ids"]]
        answer_sids = {str(sid) for sid in item["answer_session_ids"]}

        if any(parsed_dates[i] > parsed_dates[i + 1] for i in range(len(parsed_dates) - 1)):
            full_out_of_order += 1
        if parsed_dates and q_dt < max(parsed_dates):
            full_q_before_last += 1
        if len(set(parsed_dates)) < len(parsed_dates):
            tied_session_histories += 1

        evidence_dates = [dt for sid, dt in zip(session_ids, parsed_dates, strict=True) if sid in answer_sids]
        if any(evidence_dates[i] > evidence_dates[i + 1] for i in range(len(evidence_dates) - 1)):
            evidence_out_of_order += 1
        if evidence_dates and q_dt < max(evidence_dates):
            evidence_q_before_last += 1

        if verify_replay:
            scheduled, rho = build_replay_schedule_for_item(item)
            if scheduled and rho < scheduled[-1].recorded_clock:
                raise ValueError("reference_time_precedes_last_recorded_clock")
            replay_verified += 1

    return LongMemEvalVerificationSummary(
        variant=variant,
        filename=filename,
        sha256=sha256,
        total_questions=total_questions,
        abstention_questions=abstention_questions,
        single_session_assistant_questions=single_session_assistant_questions,
        answerable_questions=total_questions - abstention_questions,
        primary_recall_eligible_questions=total_questions - abstention_questions - single_session_assistant_questions,
        evidence_out_of_order_histories=evidence_out_of_order,
        evidence_question_before_last_session_histories=evidence_q_before_last,
        full_haystack_out_of_order_histories=full_out_of_order,
        full_haystack_question_before_last_session_histories=full_q_before_last,
        tied_session_timestamp_histories=tied_session_histories,
        replay_schedules_verified=replay_verified,
        question_type_counts=question_type_counts,
    )


def verify_longmemeval_file(
    variant: str,
    *,
    file_path: Path | None = None,
    manifest_path: Path = MANIFEST_PATH,
    verify_replay: bool = False,
) -> LongMemEvalVerificationSummary:
    """Verify SHA-256 and recompute exact question and D-23 timestamp anomaly counts."""
    key = variant.lower()
    if key not in VARIANT_FILES:
        raise ValueError("unknown_variant")
    filename = VARIANT_FILES[key]
    manifest = load_manifest(manifest_path)
    expected_sha256 = manifest[filename]
    resolved_path = file_path if file_path is not None else (DATA_DIR / filename)
    if not resolved_path.exists():
        raise FileNotFoundError(f"benchmark_file_not_found: {resolved_path}")

    actual_sha256 = compute_sha256(resolved_path)
    if actual_sha256 != expected_sha256:
        raise ValueError("sha256_mismatch")

    records = json.loads(resolved_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("expected_json_array")

    summary = audit_longmemeval_records(
        records,
        variant=key,
        filename=filename,
        sha256=actual_sha256,
        verify_replay=verify_replay or (key == "oracle"),
    )

    if (
        summary.total_questions != 500
        or summary.abstention_questions != 30
        or summary.single_session_assistant_questions != 56
    ):
        raise ValueError("unexpected_longmemeval_question_counts")

    if key in EXPECTED_VARIANT_ANOMALIES:
        expected_tuple = EXPECTED_VARIANT_ANOMALIES[key]
        actual_tuple = (
            summary.evidence_out_of_order_histories,
            summary.evidence_question_before_last_session_histories,
            summary.full_haystack_out_of_order_histories,
            summary.full_haystack_question_before_last_session_histories,
            summary.tied_session_timestamp_histories,
        )
        if actual_tuple != expected_tuple:
            raise ValueError("unexpected_longmemeval_anomalies")

    logger.info(
        "longmemeval_verified",
        variant=key,
        total_questions=summary.total_questions,
        primary_recall_eligible=summary.primary_recall_eligible_questions,
        full_out_of_order=summary.full_haystack_out_of_order_histories,
        tied_histories=summary.tied_session_timestamp_histories,
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for fetching and verifying LongMemEval variants."""
    parser = argparse.ArgumentParser(description="Fetch and verify LongMemEval benchmark files.")
    parser.add_argument(
        "--variants",
        nargs="+",
        default=["oracle", "s"],
        choices=["oracle", "s", "m"],
        help="Variants to fetch and verify (default: oracle s).",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Do not download over the network; use local data/ or cached files only.",
    )
    args = parser.parse_args(argv)
    for variant in args.variants:
        path = ensure_variant_file(variant, allow_download=not args.no_download)
        summary = verify_longmemeval_file(variant, file_path=path)
        console.print_json(summary.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
