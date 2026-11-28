"""The streamed harness releases completed cases before loading the next one."""

import json
from pathlib import Path

import pytest

from evaluation.harness.config import HarnessConfig
from evaluation.harness.dummy import DummyAdapter, dummy_score
from evaluation.harness.runner import run_evaluation
from evaluation.harness.tests.test_config_contract import config_data
from evaluation.harness.tests.test_runner import fixture_case, run_fixture


def test_optional_case_release_is_local_before_next_case_and_preserves_campaign_manifest(repo: Path) -> None:
    adapters = []
    released = []

    class Tracking(DummyAdapter):
        def __init__(self, config):
            super().__init__(config)
            self.active = set()
            self.completed_cases = 0
            adapters.append(self)

        def reset_user(self, user_id):
            assert not self.active
            self.active.add(user_id)
            return super().reset_user(user_id)

        def release_user(self, user_id):
            self.active.remove(user_id)
            released.append(user_id)
            self.completed_cases += 1

        def adapter_metadata(self):
            return {"completed_cases": self.completed_cases}

        def close(self):
            assert not self.active

    def cases():
        for index in range(20):
            if adapters:
                assert not adapters[0].active
            yield fixture_case(cluster=f"case-{index}", user=f"user-{index}")

    split = repo / "dev.json"
    split.write_text('{"fixture":"public synthetic lifecycle input"}\n')
    destination = run_evaluation(
        config=HarnessConfig.model_validate_json(json.dumps(config_data())),
        suite_id="T",
        split="dev",
        split_path=split,
        case_loader=cases,
        adapter_factory=Tracking,
        scorer=dummy_score,
        run_id="lifecycle",
        output_root=repo / "runs",
        repo_root=repo,
        stream_cases=True,
    )
    assert len(adapters) == 1 and released == [f"user-{index}" for index in range(20)]
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["case_count"] == manifest["question_count"] == 20
    assert manifest["usage"]["cost_usd"] == "0"
    assert manifest["campaign_budget"]["unknown_operations"] == 0
    assert manifest["adapter_metadata"] == {"completed_cases": 20}


@pytest.mark.parametrize("fail_second_case", [False, True])
def test_every_fallback_adapter_closes_once_on_success_or_second_case_failure(
    repo: Path, fail_second_case: bool
) -> None:
    adapters = []
    observed_closes = []

    class Closing(DummyAdapter):
        """A resource-only adapter deliberately has no release_user capability."""

        def __init__(self, config):
            super().__init__(config)
            self.instance_number = len(adapters)
            self.closes = 0
            adapters.append(self)

        def close(self):
            self.closes += 1
            observed_closes.append((self.instance_number, (repo / "runs/first/dummy/T/manifest.json").exists()))

        def answer(self, user_id, question, as_of):
            if fail_second_case and self.instance_number == 1:
                raise RuntimeError("synthetic_second_case_failure")
            return super().answer(user_id, question, as_of)

    def loader():
        yield fixture_case(cluster="first", user="first")
        yield fixture_case(cluster="second", user="second")

    if fail_second_case:
        with pytest.raises(RuntimeError, match="synthetic_second_case_failure"):
            run_fixture(repo, factory=Closing, loader=loader)
    else:
        destination = run_fixture(repo, factory=Closing, loader=loader)
        manifest = json.loads((destination / "manifest.json").read_text())
        assert manifest["case_count"] == manifest["question_count"] == 2
        assert manifest["campaign_budget"]["unknown_operations"] == 0
        assert manifest["usage"]["cost_usd"] == "0"
    assert len(adapters) == 2
    assert [adapter.closes for adapter in adapters] == [1, 1]
    assert observed_closes == [(0, False), (1, False)], "every owned instance must close before success publication"
    if fail_second_case:
        assert not (repo / "runs/first/dummy/T/manifest.json").exists()
