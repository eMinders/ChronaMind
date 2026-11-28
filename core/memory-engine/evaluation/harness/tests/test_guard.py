"""Denied test attempts must fail before accessing split contents or output paths."""

import json
from pathlib import Path

import pytest

from evaluation.harness.config import HarnessConfig
from evaluation.harness.guard import authorize_run
from evaluation.harness.tests.test_config_contract import config_data


@pytest.mark.parametrize("confirm", [False, True])
def test_missing_freeze_denies_before_split_access(tmp_path: Path, confirm: bool) -> None:
    config = HarnessConfig.model_validate_json(json.dumps(config_data()))
    with pytest.raises(ValueError, match="test_run_requires_confirmation_and_freeze"):
        authorize_run(
            config=config,
            split="test",
            suite_id="T",
            split_path=tmp_path / "does-not-exist",
            repo_root=tmp_path,
            confirm_one_shot_test_run=confirm,
            pilot_freeze_path=None,
        )
    assert list(tmp_path.iterdir()) == []
