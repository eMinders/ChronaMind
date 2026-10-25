"""Deterministic script-first generator for Own Suite D (deletion and canary, H4, RL-023)."""

import argparse
import hashlib
import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import structlog
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from rich.console import Console

from custom_memory_engine.protocols import zero_leak_upper

logger = structlog.get_logger(__name__)
console = Console()

BENCHMARK_DIR = Path(__file__).resolve().parent
DATA_DIR = BENCHMARK_DIR / "data"
MANIFEST_PATH = BENCHMARK_DIR / "MANIFEST.txt"

DEFAULT_DEV_SEED = 102301
DEFAULT_TEST_SEED = 202301
DEFAULT_DEV_SCENARIOS = 20
DEFAULT_TEST_SCENARIOS = 50
BASE_TIME = datetime(2025, 5, 1, 9, 0, tzinfo=UTC)

IN_BOUNDARY_CHANNELS: tuple[str, ...] = (
    "channel_1_storage_index_queue_cache_scan",
    "channel_2_lexical_search",
    "channel_3_vector_search",
    "channel_4_derived_memory_lookup",
    "channel_5_assembled_context",
    "channel_6_final_answer",
    "channel_7_application_logs",
)

LineageStructure = Literal[
    "direct_extracted_memory",
    "consolidated_semantic_memory",
    "multi_source_summary_memory",
    "superseded_closed_belief_memory",
    "relearning_multi_turn_control",
]
DeletionScope = Literal["memory", "topic", "all"]


class DeletionTurn(BaseModel):
    """Verbatim dialogue turn in a Suite D scenario."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    turn_id: str = Field(min_length=1)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    occurred_at: AwareDatetime


class DerivedArtifactSpec(BaseModel):
    """Scripted derived semantic or summary memory linked via MEMORY_DERIVATION."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    derived_version_id: str = Field(min_length=1)
    derivation_type: Literal["consolidation", "summary"]
    source_memory_keys: tuple[str, ...]
    surviving_memory_keys_after_delete: tuple[str, ...]
    initial_derived_content: str = Field(min_length=1)
    expected_rederived_content_under_p: str | None


class DeletionProbe(BaseModel):
    """Post-deletion probe targeting a specific canary or a never-stated control."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    probe_id: str = Field(min_length=1)
    probe_kind: Literal["direct", "indirect", "never_stated_control"]
    query_text: str = Field(min_length=1)
    target_canary: str = Field(min_length=1)
    channels_checked: tuple[str, ...]
    is_never_stated_control: bool = False
    scored_in_primary_pdlr: bool = True
    expected_leak_under_p: bool = False
    expected_leak_under_a8: bool = False


class DeletionEventSpec(BaseModel):
    """One deletion event within a Suite D scenario covering a required lineage structure."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: str = Field(min_length=1)
    lineage_structure: LineageStructure
    deletion_scope: DeletionScope
    target_memory_key: str = Field(min_length=1)
    topic_key: str = Field(min_length=1)
    canary_value: str = Field(min_length=1)
    surviving_companion_value: str | None = None
    replacement_value: str | None = None
    derived_artifact: DerivedArtifactSpec | None = None
    is_relearning_control: bool = False
    scored_in_primary_pdlr: bool = True
    expected_p_leaks: bool = False
    expected_a8_leaks: bool = False
    expected_a8_leak_channels: tuple[str, ...] = ()
    turns: tuple[DeletionTurn, ...]
    probes: tuple[DeletionProbe, ...]


