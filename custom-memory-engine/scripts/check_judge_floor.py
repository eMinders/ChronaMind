"""Verify ``JUDGE_RELEVANCE_FLOOR`` against an ONNX cross-encoder's uncensored extremes (``V2-D104``, ``V2-D118``).

Run:   uv run --no-sync python scripts/check_judge_floor.py
       uv run --no-sync python scripts/check_judge_floor.py --asset jina-reranker-v2-base-multilingual
Exit:  0 if the floor sits strictly inside (strongest_unanswerable, weakest_answerable),
       1 if outside,
       2 if the asset directory/files are missing or invalid.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_memory_engine.engine.hybrid import JUDGE_RELEVANCE_FLOOR  # noqa: E402
from custom_memory_engine.integrations.cross_encoder import CrossEncoderError  # noqa: E402
from evaluation.retrieval.judge_floor import measure_asset_floor  # noqa: E402

DEFAULT_ASSET = "bge-reranker-v2-m3"


def _resolve_asset(cli_asset: str | None) -> str:
    if cli_asset is not None:
        stripped = cli_asset.strip()
        return stripped or DEFAULT_ASSET
    return os.environ.get("CME_RERANKER_ASSET", "").strip() or DEFAULT_ASSET


def _resolve_directory(cli_dir: Path | None, asset: str, cli_asset_given: bool) -> Path:
    if cli_dir is not None:
        return cli_dir
    env_asset = os.environ.get("CME_RERANKER_ASSET", "").strip()
    env_dir = os.environ.get("CME_RERANKER_DIR", "").strip()
    if env_dir and (not cli_asset_given or asset == env_asset):
        return Path(env_dir)
    return Path(".model-cache") / asset


def _resolve_floor(cli_floor: float | None) -> float:
    if cli_floor is not None:
        value = float(cli_floor)
    else:
        raw = os.environ.get("CME_RERANKER_FLOOR", "").strip()
        value = float(raw) if raw else JUDGE_RELEVANCE_FLOOR
    if not math.isfinite(value):
        raise ValueError("floor_must_be_finite")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Verify a relevance judge floor against uncensored full-corpus extremes.",
    )
    parser.add_argument(
        "--asset",
        default=None,
        help=f"reranker asset name (default: CME_RERANKER_ASSET or {DEFAULT_ASSET})",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="asset directory (default: CME_RERANKER_DIR or .model-cache/<asset>)",
    )
    parser.add_argument(
        "--floor",
        type=float,
        default=None,
        help=f"candidate relevance floor (default: CME_RERANKER_FLOOR or {JUDGE_RELEVANCE_FLOOR})",
    )
    parser.add_argument("--threads", type=int, default=0, help="ONNX intra-op thread count (0 = default)")
    args = parser.parse_args(argv)

    asset = _resolve_asset(args.asset)
    directory = _resolve_directory(args.dir, asset, args.asset is not None)
    try:
        floor = _resolve_floor(args.floor)
    except ValueError as error:
        print(f"check_judge_floor: invalid floor ({error})", file=sys.stderr)
        return 2

    if not directory.is_dir():
        print(f"check_judge_floor: asset directory not found: {directory}", file=sys.stderr)
        return 2

    try:
        report = asyncio.run(measure_asset_floor(asset, directory, floor=floor, threads=args.threads))
    except CrossEncoderError as error:
        print(f"check_judge_floor: missing or invalid asset ({error.code}) in {directory}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"check_judge_floor: failed to read asset in {directory} ({type(error).__name__})", file=sys.stderr)
        return 2

    print(report.format_line())
    return 0 if report.inside else 1


if __name__ == "__main__":
    raise SystemExit(main())
