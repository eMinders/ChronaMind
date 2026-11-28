"""Isolated worker bootstrap: no provider secrets, inherited dotenv or vendor output."""

import runpy
import sys


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"b3_mem0", "b4_graphiti"}:
        raise ValueError("explicit_vendor_worker_required")
    # The protocol captures __stdout__; all incidental SDK print/log output goes
    # to the parent's discarded stderr, never into artifacts or model payloads.
    sys.stdout = sys.stderr
    runpy.run_module(f"evaluation.baselines.{sys.argv[1]}", run_name="__main__")


if __name__ == "__main__":
    main()