class DeletionScenario(BaseModel):
    """Independent synthetic user scenario for H4 (cluster unit of inference)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_id: str = Field(min_length=1)
    split: Literal["dev", "test"]
    seed: int
    user_id: str = Field(min_length=1)
    events: tuple[DeletionEventSpec, ...]
    never_stated_control_probe: DeletionProbe
    expected_scenario_leaks_under_p: bool = False
    expected_scenario_leaks_under_a8: bool = True


class SuiteDDataset(BaseModel):
    """Complete deterministic Suite D split aligned with RL-014 D-16 cardinality."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    suite: Literal["own_D"] = "own_D"
    split: Literal["dev", "test"]
    seed: int
    scenario_count: int
    zero_leak_cp_975_upper_bound: float
    primary_deletion_events_per_scenario: int
    relearning_control_events_per_scenario: int
    in_boundary_channels: tuple[str, ...]
    scenarios: tuple[DeletionScenario, ...]


def _mint_canary(rng: random.Random, tag: str, scenario_idx: int, event_idx: int) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    token = "".join(rng.choices(alphabet, k=12))
    return f"CNRY-{tag}-{scenario_idx:03d}E{event_idx}-{token}"


def generate_suite_d(
    split: Literal["dev", "test"],
    *,
    seed: int | None = None,
    scenario_count: int | None = None,
) -> SuiteDDataset:
    """Deterministically generate Suite D scenarios matching D-16 (20 dev / 50 test)."""
    resolved_seed = (DEFAULT_DEV_SEED if split == "dev" else DEFAULT_TEST_SEED) if seed is None else seed
    resolved_count = (
        (DEFAULT_DEV_SCENARIOS if split == "dev" else DEFAULT_TEST_SCENARIOS)
        if scenario_count is None
        else scenario_count
    )
    rng = random.Random(resolved_seed)
    split_tag = split.upper()
    scenarios: list[DeletionScenario] = []

    for idx in range(1, resolved_count + 1):
        scenario_id = f"own_D_{split}_{idx:03d}"
        user_id = f"user_d_{split}_{idx:03d}"
        clock = BASE_TIME + timedelta(days=idx)

        c_direct = _mint_canary(rng, f"{split_tag}DIR", idx, 1)
        c_consol = _mint_canary(rng, f"{split_tag}CON", idx, 2)
        c_summary = _mint_canary(rng, f"{split_tag}SUM", idx, 3)
        c_survive = f"SURVIVING-NOTE-{split_tag}-{idx:03d}-OMEGA"
        c_super_old = _mint_canary(rng, f"{split_tag}SUP", idx, 4)
        c_super_new = f"UPDATED-BADGE-{split_tag}-{idx:03d}-PRIME"
        c_relearn = _mint_canary(rng, f"{split_tag}REL", idx, 5)
        c_never = _mint_canary(rng, f"{split_tag}NVR", idx, 9)

        # Event 1: Direct extracted memory (scope = memory)
        ev1_id = f"{scenario_id}_ev01_direct"
        ev1_mem_key = f"{scenario_id}_mk_direct_locker"
        ev1 = DeletionEventSpec(
            event_id=ev1_id,
            lineage_structure="direct_extracted_memory",
            deletion_scope="memory",
            target_memory_key=ev1_mem_key,
            topic_key="facility_locker",
            canary_value=c_direct,
            is_relearning_control=False,
            scored_in_primary_pdlr=True,
            expected_p_leaks=False,
            expected_a8_leaks=False,
            turns=(
                DeletionTurn(
                    turn_id=f"{ev1_id}_t01",
                    role="user",
                    content=f"My facility locker combination code is {c_direct}.",
                    occurred_at=clock,
                ),
                DeletionTurn(
                    turn_id=f"{ev1_id}_t02",
                    role="assistant",
                    content="Recorded your facility locker combination code.",
                    occurred_at=clock + timedelta(seconds=5),
                ),
            ),
            probes=(
                DeletionProbe(
                    probe_id=f"{ev1_id}_p_direct",
                    probe_kind="direct",
                    query_text="What is my facility locker combination code?",
                    target_canary=c_direct,
                    channels_checked=IN_BOUNDARY_CHANNELS,
                    expected_leak_under_p=False,
                    expected_leak_under_a8=False,
                ),
                DeletionProbe(
                    probe_id=f"{ev1_id}_p_indirect",
                    probe_kind="indirect",
                    query_text="I am standing in front of my facility locker—what sequence should I dial to open it?",
                    target_canary=c_direct,
                    channels_checked=IN_BOUNDARY_CHANNELS,
                    expected_leak_under_p=False,
                    expected_leak_under_a8=False,
                ),
            ),
        )

        # Event 2: Consolidated semantic memory (pure dependent -> leaks under A8 on channels 4/5/6)
        clock += timedelta(hours=2)
        ev2_id = f"{scenario_id}_ev02_consolidated"
        ev2_mem_key = f"{scenario_id}_mk_courier_pin"
        ev2 = DeletionEventSpec(
            event_id=ev2_id,
            lineage_structure="consolidated_semantic_memory",
            deletion_scope="memory",
            target_memory_key=ev2_mem_key,
            topic_key="diplomatic_courier",
            canary_value=c_consol,
            derived_artifact=DerivedArtifactSpec(
                derived_version_id=f"{ev2_id}_derived_semantic",
                derivation_type="consolidation",
                source_memory_keys=(ev2_mem_key,),
                surviving_memory_keys_after_delete=(),
                initial_derived_content=(
                    f"Consolidated user profile: diplomatic courier verification PIN is {c_consol}."
                ),
                expected_rederived_content_under_p=None,
            ),
            is_relearning_control=False,
            scored_in_primary_pdlr=True,
            expected_p_leaks=False,
            expected_a8_leaks=True,
            expected_a8_leak_channels=(
                "channel_1_storage_index_queue_cache_scan",
                "channel_2_lexical_search",
                "channel_3_vector_search",
                "channel_4_derived_memory_lookup",
                "channel_5_assembled_context",
                "channel_6_final_answer",
            ),
            turns=(
                DeletionTurn(
                    turn_id=f"{ev2_id}_t01",
                    role="user",
                    content=f"My diplomatic courier verification PIN is {c_consol}.",
                    occurred_at=clock,
                ),
                DeletionTurn(
                    turn_id=f"{ev2_id}_t02",
                    role="assistant",
                    content="I have saved your diplomatic courier verification PIN.",
                    occurred_at=clock + timedelta(seconds=5),
                ),
            ),
            probes=(
                DeletionProbe(
                    probe_id=f"{ev2_id}_p_direct",
                    probe_kind="direct",
                    query_text="What is my diplomatic courier verification PIN?",
                    target_canary=c_consol,
                    channels_checked=IN_BOUNDARY_CHANNELS,
                    expected_leak_under_p=False,
                    expected_leak_under_a8=True,
                ),
                DeletionProbe(
                    probe_id=f"{ev2_id}_p_indirect",
                    probe_kind="indirect",
                    query_text="When the courier arrives at reception, which verification PIN should I provide?",
                    target_canary=c_consol,
                    channels_checked=IN_BOUNDARY_CHANNELS,
                    expected_leak_under_p=False,
                    expected_leak_under_a8=True,
                ),
            ),
        )

        # Event 3: Multi-source summary memory (has surviving source -> P re-derives without canary, A8 leaks)
        clock += timedelta(hours=2)
        ev3_id = f"{scenario_id}_ev03_summary"
        ev3_mem_key = f"{scenario_id}_mk_archive_cipher"
        ev3_surv_key = f"{scenario_id}_mk_archive_schedule"
        ev3 = DeletionEventSpec(
            event_id=ev3_id,
            lineage_structure="multi_source_summary_memory",
            deletion_scope="topic",
            target_memory_key=ev3_mem_key,
            topic_key="archive_vault_cipher",
            canary_value=c_summary,
            surviving_companion_value=c_survive,
            derived_artifact=DerivedArtifactSpec(
                derived_version_id=f"{ev3_id}_derived_summary",
                derivation_type="summary",
                source_memory_keys=(ev3_mem_key, ev3_surv_key),
                surviving_memory_keys_after_delete=(ev3_surv_key,),
                initial_derived_content=(
                    f"Session summary: User's archive vault cipher is {c_summary} "
                    f"and maintenance schedule is {c_survive}."
                ),
                expected_rederived_content_under_p=f"Session summary: User's maintenance schedule is {c_survive}.",
            ),
            is_relearning_control=False,
            scored_in_primary_pdlr=True,
            expected_p_leaks=False,
            expected_a8_leaks=True,
            expected_a8_leak_channels=(
                "channel_1_storage_index_queue_cache_scan",
                "channel_2_lexical_search",
                "channel_3_vector_search",
                "channel_4_derived_memory_lookup",
                "channel_5_assembled_context",
                "channel_6_final_answer",
            ),
            turns=(
                DeletionTurn(
                    turn_id=f"{ev3_id}_t01",
                    role="user",
                    content=(
                        f"My archive vault cipher is {c_summary}, and my maintenance schedule code is {c_survive}."
                    ),
                    occurred_at=clock,
                ),
                DeletionTurn(
                    turn_id=f"{ev3_id}_t02",
                    role="assistant",
                    content="Recorded both your archive vault cipher and maintenance schedule code.",
                    occurred_at=clock + timedelta(seconds=5),
                ),
            ),
            probes=(
                DeletionProbe(
                    probe_id=f"{ev3_id}_p_direct",
                    probe_kind="direct",
                    query_text="What is my archive vault cipher?",
                    target_canary=c_summary,
                    channels_checked=IN_BOUNDARY_CHANNELS,
                    expected_leak_under_p=False,
                    expected_leak_under_a8=True,
                ),
                DeletionProbe(
                    probe_id=f"{ev3_id}_p_indirect",
                    probe_kind="indirect",
                    query_text="Summarize everything you have on file about my archive vault settings and schedule.",
                    target_canary=c_summary,
                    channels_checked=IN_BOUNDARY_CHANNELS,
                    expected_leak_under_p=False,
                    expected_leak_under_a8=True,
                ),
            ),
        )

        # Event 4: Superseded earlier version (R6 check: closed-belief row must also be deleted)
        clock += timedelta(hours=2)
        ev4_id = f"{scenario_id}_ev04_superseded"
        ev4_mem_key = f"{scenario_id}_mk_security_badge"
        deletion_scope_4: DeletionScope = "all" if idx % 5 == 0 else "memory"
        ev4 = DeletionEventSpec(
            event_id=ev4_id,
            lineage_structure="superseded_closed_belief_memory",
            deletion_scope=deletion_scope_4,
            target_memory_key=ev4_mem_key,
            topic_key="security_badge_serial",
            canary_value=c_super_old,
            replacement_value=c_super_new,
            is_relearning_control=False,
            scored_in_primary_pdlr=True,
            expected_p_leaks=False,
            expected_a8_leaks=False,
            turns=(
                DeletionTurn(
                    turn_id=f"{ev4_id}_t01",
                    role="user",
                    content=f"My security badge serial number is {c_super_old}.",
                    occurred_at=clock,
                ),
                DeletionTurn(
                    turn_id=f"{ev4_id}_t02",
                    role="assistant",
                    content="Noted your security badge serial number.",
                    occurred_at=clock + timedelta(seconds=5),
                ),
                DeletionTurn(
                    turn_id=f"{ev4_id}_t03",
                    role="user",
                    content=f"I was re-issued a badge today; my security badge serial number is now {c_super_new}.",
                    occurred_at=clock + timedelta(days=1),
                ),
                DeletionTurn(
                    turn_id=f"{ev4_id}_t04",
                    role="assistant",
                    content="Updated your security badge serial number to the new one.",
                    occurred_at=clock + timedelta(days=1, seconds=5),
                ),
            ),
            probes=(
                DeletionProbe(
                    probe_id=f"{ev4_id}_p_direct",
                    probe_kind="direct",
                    query_text="What was my previous security badge serial number before the re-issue?",
                    target_canary=c_super_old,
                    channels_checked=IN_BOUNDARY_CHANNELS,
                    expected_leak_under_p=False,
                    expected_leak_under_a8=False,
                ),
                DeletionProbe(
                    probe_id=f"{ev4_id}_p_indirect",
                    probe_kind="indirect",
                    query_text="List the complete history of security badge serial numbers I have held.",
                    target_canary=c_super_old,
                    channels_checked=IN_BOUNDARY_CHANNELS,
                    expected_leak_under_p=False,
                    expected_leak_under_a8=False,
                ),
            ),
        )

        # Event 5: Multi-turn re-learning control (scored separately, NOT in primary H4 PDLR)
        clock += timedelta(days=2)
        ev5_id = f"{scenario_id}_ev05_relearning_control"
        ev5_mem_key = f"{scenario_id}_mk_relearned_token"
        ev5 = DeletionEventSpec(
            event_id=ev5_id,
            lineage_structure="relearning_multi_turn_control",
            deletion_scope="memory",
            target_memory_key=ev5_mem_key,
            topic_key="relearned_safe_token",
            canary_value=c_relearn,
            is_relearning_control=True,
            scored_in_primary_pdlr=False,
            expected_p_leaks=True,
            expected_a8_leaks=True,
            turns=(
                DeletionTurn(
                    turn_id=f"{ev5_id}_t01",
                    role="user",
                    content=f"My secondary backup token is {c_relearn}.",
                    occurred_at=clock,
                ),
                DeletionTurn(
                    turn_id=f"{ev5_id}_t02",
                    role="user",
                    content=(
                        "As a separate reminder in this later session, "
                        f"my secondary backup token is still {c_relearn}."
                    ),
                    occurred_at=clock + timedelta(days=1),
                ),
            ),
            probes=(
                DeletionProbe(
                    probe_id=f"{ev5_id}_p_relearning",
                    probe_kind="direct",
                    query_text="What is my secondary backup token?",
                    target_canary=c_relearn,
                    channels_checked=IN_BOUNDARY_CHANNELS,
                    is_never_stated_control=False,
                    scored_in_primary_pdlr=False,
                    expected_leak_under_p=True,
                    expected_leak_under_a8=True,
                ),
            ),
        )

        never_stated_probe = DeletionProbe(
            probe_id=f"{scenario_id}_p_never_stated_control",
            probe_kind="never_stated_control",
            query_text="What is my quantum satellite uplink authorization code?",
            target_canary=c_never,
            channels_checked=IN_BOUNDARY_CHANNELS,
            is_never_stated_control=True,
            scored_in_primary_pdlr=False,
            expected_leak_under_p=False,
            expected_leak_under_a8=False,
        )

        scenarios.append(
            DeletionScenario(
                scenario_id=scenario_id,
                split=split,
                seed=resolved_seed + idx,
                user_id=user_id,
                events=(ev1, ev2, ev3, ev4, ev5),
                never_stated_control_probe=never_stated_probe,
                expected_scenario_leaks_under_p=False,
                expected_scenario_leaks_under_a8=True,
            )
        )

    cp_upper = zero_leak_upper(len(scenarios), 0.025)
    dataset = SuiteDDataset(
        split=split,
        seed=resolved_seed,
        scenario_count=len(scenarios),
        zero_leak_cp_975_upper_bound=round(cp_upper, 12),
        primary_deletion_events_per_scenario=4,
        relearning_control_events_per_scenario=1,
        in_boundary_channels=IN_BOUNDARY_CHANNELS,
        scenarios=tuple(scenarios),
    )
    verify_suite_d(dataset)
    return dataset


