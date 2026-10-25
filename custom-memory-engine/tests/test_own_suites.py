"""Reproducibility, schema, and control verification tests for Own Suites G, I, D, and T (RL-022..024)."""

import hashlib
from pathlib import Path

from research.benchmarks.own_D.generator import (
    MANIFEST_PATH as MANIFEST_D,
)
from research.benchmarks.own_D.generator import (
    generate_suite_d,
    verify_suite_d,
)
from research.benchmarks.own_G.generator import (
    DEV_INJECTION_TEMPLATES,
    TEST_HELD_OUT_INJECTION_TEMPLATES,
    generate_suite_g,
    verify_suite_g,
)
from research.benchmarks.own_G.generator import (
    MANIFEST_PATH as MANIFEST_G,
)
from research.benchmarks.own_I.generator import (
    MANIFEST_PATH as MANIFEST_I,
)
from research.benchmarks.own_I.generator import (
    generate_suite_i,
    verify_suite_i,
)
from research.benchmarks.own_T.generator import (
    MANIFEST_PATH as MANIFEST_T,
)
from research.benchmarks.own_T.generator import (
    generate_suite_t,
    verify_suite_t,
)


def _read_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split()
            entries[name] = digest
    return entries


def test_suite_g_determinism_and_held_out_injection_families() -> None:
    dev_a = generate_suite_g("dev")
    dev_b = generate_suite_g("dev")
    test_a = generate_suite_g("test")
    assert dev_a.model_dump_json() == dev_b.model_dump_json()
    assert dev_a.scenario_count == 20
    assert test_a.scenario_count == 50
    assert dev_a.total_episodes == 20 * 12
    assert test_a.total_episodes == 50 * 12

    dev_fams = set(dev_a.injection_families) - {"unconfirmed_assistant_speculation"}
    test_fams = set(test_a.injection_families) - {"unconfirmed_assistant_speculation"}
    assert dev_fams == {f for f, _ in DEV_INJECTION_TEMPLATES}
    assert test_fams == {f for f, _ in TEST_HELD_OUT_INJECTION_TEMPLATES}
    assert dev_fams.isdisjoint(test_fams)
    verify_suite_g(dev_a)
    verify_suite_g(test_a)

    manifest = _read_manifest(MANIFEST_G)
    assert hashlib.sha256((dev_a.model_dump_json(indent=2) + "\n").encode()).hexdigest() == manifest["own_G_dev.json"]
    assert (
        hashlib.sha256((test_a.model_dump_json(indent=2) + "\n").encode()).hexdigest() == manifest["own_G_test.json"]
    )


def test_suite_i_determinism_and_zero_exposure_contract() -> None:
    dev_a = generate_suite_i("dev")
    dev_b = generate_suite_i("dev")
    test_a = generate_suite_i("test")
    assert dev_a.model_dump_json() == dev_b.model_dump_json()
    assert dev_a.scenario_count == 20
    assert test_a.scenario_count == 50
    assert dev_a.expected_total_exposures == 0
    assert test_a.expected_total_exposures == 0
    verify_suite_i(dev_a)
    verify_suite_i(test_a)

    manifest = _read_manifest(MANIFEST_I)
    assert hashlib.sha256((dev_a.model_dump_json(indent=2) + "\n").encode()).hexdigest() == manifest["own_I_dev.json"]
    assert (
        hashlib.sha256((test_a.model_dump_json(indent=2) + "\n").encode()).hexdigest() == manifest["own_I_test.json"]
    )


def test_suite_d_determinism_cardinality_and_controls() -> None:
    dev_a = generate_suite_d("dev")
    dev_b = generate_suite_d("dev")
    test_a = generate_suite_d("test")
    assert dev_a.model_dump_json() == dev_b.model_dump_json()
    assert dev_a.scenario_count == 20
    assert test_a.scenario_count == 50
    assert round(dev_a.zero_leak_cp_975_upper_bound, 4) == 0.1684
    assert round(test_a.zero_leak_cp_975_upper_bound, 4) == 0.0711
    assert all(s.expected_scenario_leaks_under_p is False for s in test_a.scenarios)
    assert all(s.expected_scenario_leaks_under_a8 is True for s in test_a.scenarios)
    verify_suite_d(dev_a)
    verify_suite_d(test_a)

    manifest = _read_manifest(MANIFEST_D)
    assert hashlib.sha256((dev_a.model_dump_json(indent=2) + "\n").encode()).hexdigest() == manifest["own_D_dev.json"]
    assert (
        hashlib.sha256((test_a.model_dump_json(indent=2) + "\n").encode()).hexdigest() == manifest["own_D_test.json"]
    )


def test_suite_t_determinism_stratum_balance_and_audit_grid() -> None:
    dev_a = generate_suite_t("dev")
    dev_b = generate_suite_t("dev")
    test_a = generate_suite_t("test")
    assert dev_a.model_dump_json() == dev_b.model_dump_json()
    assert dev_a.scenario_count == 20
    assert test_a.scenario_count == 50
    assert dev_a.history_dependent_questions == 60
    assert dev_a.present_state_questions == 60
    assert test_a.history_dependent_questions == 150
    assert test_a.present_state_questions == 150
    verify_suite_t(dev_a)
    verify_suite_t(test_a)

    manifest = _read_manifest(MANIFEST_T)
    assert hashlib.sha256((dev_a.model_dump_json(indent=2) + "\n").encode()).hexdigest() == manifest["own_T_dev.json"]
    assert (
        hashlib.sha256((test_a.model_dump_json(indent=2) + "\n").encode()).hexdigest() == manifest["own_T_test.json"]
    )
