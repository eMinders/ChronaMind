"""The documented source-checkout command produces deterministic synthetic outputs."""

import subprocess
import sys
from pathlib import Path


def test_documented_cli_two_runs(tmp_path: Path) -> None:
    engine = Path(__file__).resolve().parents[3]
    command = [
        sys.executable,
        "-m",
        "evaluation.harness",
        "--config",
        "evaluation/harness/fixtures/config.json",
        "--cases",
        "evaluation/harness/fixtures/dev.json",
        "--suite",
        "T",
        "--split",
        "dev",
        "--runs-root",
        str(tmp_path / "runs"),
    ]
    for run_id in ("first", "second"):
        result = subprocess.run(command + ["--run-id", run_id], cwd=engine, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    for name in ("answers.jsonl", "scores.jsonl"):
        assert (tmp_path / f"runs/first/fixture-dummy/T/{name}").read_bytes() == (
            tmp_path / f"runs/second/fixture-dummy/T/{name}"
        ).read_bytes()
    duplicate = subprocess.run(command + ["--run-id", "first"], cwd=engine, capture_output=True, timeout=30)
    assert duplicate.returncode == 2
