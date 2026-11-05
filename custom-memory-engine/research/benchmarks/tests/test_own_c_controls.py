"""Per-family controls and scoring instruments for Own Suite C (E-022 §10.6–9).

Positive controls: a system that answers trivially must fail the family. The near-miss
control fails whenever matching is similarity-only; the `procedures` channel assertion
fails whenever the recipe is stored as a claim.
"""

import pytest

from custom_memory_engine.engine.channels import channel_of
from custom_memory_engine.protocols import zero_leak_upper
from research.benchmarks.own_C.generator import (
    CodingScenario,
    SuiteCDataset,
    generate_suite_c,
    library_of,
    offer_by_preconditions,
    offer_by_similarity,
    score_abstention_probe,
    score_offer_probe,
    score_qa_probe,
    verify_suite_c,
)


@pytest.fixture(scope="module")
def dev_suite() -> SuiteCDataset:
    return generate_suite_c("dev")


@pytest.fixture(scope="module")
def test_suite() -> SuiteCDataset:
    return generate_suite_c("test")


def _scenarios_by_family(dataset: SuiteCDataset, family: str) -> list[CodingScenario]:
    return [scenario for scenario in dataset.scenarios if scenario.family == family]


def test_prior_attempt_family_recalls_the_failed_fix(dev_suite: SuiteCDataset) -> None:
    scenarios = _scenarios_by_family(dev_suite, "prior_attempt_recall")
    assert len(scenarios) == 4
    for scenario in scenarios:
        (probe,) = scenario.probes
        correct = score_qa_probe(probe, probe.gold_answer or "")
        assert correct == 1.0
        assert score_qa_probe(probe, "I do not know.") == 0.0
        assert score_qa_probe(probe, "We touched the vendor lockfile and it fixed itself.") == 0.0


def test_repo_fact_family_requires_the_recorded_invariant(dev_suite: SuiteCDataset) -> None:
    scenarios = _scenarios_by_family(dev_suite, "repo_fact_recall")
    assert len(scenarios) == 4
    for scenario in scenarios:
        (probe,) = scenario.probes
        assert probe.gold_evidence_episode_ids
        assert score_qa_probe(probe, probe.gold_answer or "") == 1.0
        assert score_qa_probe(probe, "I do not know.") == 0.0


def test_abstention_control_rejects_trivially_answering_systems(dev_suite: SuiteCDataset) -> None:
    scenarios = _scenarios_by_family(dev_suite, "abstention_control")
    assert len(scenarios) == 4
    for scenario in scenarios:
        (probe,) = scenario.probes
        assert probe.gold_answer is None and not probe.gold_evidence_episode_ids
        # Positive requirement: "not known" is scoreable; "remembered wrongly" is not tolerated.
        assert score_abstention_probe(probe, "No information available in the recorded episodes.") == 1.0
        distractor_echo = scenario.episodes[0].content
        assert score_abstention_probe(probe, distractor_echo) == 0.0


def test_procedure_reuse_offers_the_stored_recipe_on_identical_preconditions(
    dev_suite: SuiteCDataset, test_suite: SuiteCDataset
) -> None:
    for dataset in (dev_suite, test_suite):
        scenarios = _scenarios_by_family(dataset, "procedure_reuse")
        assert len(scenarios) == (4 if dataset.split == "dev" else 10)
        for scenario in scenarios:
            (probe,) = scenario.probes
            library = library_of(scenario)
            assert score_offer_probe(probe, library) == 1.0
            assert offer_by_preconditions(probe, library) is True


