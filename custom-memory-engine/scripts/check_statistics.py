"""Reproduce every numerical bound quoted in PLAN/V2/STATISTICS.md (SD-007).

This is a *validation* script, not engine code: it depends only on the already-verified
v1 Clopper-Pearson module and the standard library. It exists so that no number in the
V2 documentation is asserted without a runnable derivation.

Run:  uv run python scripts/check_statistics.py
Exit: 0 if every assertion holds, 1 otherwise.
"""

from __future__ import annotations

import sys
from math import ceil, log
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validation.cp_bounds import cp_upper  # noqa: E402  (v1 asset, unchanged)

ALPHA = 0.05  # two-sided 95% == one-sided 97.5%; the single system convention
TAU_CONF = 0.90
TAU_COV = 0.60


def ledger(support: int, counter: int, undetermined: int) -> dict[str, float]:
    """Partially identified failure-rate bound (STATISTICS.md §2.2, §4.2)."""
    opportunity = support + counter + undetermined
    if opportunity == 0:
        raise ValueError("opportunity_count_must_be_positive")
    upper = cp_upper(counter + undetermined, opportunity)  # undetermined counted as failure
    return {
        "o": opportunity,
        "coverage": (support + counter) / opportunity,
        "conf_lower": 1.0 - upper,
        "cp_upper": upper,
        "rate_lower": counter / opportunity,
        "rate_upper": (counter + undetermined) / opportunity,
    }


def admitted(row: dict[str, float], counter: int) -> bool:
    """Admission (STATISTICS.md §4.2).

    Conditions: partially-identified bound above tau, coverage above tau_coverage, and
    for the zero-counter case an explicit resolved-only bound so that a large opportunity
    count cannot be mistaken for evidence.
    """
    if row["conf_lower"] < TAU_CONF:
        return False
    if row["coverage"] < TAU_COV:
        return False
    if counter == 0 and cp_upper(0, int(row["o"])) > 1.0 - TAU_CONF:
        return False
    return True


CASES = {
    "r3_regression_sparse_unknown": (5, 0, 45),  # MUST be rejected
    "all_resolved": (40, 0, 0),  # MUST be admitted
    "some_unknown": (30, 0, 10),  # MUST be rejected
    "small_n": (8, 0, 0),  # MUST be rejected
    "with_counters": (30, 2, 8),  # MUST be rejected
}
EXPECTED = {
    "r3_regression_sparse_unknown": (50, 0.10, 0.0333, False),
    "all_resolved": (40, 1.00, 0.9119, True),
    "some_unknown": (40, 0.75, 0.5880, False),
    "small_n": (8, 1.00, 0.6306, False),
    "with_counters": (40, 0.80, 0.5880, False),
}


def main() -> int:
    failures: list[str] = []

    print("== admission table (u counted as failure) ==")
    for name, (s, k, u) in CASES.items():
        row = ledger(s, k, u)
        verdict = admitted(row, k)
        exp_o, exp_cov, exp_conf, exp_adm = EXPECTED[name]
        print(
            f"  {name:30s} o={row['o']:3d} coverage={row['coverage']:.2f} "
            f"conf_lower={row['conf_lower']:.4f} admitted={verdict}"
        )
        if row["o"] != exp_o:
            failures.append(f"{name}: o {row['o']} != {exp_o}")
        if abs(row["coverage"] - exp_cov) > 5e-3:
            failures.append(f"{name}: coverage {row['coverage']:.4f} != {exp_cov}")
        if abs(row["conf_lower"] - exp_conf) > 5e-4:
            failures.append(f"{name}: conf_lower {row['conf_lower']:.4f} != {exp_conf}")
        if verdict != exp_adm:
            failures.append(f"{name}: admitted {verdict} != {exp_adm}")

    print("\n== zero-counter thresholds ==")
    for label, alpha, expected_n in (
        ("one-sided 95%", 0.05, 29),
        ("one-sided 97.5% (system)", 0.025, 36),
    ):
        n = ceil(log(alpha) / log(TAU_CONF))
        bound = 1 - alpha ** (1 / n)
        print(f"  {label:26s} o >= {n:3d}  bound={bound:.4f}")
        if n != expected_n:
            failures.append(f"threshold {label}: {n} != {expected_n}")

    print("\n== agreement with the verified v1 asset ==")
    for n in (8, 29, 36, 40, 100):
        closed = 1 - 0.025 ** (1 / n)
        bisected = cp_upper(0, n)
        diff = abs(closed - bisected)
        print(f"  n={n:3d} closed_form={closed:.6f} cp_upper={bisected:.6f} diff={diff:.2e}")
        if diff > 1e-9:
            failures.append(f"cp_upper(0,{n}) disagrees with closed form by {diff:.2e}")

    print("\n== design-effect sensitivity (n=40 opportunities) ==")
    for rho, size in ((0.0, 1), (0.05, 4), (0.20, 4), (0.20, 8)):
        n_eff = max(1, int(round(40 / (1 + (size - 1) * rho))))
        print(f"  rho={rho:<5} m={size}: n_eff={n_eff:3d} CP_upper(0,n_eff)={cp_upper(0, n_eff):.4f}")

    print("\n== r2 (credited unknowns) vs r3 (corrected) on the sparse case ==")
    s, k, u = 5, 0, 45
    o = s + k + u
    buggy = 1 - cp_upper(k, o)
    correct = 1 - cp_upper(k + u, o)
    print(f"  r2 1-CP_upper(k,o)   = {buggy:.4f}  (admitted at tau=0.90 -> WRONG)")
    print(f"  r3 1-CP_upper(k+u,o) = {correct:.4f}  (rejected -> correct)")
    if buggy < TAU_CONF or correct >= TAU_CONF:
        failures.append("r3 regression case did not reproduce")

    if failures:
        print("\nFAILED:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nALL STATISTICS ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
