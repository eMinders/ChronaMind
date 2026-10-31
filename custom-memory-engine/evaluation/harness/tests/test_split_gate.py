"""B-09 / V2-D42: gate G-TS refuses tampered or unfrozen test splits before any read."""

import json
import shutil
from pathlib import Path

import pytest

from evaluation.harness import split_gate
from evaluation.harness.config import HarnessConfig
from evaluation.harness.split_gate import (
    ALL_SUITES,
    SplitGateError,
    authorize_confirmatory_run,
    check_integrity,
    frozen_coverage,
)
from evaluation.harness.tests.test_config_contract import config_data
from research.benchmarks.splits.freeze import FREEZE_LIST_PATH, SPLITS_DIR


def copy_splits(tmp_path: Path) -> tuple[Path, Path]:
    splits = tmp_path / "splits"
    shutil.copytree(SPLITS_DIR, splits, ignore=shutil.ignore_patterns("__pycache__"))
    return splits, splits / FREEZE_LIST_PATH.name


def test_repository_splits_pass_the_integrity_gate() -> None:
    report = check_integrity()
    assert report.integrity_passed, report.failure
    assert len(report.frozen_digests) == 6
    assert report.unfrozen_suites == ()


def test_every_declared_suite_is_frozen() -> None:
    assert len(ALL_SUITES) == 8
    assert frozen_coverage() == ()


def test_a_new_suite_without_a_frozen_split_fails_coverage() -> None:
    """Adding own_P (or any suite) without freezing it must fail the gate."""
    report = check_integrity(suites=(*ALL_SUITES, "P"))
    assert not report.integrity_passed and report.unfrozen_suites == ("P",)


def test_a_modified_split_file_fails_integrity(tmp_path: Path) -> None:
    splits, freeze_list = copy_splits(tmp_path)
    target = splits / "own_D.yaml"
    target.write_text(target.read_text(encoding="utf-8").replace("test", "tset", 1), encoding="utf-8")
    report = check_integrity(splits_dir=splits, freeze_list_path=freeze_list)
    assert not report.integrity_passed and report.failure is not None


def test_a_missing_split_file_fails_coverage_and_integrity(tmp_path: Path) -> None:
    splits, freeze_list = copy_splits(tmp_path)
    (splits / "own_I.yaml").unlink()
    report = check_integrity(splits_dir=splits, freeze_list_path=freeze_list)
    assert not report.integrity_passed
    assert "I" in report.unfrozen_suites


def test_confirmatory_run_is_refused_before_authorization_when_integrity_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing authorization (which reads the split) must never be reached."""
    splits, freeze_list = copy_splits(tmp_path)
    (splits / "own_D.yaml").write_text("tampered\n", encoding="utf-8")

    def must_not_run(**_: object) -> None:
        raise AssertionError("authorize_run_reached_despite_failed_integrity")

    monkeypatch.setattr(split_gate, "authorize_run", must_not_run)
    with pytest.raises(SplitGateError) as error:
        authorize_confirmatory_run(
            config=object(),  # type: ignore[arg-type]
            suite_id="D",
            repo_root=tmp_path,
            pilot_freeze_path=tmp_path / "missing.json",
            confirm_one_shot_test_run=True,
            splits_dir=splits,
            freeze_list_path=freeze_list,
        )
    assert error.value.code == "test_split_integrity_gate_failed"


def test_intact_splits_still_require_the_signed_freeze(tmp_path: Path) -> None:
    """Integrity alone never authorizes: J7/J8 (signed PilotFreeze) is still required."""
    config = HarnessConfig.model_validate_json(json.dumps(config_data()))
    with pytest.raises((ValueError, OSError)):
        authorize_confirmatory_run(
            config=config,
            suite_id="D",
            repo_root=Path(__file__).resolve().parents[3],
            pilot_freeze_path=tmp_path / "no-envelope.json",
            confirm_one_shot_test_run=True,
        )