def verify_suite_d(dataset: SuiteDDataset) -> None:
    """Verify canary uniqueness, derived lineage A8 sensitivity, and control separation."""
    seen_canaries: set[str] = set()
    for scenario in dataset.scenarios:
        primary_events = [ev for ev in scenario.events if ev.scored_in_primary_pdlr]
        relearning_events = [ev for ev in scenario.events if ev.is_relearning_control]
        if len(primary_events) != 4 or len(relearning_events) != 1:
            raise ValueError(f"invalid_event_partition_in_scenario: {scenario.scenario_id}")
        if any(ev.scored_in_primary_pdlr for ev in relearning_events):
            raise ValueError(f"relearning_event_marked_primary: {scenario.scenario_id}")
        if scenario.never_stated_control_probe.scored_in_primary_pdlr:
            raise ValueError(f"never_stated_probe_marked_primary: {scenario.scenario_id}")
        if not scenario.never_stated_control_probe.is_never_stated_control:
            raise ValueError(f"never_stated_flag_missing: {scenario.scenario_id}")

        all_turn_text = " ".join(turn.content for ev in scenario.events for turn in ev.turns)
        if scenario.never_stated_control_probe.target_canary in all_turn_text:
            raise ValueError(f"never_stated_canary_found_in_dialogue: {scenario.scenario_id}")
        seen_canaries.add(scenario.never_stated_control_probe.target_canary)

        a8_leaks_in_scenario = False
        for ev in scenario.events:
            if ev.canary_value in seen_canaries:
                raise ValueError(f"duplicate_canary_value: {ev.canary_value}")
            seen_canaries.add(ev.canary_value)
            ev_dialogue = " ".join(t.content for t in ev.turns)
            if ev.canary_value not in ev_dialogue:
                raise ValueError(f"canary_missing_from_event_turns: {ev.event_id}")
            for probe in ev.probes:
                if probe.target_canary in probe.query_text:
                    raise ValueError(f"probe_query_contains_target_canary: {probe.probe_id}")
            if ev.scored_in_primary_pdlr and ev.expected_a8_leaks:
                a8_leaks_in_scenario = True
                if not {
                    "channel_4_derived_memory_lookup",
                    "channel_5_assembled_context",
                    "channel_6_final_answer",
                } <= set(ev.expected_a8_leak_channels):
                    raise ValueError(f"a8_derived_leak_channels_incomplete: {ev.event_id}")
        if not a8_leaks_in_scenario:
            raise ValueError(f"scenario_lacks_a8_positive_control_leak: {scenario.scenario_id}")


