"""CI check for gate G-TS (V2-D42): test-split integrity and freeze coverage.

Exit 0 when every frozen split verifies and every suite is frozen; exit 1 otherwise.
Confirmatory authorization (J7/J8 sign-offs) is reported, not required: no confirmatory
run is authorized until a signed PilotFreeze exists, which is the intended state.
"""

import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.harness.split_gate import check_integrity  # noqa: E402


def main() -> int:
    console = Console()
    report = check_integrity()
    table = Table(title="Gate G-TS — test-split integrity (V2-D42)")
    table.add_column("frozen split file")
    table.add_column("sha256", overflow="fold")
    for name, digest in sorted(report.frozen_digests.items()):
        table.add_row(name, digest)
    console.print(table)
    if not report.integrity_passed:
        console.print(f"[red]G-TS integrity FAILED[/red]: {report.failure} unfrozen={list(report.unfrozen_suites)}")
        return 1
    console.print("G-TS integrity PASSED (J5 + coverage). Confirmatory runs still require a signed PilotFreeze.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