def test_procedure_recipe_renders_in_the_procedures_channel_and_not_as_a_claim(
    dev_suite: SuiteCDataset, test_suite: SuiteCDataset
) -> None:
    """Suite C §5 family table: the recipe is a procedure; storing it as a claim must fail."""
    for dataset in (dev_suite, test_suite):
        for scenario in _scenarios_by_family(dataset, "procedure_reuse"):
            (recipe,) = (e for e in scenario.episodes if e.kind == "procedure")
            stored = channel_of(recipe.kind, recipe.lifecycle, recipe.verification, False, False)
            assert stored == "procedures", recipe.episode_id
            claim_mutation = channel_of("claim", recipe.lifecycle, recipe.verification, False, False)
            assert claim_mutation == "facts", recipe.episode_id


def test_near_miss_control_refuses_on_preconditions_and_beats_similarity_matching(
    dev_suite: SuiteCDataset, test_suite: SuiteCDataset
) -> None:
    """The similarity strawman offers on every near-miss probe; the suite matcher refuses.

    If the suite instrument were replaced with similarity-only matching (this test's
    `offer_by_preconditions` rows flipped to `offer_by_similarity`), every near-miss label
    would invert and this test would fail.
    """
    for dataset in (dev_suite, test_suite):
        scenarios = _scenarios_by_family(dataset, "near_miss_control")
        assert len(scenarios) == (4 if dataset.split == "dev" else 10)
        for scenario in scenarios:
            (probe,) = scenario.probes
            assert probe.is_near_miss_control is True
            library = library_of(scenario)
            assert offer_by_preconditions(probe, library) is False
            assert score_offer_probe(probe, library) == 1.0
            assert offer_by_similarity(probe, library) is True


def test_zero_event_strata_report_counts_with_cp_975_upper_bounds(
    dev_suite: SuiteCDataset, test_suite: SuiteCDataset
) -> None:
    """Reporting convention (E-022 §3.5, STATISTICS.md §4.2): counts plus one-sided 97.5% CP."""
    for dataset in (dev_suite, test_suite):
        assert dataset.expected_false_offers == 0
        assert dataset.near_miss_control_probes > 0
        assert dataset.false_offer_cp_975_upper_bound == pytest.approx(
            zero_leak_upper(dataset.near_miss_control_probes), abs=1e-12
        )
    assert test_suite.false_offer_cp_975_upper_bound < dev_suite.false_offer_cp_975_upper_bound


def test_suite_c_scoring_labels_have_no_cross_tenant_scope_interference(test_suite: SuiteCDataset) -> None:
    """Repo scopes stay per-scenario (E-011); no scenario's canary appears in another's text."""
    scenarios = test_suite.scenarios
    for scenario in scenarios:
        serialized = scenario.model_dump_json()
        for other in scenarios:
            if other.scenario_id == scenario.scenario_id:
                continue
            assert other.canary not in serialized


def test_verify_rejects_a_tampered_answerable_probe(dev_suite: SuiteCDataset) -> None:
    """Genuine control: breaking the gold label must make the generator's verifier fail."""
    scenario = dev_suite.scenarios[0]
    assert scenario.family == "prior_attempt_recall"
    (probe,) = scenario.probes
    tampered_scenario = scenario.model_copy(update={"probes": (probe.model_copy(update={"gold_answer": None}),)})
    with pytest.raises(ValueError, match="answerable_probe_requires_gold"):
        verify_suite_c(dev_suite.model_copy(update={"scenarios": (tampered_scenario,)}))


def test_verify_rejects_a_near_miss_probe_that_keeps_the_sibling_advantage(
    dev_suite: SuiteCDataset,
) -> None:
    """Genuine control: a near-miss probe with matching bindings must fail verification."""
    scenarios = _scenarios_by_family(dev_suite, "near_miss_control")
    assert scenarios
    scenario = scenarios[0]
    (probe,) = scenario.probes
    assert scenario.procedure is not None
    tampered_scenario = scenario.model_copy(
        update={"probes": (probe.model_copy(update={"observed_bindings": scenario.procedure.preconditions}),)}
    )
    with pytest.raises(ValueError, match="near_miss_probe_bindings_match_storage"):
        verify_suite_c(dev_suite.model_copy(update={"scenarios": (tampered_scenario,)}))