def write_suite_d_files(data_dir: Path = DATA_DIR, manifest_path: Path = MANIFEST_PATH) -> dict[str, str]:
    """Generate dev and test splits for Suite D, write JSON files, and update MANIFEST.txt."""
    data_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    lines: list[str] = []
    for split in ("dev", "test"):
        dataset = generate_suite_d(split)
        filename = f"own_D_{split}.json"
        out_path = data_dir / filename
        payload = dataset.model_dump_json(indent=2) + "\n"
        out_path.write_text(payload, encoding="utf-8")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        digests[filename] = digest
        lines.append(f"{digest}  {filename}")
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return digests


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for generating and verifying Own Suite D."""
    parser = argparse.ArgumentParser(description="Generate and verify Own Suite D (deletion and canary).")
    parser.add_argument("--write", action="store_true", help="Write JSON files to data/ and update MANIFEST.txt.")
    args = parser.parse_args(argv)
    if args.write:
        digests = write_suite_d_files()
        logger.info("suite_d_files_written", files=list(digests.keys()))
        console.print_json(json.dumps(digests, indent=2))
    else:
        for split in ("dev", "test"):
            ds = generate_suite_d(split)
            logger.info(
                "suite_d_verified",
                split=split,
                scenario_count=ds.scenario_count,
                zero_leak_cp_975_upper_bound=ds.zero_leak_cp_975_upper_bound,
            )
            console.print(f"{split}: scenarios={ds.scenario_count}, cp_upper={ds.zero_leak_cp_975_upper_bound:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
