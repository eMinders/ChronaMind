"""Gate G-TS: test-split integrity before any confirmatory run (V2-D42, resolves B-09).

``PLAN/CONTRIBUTING.md`` §2 allowed a confirmatory test split to run once, after gates
J5 (``RL-026``), J7 (``RL-094``) and J8 (``RL-100``). The V2 transition retired those
task ids without saying which V2 gate carries them, while ``ROADMAP_V2.md`` G5 consumes
the ``own_P`` *test* split in W5 — earlier than W6. V2-D42 keeps J5/J7/J8 in force as
gate **G-TS**, a precondition of every confirmatory run, first needed at G5:

=====  ==========================================================  ======================
part   requirement                                                 enforced by
=====  ==========================================================  ======================
J5     every frozen split regenerates byte-identically and every    ``verify_frozen_splits``
       digest is recorded in ``FREEZE_LIST.md``                     (CI, here)
cover  every ``SuiteId`` resolves to a frozen split, so a new V2    ``frozen_coverage``
       suite cannot acquire a test split without being frozen       (CI, here)
J7/J8  a ``PilotFreeze`` envelope: three distinct human sign-offs,  ``guard.authorize_run``
       committed at a git tag, binding config, freeze list and
       split digests
once   a durable one-shot claim before dispatch                     ``guard.claim_test_run``
=====  ==========================================================  ======================

:func:`authorize_confirmatory_run` runs the integrity parts first and only then the
existing authorization, so a tampered or unfrozen split is refused before any
test-split byte is read.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

from evaluation.harness.config import HarnessConfig
from evaluation.harness.guard import Authorization, authorize_run
from evaluation.harness.models import SuiteId
from research.benchmarks.splits.freeze import (
    FREEZE_LIST_PATH,
    SPLITS_DIR,
    split_path_for_suite,
    verify_frozen_splits,
)

ALL_SUITES: tuple[SuiteId, ...] = get_args(SuiteId.__value__)


class SplitGateError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class GateReport:
    integrity_passed: bool
    frozen_digests: dict[str, str]
    unfrozen_suites: tuple[str, ...]
    failure: str | None


def frozen_coverage(suites: Iterable[str] = ALL_SUITES, splits_dir: Path = SPLITS_DIR) -> tuple[str, ...]:
    """Return suites that have no frozen split file (must be empty)."""
    missing: list[str] = []
    for suite in suites:
        try:
            path = split_path_for_suite(suite, splits_dir)  # type: ignore[arg-type]
        except ValueError:
            missing.append(suite)
            continue
        if not path.is_file():
            missing.append(suite)
    return tuple(missing)


def check_integrity(
    *,
    suites: Iterable[str] = ALL_SUITES,
    splits_dir: Path = SPLITS_DIR,
    freeze_list_path: Path = FREEZE_LIST_PATH,
) -> GateReport:
    """Run the CI-enforceable parts of G-TS (J5 + coverage). Never raises on a failed check."""
    unfrozen = frozen_coverage(suites, splits_dir)
    try:
        digests = verify_frozen_splits(splits_dir, freeze_list_path)
    except (OSError, ValueError) as error:
        return GateReport(False, {}, unfrozen, type(error).__name__ + ":" + str(error).split(":")[0])
    if unfrozen:
        return GateReport(False, digests, unfrozen, "suite_without_frozen_split")
    return GateReport(True, digests, (), None)


def authorize_confirmatory_run(
    *,
    config: HarnessConfig,
    suite_id: SuiteId,
    repo_root: Path,
    pilot_freeze_path: Path,
    confirm_one_shot_test_run: bool,
    splits_dir: Path = SPLITS_DIR,
    freeze_list_path: Path = FREEZE_LIST_PATH,
) -> Authorization:
    """G-TS then J7/J8: integrity is proven before the existing test authorization runs."""
    report = check_integrity(suites=(suite_id,), splits_dir=splits_dir, freeze_list_path=freeze_list_path)
    if not report.integrity_passed:
        raise SplitGateError("test_split_integrity_gate_failed")
    return authorize_run(
        config=config,
        split="test",
        suite_id=suite_id,
        split_path=split_path_for_suite(suite_id, splits_dir),
        repo_root=repo_root,
        confirm_one_shot_test_run=confirm_one_shot_test_run,
        pilot_freeze_path=pilot_freeze_path,
    )


__all__ = [
    "ALL_SUITES",
    "GateReport",
    "SplitGateError",
    "authorize_confirmatory_run",
    "check_integrity",
    "frozen_coverage",
]
