"""Source-checkout CLI for the local dummy; real providers are not implemented here."""

import argparse
from functools import partial
from pathlib import Path

from rich.console import Console

from evaluation.harness.config import load_config
from evaluation.harness.dataset import load_cases
from evaluation.harness.dummy import DummyAdapter, dummy_score
from evaluation.harness.runner import run_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument(
        "--suite",
        required=True,
        choices=("T", "G", "D", "I", "C", "LongMemEval_oracle", "LongMemEval_S", "LongMemEval_M", "LoCoMo"),
    )
    parser.add_argument("--split", required=True, choices=("dev", "test"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-root", type=Path, default=Path("evaluation/runs"))
    parser.add_argument("--confirm-one-shot-test-run", action="store_true")
    parser.add_argument("--pilot-freeze", type=Path)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    console = Console()
    try:
        destination = run_evaluation(
            config=load_config(args.config),
            suite_id=args.suite,
            split=args.split,
            split_path=args.cases,
            case_loader=partial(load_cases, args.cases, args.split, args.suite),
            adapter_factory=DummyAdapter,
            scorer=dummy_score,
            run_id=args.run_id,
            output_root=args.runs_root,
            repo_root=repo_root,
            confirm_one_shot_test_run=args.confirm_one_shot_test_run,
            pilot_freeze_path=args.pilot_freeze,
        )
    except (ValueError, OSError):
        console.print("[red]Harness refused or failed the run. Inspect configuration and local run state.[/red]")
        return 2
    console.print(f"[green]Synthetic run completed:[/green] {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
