"""#46 acceptance on PostgreSQL: Suite D under P is clean on all seven channels; A8 is detected.

Uses a deterministic bag-of-words embedder so CI does not need the model assets.
The recorded frozen-model run is ``reports/deletion/suite_d_dev_{P,A8}.json``.
"""

import hashlib
import math
import os
from collections.abc import Sequence

import pytest

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from evaluation.harness.deletion_suite import run_suite

pytestmark = pytest.mark.integration

SCENARIOS = 3


def bag_of_words(texts: Sequence[str], *, query: bool) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        vector = [0.0] * 384
        for token in text.lower().split():
            vector[int(hashlib.sha256(token.encode()).hexdigest(), 16) % 384] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        vectors.append([x / norm for x in vector])
    return vectors


@pytest.fixture
def database_url() -> str:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database with vector and btree_gist")
    return DatabaseConfig.from_environment().database_url.get_secret_value()


async def test_p_is_fully_measured_and_clean(database_url: str) -> None:
    report = await run_suite(database_url, ablation="P", embed=bag_of_words, limit=SCENARIOS)
    data = report.to_json()
    assert data["scenario_count"] == SCENARIOS
    assert data["incomplete_measurements"] == 0, "every channel must be measured and complete"
    assert data["leaking_scenarios"] == 0
    assert set(data["primary_channel_totals"].values()) == {0}  # type: ignore[union-attr]
    assert data["never_stated_false_positives"] == 0


async def test_a8_positive_control_leaks_exactly_where_expected(database_url: str) -> None:
    report = await run_suite(database_url, ablation="A8", embed=bag_of_words, limit=SCENARIOS)
    assert report.leaking_scenarios == SCENARIOS
    for scenario in report.scenarios:
        for event in scenario.events:
            if event.relearning_control:
                continue
            leaked = {channel for channel, count in event.channel_residuals.items() if count}
            if event.expected_a8_leak_channels:
                assert "channel_4_derived_memory_lookup" in leaked, event.event_id
            else:
                assert not leaked, event.event_id


async def test_report_carries_the_one_sided_cp_bound(database_url: str) -> None:
    report = await run_suite(database_url, ablation="P", embed=bag_of_words, limit=SCENARIOS)
    bound = report.to_json()["cp_upper_one_sided_97_5"]
    assert isinstance(bound, float)
    assert abs(bound - (1 - 0.025 ** (1 / SCENARIOS))) < 1e-9  # zero-leak closed form
