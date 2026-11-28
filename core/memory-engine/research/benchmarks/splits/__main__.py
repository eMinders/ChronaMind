"""CLI entrypoint for `python -m research.benchmarks.splits` (RL-026)."""

from research.benchmarks.splits.freeze import main

if __name__ == "__main__":
    raise SystemExit(main())
