"""Deterministic script-first generator for Own Suite T (temporal-change, H1, RL-024)."""

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

logger = structlog.get_logger(__name__)
console = Console()

BENCHMARK_DIR = Path(__file__).resolve().parent
DATA_DIR = BENCHMARK_DIR / "data"
MANIFEST_PATH = BENCHMARK_DIR / "MANIFEST.txt"

DEFAULT_DEV_SEED = 102401
DEFAULT_TEST_SEED = 202401
DEFAULT_DEV_SCENARIOS = 20
DEFAULT_TEST_SCENARIOS = 50

QuestionMode = Literal["current", "as_of", "what_changed", "before_change", "stable"]
Stratum = Literal["history-dependent", "present-state"]
ValidFromBasis = Literal["explicit", "inferred"]

TEMPORAL_DOMAINS: tuple[dict[str, object], ...] = (
    {
        "domain_id": "employment_role",
        "primary_predicate": "job_title",
        "primary_values": (
            "Junior Data Analyst",
            "Staff Machine Learning Engineer",
            "Principal Systems Architect",
            "Lead Research Scientist",
            "Director of Platform Engineering",
            "Senior Security Strategist",
        ),
        "stable_predicate": "alma_mater_university",
        "stable_values": ("ETH Zurich", "TU Delft", "EPFL", "University of Toronto", "Imperial College London"),
        "secondary_predicate": "commute_mode",
        "secondary_values": ("bicycle", "commuter rail", "electric tram", "ferry"),
        "multi_predicate": "active_committee_membership",
        "multi_values": ("Safety Review Board", "Architecture Guild", "Open Source Council", "Ethics Panel"),
        "q_current": "What is my current job title?",
        "q_as_of_current": "As of {date_str}, what was my job title?",
        "q_as_of_past": "Back on {date_str}, what was my job title?",
        "q_before_change": "What was my job title immediately before my most recent promotion or role change?",
        "q_what_changed": "How did my job title change and on what effective date did the new role start?",
        "q_stable": "Which university is my alma mater?",
    },
    {
        "domain_id": "residential_location",
        "primary_predicate": "home_district",
        "primary_values": (
            "Kreis 4",
            "Seefeld",
            "Oerlikon",
            "Wiedikon",
            "Enge",
            "Altstetten",
        ),
        "stable_predicate": "blood_type_group",
        "stable_values": ("O-positive", "A-negative", "B-positive", "AB-positive", "O-negative"),
        "secondary_predicate": "heating_provider",
        "secondary_values": ("district steam", "geothermal heat pump", "solar thermal", "hydronic pellet"),
        "multi_predicate": "subscribed_periodical",
        "multi_values": ("Alpine Science Monthly", "Helvetia Law Review", "Nordic Rail Digest", "Quantum Letters"),
        "q_current": "Which district do I currently live in?",
        "q_as_of_current": "As of {date_str}, which district was I living in?",
        "q_as_of_past": "On {date_str}, which district did I live in?",
        "q_before_change": "Which district did I live in right before moving to my current district?",
        "q_what_changed": "What change occurred in my residential district and on what effective date did I move?",
        "q_stable": "What is my recorded blood type group?",
    },
    {
        "domain_id": "clinical_training_assignment",
        "primary_predicate": "assigned_hospital_ward",
        "primary_values": (
            "Cardiology Ward C",
            "Neurology Unit B",
            "Oncology Pavilion A",
            "Pediatrics Wing D",
            "Trauma Center E",
            "Radiology Suite F",
        ),
        "stable_predicate": "medical_license_canton",
        "stable_values": (
            "Canton of Bern",
            "Canton of Geneva",
            "Canton of Vaud",
            "Canton of Basel-Stadt",
            "Canton of Zurich",
        ),
        "secondary_predicate": "shift_schedule_pattern",
        "secondary_values": ("day-rotation", "night-float", "weekend-block", "split-swing"),
        "multi_predicate": "certified_imaging_modality",
        "multi_values": ("3T MRI", "PET-CT", "Doppler Ultrasound", "Fluoroscopy"),
        "q_current": "Which hospital ward am I currently assigned to?",
        "q_as_of_current": "As of {date_str}, which hospital ward was I assigned to?",
        "q_as_of_past": "On {date_str}, which hospital ward was I assigned to?",
        "q_before_change": "Which hospital ward was I assigned to immediately prior to my current ward?",
        "q_what_changed": "How did my assigned hospital ward change and what was the effective date of the transfer?",
        "q_stable": "In which canton is my medical license registered?",
    },
)


class TemporalTurn(BaseModel):
    """Verbatim turn with session and source timestamps in Suite T."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    turn_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    occurred_at: AwareDatetime


class ScriptedVersionRecord(BaseModel):
    """Expected bi-temporal version state at reference time rho (after E1..E5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version_id: str = Field(min_length=1)
    memory_key: str = Field(min_length=1)
    predicate_key: str = Field(min_length=1)
    single_valued: bool
    value: str = Field(min_length=1)
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None
    valid_from_basis: ValidFromBasis
    recorded_from: AwareDatetime
    recorded_to: AwareDatetime | None
    is_current_belief_at_rho: bool
    is_currently_valid_at_rho: bool


class TemporalQuestion(BaseModel):
    """Conversational question in Suite T with mechanically assigned stratum and gold answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    question_id: str = Field(min_length=1)
    mode: QuestionMode
    stratum: Stratum
    query_text: str = Field(min_length=1)
    as_of_time: AwareDatetime | None
    reference_time: AwareDatetime
    gold_value: str = Field(min_length=1)
    gold_valid_from: AwareDatetime
    gold_valid_to: AwareDatetime | None
    requires_non_current_valid_version: bool
    expected_correct_under_p: bool = True
    expected_correct_under_a2: bool
    naive_latest_heuristic_correct: bool
    naive_previous_heuristic_correct: bool


class AuditGridProbe(BaseModel):
    """Deterministic API-level audit_as_of(user_id, valid_time, belief_time) check."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    audit_probe_id: str = Field(min_length=1)
    valid_time: AwareDatetime
    belief_time: AwareDatetime
    expected_version_ids: tuple[str, ...]
    expected_values: tuple[str, ...]
    rationale: str = Field(min_length=1)


class TemporalScenario(BaseModel):
    """Independent synthetic person's multi-session history in Suite T (cluster unit for H1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_id: str = Field(min_length=1)
    split: Literal["dev", "test"]
    seed: int
    user_id: str = Field(min_length=1)
    domain_id: str = Field(min_length=1)
    reference_time: AwareDatetime
    turns: tuple[TemporalTurn, ...]
    scripted_versions: tuple[ScriptedVersionRecord, ...]
    questions: tuple[TemporalQuestion, ...]
    audit_grid: tuple[AuditGridProbe, ...]


class SuiteTDataset(BaseModel):
    """Complete deterministic Suite T split with 1:1 stratum balance."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    suite: Literal["own_T"] = "own_T"
    split: Literal["dev", "test"]
    seed: int
    scenario_count: int
    total_questions: int
    history_dependent_questions: int
    present_state_questions: int
    total_audit_probes: int
    scenarios: tuple[TemporalScenario, ...]


def assign_stratum(mode: QuestionMode, *, requires_non_current_valid_version: bool) -> Stratum:
    """Mechanical stratum assignment rule (Graduation_Project_Proposal.md §10.2)."""
    if requires_non_current_valid_version or mode in ("before_change", "what_changed"):
        return "history-dependent"
    return "present-state"


def generate_suite_t(
    split: Literal["dev", "test"],
    *,
    seed: int | None = None,
    scenario_count: int | None = None,
) -> SuiteTDataset:
    """Deterministically generate Suite T scenarios covering E1-E3, R1-R8, and 5 question modes."""
    resolved_seed = (DEFAULT_DEV_SEED if split == "dev" else DEFAULT_TEST_SEED) if seed is None else seed
    resolved_count = (
        (DEFAULT_DEV_SCENARIOS if split == "dev" else DEFAULT_TEST_SCENARIOS)
        if scenario_count is None
        else scenario_count
    )
    rng = random.Random(resolved_seed)
    scenarios: list[TemporalScenario] = []

    for idx in range(1, resolved_count + 1):
        scenario_id = f"own_T_{split}_{idx:03d}"
        user_id = f"user_t_{split}_{idx:03d}"
        domain = TEMPORAL_DOMAINS[(idx - 1) % len(TEMPORAL_DOMAINS)]
        domain_id = str(domain["domain_id"])
        primary_pred = str(domain["primary_predicate"])
        stable_pred = str(domain["stable_predicate"])
        secondary_pred = str(domain["secondary_predicate"])
        multi_pred = str(domain["multi_predicate"])

        p_vals = list(domain["primary_values"])  # type: ignore[arg-type]
        s_vals = list(domain["stable_values"])  # type: ignore[arg-type]
        sec_vals = list(domain["secondary_values"])  # type: ignore[arg-type]
        m_vals = list(domain["multi_values"])  # type: ignore[arg-type]
        rng.shuffle(p_vals)
        rng.shuffle(s_vals)
        rng.shuffle(sec_vals)
        rng.shuffle(m_vals)

        v1_old = str(p_vals[0])
        v2_new = str(p_vals[1])
        v_stable = str(s_vals[0])
        v_sec_mistake = str(sec_vals[0])
        v_sec_corrected = str(sec_vals[1])
        mv_keep = str(m_vals[0])
        mv_end = str(m_vals[1])

        # Timeline per scenario (shifted by idx days so every scenario has distinct dates)
        day_shift = timedelta(days=(idx - 1) * 2)
        t1 = datetime(2025, 1, 10, 9, 0, tzinfo=UTC) + day_shift
        t_retro = datetime(2025, 2, 1, 0, 0, tzinfo=UTC) + day_shift
        t2 = datetime(2025, 2, 15, 14, 0, tzinfo=UTC) + day_shift
        t3 = datetime(2025, 3, 1, 11, 0, tzinfo=UTC) + day_shift
        t4 = datetime(2025, 4, 10, 16, 0, tzinfo=UTC) + day_shift
        rho = datetime(2025, 5, 1, 12, 0, tzinfo=UTC) + day_shift

        t_query_past = t1 + timedelta(days=10)  # Strictly inside [t1, t_retro)
        t_query_between = t_retro + timedelta(days=5)  # Strictly inside [t_retro, t2): tests E2 vs E3!
        t_query_current = t3 + timedelta(days=15)  # Strictly inside [t_retro, rho)

        retro_date_str = t_retro.strftime("%Y-%m-%d")
        past_date_str = t_query_past.strftime("%Y-%m-%d")
        curr_date_str = t_query_current.strftime("%Y-%m-%d")

        turns = (
            # Session 1 at t1: E1 initial primary fact, stable fact, same-day slip + R2 correction, multi-valued facts
            TemporalTurn(
                turn_id=f"{scenario_id}_s01_t01",
                session_id=f"{scenario_id}_s01",
                role="user",
                content=(
                    f"Starting {t1.strftime('%Y-%m-%d')}, my {primary_pred} is {v1_old}, "
                    f"and my {stable_pred} is {v_stable}."
                ),
                occurred_at=t1,
            ),
            TemporalTurn(
                turn_id=f"{scenario_id}_s01_t02",
                session_id=f"{scenario_id}_s01",
                role="user",
                content=(
                    f"Also, I recently started using {v_sec_mistake} for my {secondary_pred}—wait, "
                    f"let me correct that immediately: my {secondary_pred} is actually {v_sec_corrected}."
                ),
                occurred_at=t1 + timedelta(minutes=2),
            ),
            TemporalTurn(
                turn_id=f"{scenario_id}_s01_t03",
                session_id=f"{scenario_id}_s01",
                role="user",
                content=f"For my {multi_pred}, I hold both {mv_keep} and {mv_end}.",
                occurred_at=t1 + timedelta(minutes=4),
            ),
            # Session 2 at t2: E2 update reported at t2 (initially assumed valid from t2)
            TemporalTurn(
                turn_id=f"{scenario_id}_s02_t01",
                session_id=f"{scenario_id}_s02",
                role="user",
                content=f"Update on my profile: my {primary_pred} is now {v2_new}.",
                occurred_at=t2,
            ),
            # Session 3 at t3: E3 retroactive timing revision (clarifies v2_new actually started at t_retro)
            TemporalTurn(
                turn_id=f"{scenario_id}_s03_t01",
                session_id=f"{scenario_id}_s03",
                role="user",
                content=(
                    f"To clarify the effective timing: my transition from {v1_old} to {v2_new} "
                    f"actually took effect on {retro_date_str}."
                ),
                occurred_at=t3,
            ),
            # Session 4 at t4: R7 multi-valued ending
            TemporalTurn(
                turn_id=f"{scenario_id}_s04_t01",
                session_id=f"{scenario_id}_s04",
                role="user",
                content=(
                    f"As of {t4.strftime('%Y-%m-%d')}, I ended my {mv_end} under {multi_pred}, "
                    f"though I still keep {mv_keep}."
                ),
                occurred_at=t4,
            ),
        )

        # Scripted bi-temporal version chain (E1 -> E2 -> E3 + stable + R2 same-day + R7 multi-valued)
        mk_primary = f"{scenario_id}_mk_primary"
        mk_stable = f"{scenario_id}_mk_stable"
        mk_secondary = f"{scenario_id}_mk_secondary"
        mk_mv_keep = f"{scenario_id}_mk_mv_keep"
        mk_mv_end = f"{scenario_id}_mk_mv_end"

        scripted_versions = (
            # v1: believed [t1, t2), valid [t1, inf)
            ScriptedVersionRecord(
                version_id=f"{scenario_id}_v1",
                memory_key=mk_primary,
                predicate_key=primary_pred,
                single_valued=True,
                value=v1_old,
                valid_from=t1,
                valid_to=None,
                valid_from_basis="explicit",
                recorded_from=t1,
                recorded_to=t2,
                is_current_belief_at_rho=False,
                is_currently_valid_at_rho=False,
            ),
            # v1_prime: truncated re-issue at E2, believed [t2, t3), valid [t1, t2)
            ScriptedVersionRecord(
                version_id=f"{scenario_id}_v1_prime",
                memory_key=mk_primary,
                predicate_key=primary_pred,
                single_valued=True,
                value=v1_old,
                valid_from=t1,
                valid_to=t2,
                valid_from_basis="explicit",
                recorded_from=t2,
                recorded_to=t3,
                is_current_belief_at_rho=False,
                is_currently_valid_at_rho=False,
            ),
            # v2: inserted at E2, believed [t2, t3), valid [t2, inf)
            ScriptedVersionRecord(
                version_id=f"{scenario_id}_v2",
                memory_key=mk_primary,
                predicate_key=primary_pred,
                single_valued=True,
                value=v2_new,
                valid_from=t2,
                valid_to=None,
                valid_from_basis="inferred",
                recorded_from=t2,
                recorded_to=t3,
                is_current_belief_at_rho=False,
                is_currently_valid_at_rho=False,
            ),
            # v1_double_prime: retroactive revision at E3, believed [t3, inf), valid [t1, t_retro)
            ScriptedVersionRecord(
                version_id=f"{scenario_id}_v1_double_prime",
                memory_key=mk_primary,
                predicate_key=primary_pred,
                single_valued=True,
                value=v1_old,
                valid_from=t1,
                valid_to=t_retro,
                valid_from_basis="explicit",
                recorded_from=t3,
                recorded_to=None,
                is_current_belief_at_rho=True,
                is_currently_valid_at_rho=False,
            ),
            # v2_prime: retroactive revision at E3, believed [t3, inf), valid [t_retro, inf)
            ScriptedVersionRecord(
                version_id=f"{scenario_id}_v2_prime",
                memory_key=mk_primary,
                predicate_key=primary_pred,
                single_valued=True,
                value=v2_new,
                valid_from=t_retro,
                valid_to=None,
                valid_from_basis="explicit",
                recorded_from=t3,
                recorded_to=None,
                is_current_belief_at_rho=True,
                is_currently_valid_at_rho=True,
            ),
            # v_stable: unchanged throughout [t1, inf)
            ScriptedVersionRecord(
                version_id=f"{scenario_id}_v_stable",
                memory_key=mk_stable,
                predicate_key=stable_pred,
                single_valued=True,
                value=v_stable,
                valid_from=t1,
                valid_to=None,
                valid_from_basis="explicit",
                recorded_from=t1,
                recorded_to=None,
                is_current_belief_at_rho=True,
                is_currently_valid_at_rho=True,
            ),
            # v_sec_mistake: initial mistaken claim closed at same-day R2 correction (valid_to null)
            ScriptedVersionRecord(
                version_id=f"{scenario_id}_v_sec_mistake",
                memory_key=mk_secondary,
                predicate_key=secondary_pred,
                single_valued=True,
                value=v_sec_mistake,
                valid_from=t1,
                valid_to=None,
                valid_from_basis="inferred",
                recorded_from=t1,
                recorded_to=t1 + timedelta(minutes=2),
                is_current_belief_at_rho=False,
                is_currently_valid_at_rho=False,
            ),
            # v_sec_corrected: inferred start + same-day R2 correction
            ScriptedVersionRecord(
                version_id=f"{scenario_id}_v_sec_corrected",
                memory_key=mk_secondary,
                predicate_key=secondary_pred,
                single_valued=True,
                value=v_sec_corrected,
                valid_from=t1,
                valid_to=None,
                valid_from_basis="inferred",
                recorded_from=t1 + timedelta(minutes=2),
                recorded_to=None,
                is_current_belief_at_rho=True,
                is_currently_valid_at_rho=True,
            ),
            # v_mv_keep: multi-valued active item
            ScriptedVersionRecord(
                version_id=f"{scenario_id}_v_mv_keep",
                memory_key=mk_mv_keep,
                predicate_key=multi_pred,
                single_valued=False,
                value=mv_keep,
                valid_from=t1,
                valid_to=None,
                valid_from_basis="explicit",
                recorded_from=t1 + timedelta(minutes=4),
                recorded_to=None,
                is_current_belief_at_rho=True,
                is_currently_valid_at_rho=True,
            ),
            # v_mv_end_closed: multi-valued ended item (R7)
            ScriptedVersionRecord(
                version_id=f"{scenario_id}_v_mv_end_closed",
                memory_key=mk_mv_end,
                predicate_key=multi_pred,
                single_valued=False,
                value=mv_end,
                valid_from=t1,
                valid_to=t4,
                valid_from_basis="explicit",
                recorded_from=t4,
                recorded_to=None,
                is_current_belief_at_rho=True,
                is_currently_valid_at_rho=False,
            ),
        )

        # Six conversational questions (3 present-state, 3 history-dependent)
        change_gold = f"{v1_old} -> {v2_new} (effective {retro_date_str})"
        questions = (
            TemporalQuestion(
                question_id=f"{scenario_id}_q01_current",
                mode="current",
                stratum=assign_stratum("current", requires_non_current_valid_version=False),
                query_text=str(domain["q_current"]),
                as_of_time=rho,
                reference_time=rho,
                gold_value=v2_new,
                gold_valid_from=t_retro,
                gold_valid_to=None,
                requires_non_current_valid_version=False,
                expected_correct_under_p=True,
                expected_correct_under_a2=True,
                naive_latest_heuristic_correct=True,
                naive_previous_heuristic_correct=False,
            ),
            TemporalQuestion(
                question_id=f"{scenario_id}_q02_stable",
                mode="stable",
                stratum=assign_stratum("stable", requires_non_current_valid_version=False),
                query_text=str(domain["q_stable"]),
                as_of_time=rho,
                reference_time=rho,
                gold_value=v_stable,
                gold_valid_from=t1,
                gold_valid_to=None,
                requires_non_current_valid_version=False,
                expected_correct_under_p=True,
                expected_correct_under_a2=True,
                naive_latest_heuristic_correct=True,
                naive_previous_heuristic_correct=False,
            ),
            TemporalQuestion(
                question_id=f"{scenario_id}_q03_as_of_current_period",
                mode="as_of",
                stratum=assign_stratum("as_of", requires_non_current_valid_version=False),
                query_text=str(domain["q_as_of_current"]).format(date_str=curr_date_str),
                as_of_time=t_query_current,
                reference_time=rho,
                gold_value=v2_new,
                gold_valid_from=t_retro,
                gold_valid_to=None,
                requires_non_current_valid_version=False,
                expected_correct_under_p=True,
                expected_correct_under_a2=True,
                naive_latest_heuristic_correct=True,
                naive_previous_heuristic_correct=False,
            ),
            TemporalQuestion(
                question_id=f"{scenario_id}_q04_as_of_earlier_period",
                mode="as_of",
                stratum=assign_stratum("as_of", requires_non_current_valid_version=True),
                query_text=str(domain["q_as_of_past"]).format(date_str=past_date_str),
                as_of_time=t_query_past,
                reference_time=rho,
                gold_value=v1_old,
                gold_valid_from=t1,
                gold_valid_to=t_retro,
                requires_non_current_valid_version=True,
                expected_correct_under_p=True,
                expected_correct_under_a2=False,
                naive_latest_heuristic_correct=False,
                naive_previous_heuristic_correct=True,
            ),
            TemporalQuestion(
                question_id=f"{scenario_id}_q05_before_change",
                mode="before_change",
                stratum=assign_stratum("before_change", requires_non_current_valid_version=True),
                query_text=str(domain["q_before_change"]),
                as_of_time=t_retro - timedelta(seconds=1),
                reference_time=rho,
                gold_value=v1_old,
                gold_valid_from=t1,
                gold_valid_to=t_retro,
                requires_non_current_valid_version=True,
                expected_correct_under_p=True,
                expected_correct_under_a2=False,
                naive_latest_heuristic_correct=False,
                naive_previous_heuristic_correct=True,
            ),
            TemporalQuestion(
                question_id=f"{scenario_id}_q06_what_changed",
                mode="what_changed",
                stratum=assign_stratum("what_changed", requires_non_current_valid_version=True),
                query_text=str(domain["q_what_changed"]),
                as_of_time=None,
                reference_time=rho,
                gold_value=change_gold,
                gold_valid_from=t1,
                gold_valid_to=None,
                requires_non_current_valid_version=True,
                expected_correct_under_p=True,
                expected_correct_under_a2=False,
                naive_latest_heuristic_correct=False,
                naive_previous_heuristic_correct=False,
            ),
        )

        # Deterministic API-level audit_as_of(user_id, valid_time, belief_time) grid
        audit_grid = (
            AuditGridProbe(
                audit_probe_id=f"{scenario_id}_audit_01_initial_belief",
                valid_time=t_query_between,
                belief_time=t1 + timedelta(days=5),
                expected_version_ids=(f"{scenario_id}_v1",),
                expected_values=(v1_old,),
                rationale="Before E2, v1 is believed valid for all times >= t1.",
            ),
            AuditGridProbe(
                audit_probe_id=f"{scenario_id}_audit_02_pre_retroactive_belief",
                valid_time=t_query_between,
                belief_time=t2 + timedelta(days=2),
                expected_version_ids=(f"{scenario_id}_v1_prime",),
                expected_values=(v1_old,),
                rationale=(
                    "Between E2 (t2) and E3 (t3), system believed v1_prime held on [t1, t2) covering t_query_between."
                ),
            ),
            AuditGridProbe(
                audit_probe_id=f"{scenario_id}_audit_03_post_retroactive_belief",
                valid_time=t_query_between,
                belief_time=t3 + timedelta(days=2),
                expected_version_ids=(f"{scenario_id}_v2_prime",),
                expected_values=(v2_new,),
                rationale=(
                    "After E3 (t3), retroactive correction shows v2_prime held on [t_retro, inf) "
                    "covering t_query_between."
                ),
            ),
            AuditGridProbe(
                audit_probe_id=f"{scenario_id}_audit_04_historical_january_belief",
                valid_time=t_query_past,
                belief_time=rho,
                expected_version_ids=(f"{scenario_id}_v1_double_prime",),
                expected_values=(v1_old,),
                rationale="At reference time rho, v1_double_prime remains the current belief for [t1, t_retro).",
            ),
        )

        scenarios.append(
            TemporalScenario(
                scenario_id=scenario_id,
                split=split,
                seed=resolved_seed + idx,
                user_id=user_id,
                domain_id=domain_id,
                reference_time=rho,
                turns=turns,
                scripted_versions=scripted_versions,
                questions=questions,
                audit_grid=audit_grid,
            )
        )

    hist_count = sum(1 for s in scenarios for q in s.questions if q.stratum == "history-dependent")
    pres_count = sum(1 for s in scenarios for q in s.questions if q.stratum == "present-state")
    dataset = SuiteTDataset(
        split=split,
        seed=resolved_seed,
        scenario_count=len(scenarios),
        total_questions=hist_count + pres_count,
        history_dependent_questions=hist_count,
        present_state_questions=pres_count,
        total_audit_probes=sum(len(s.audit_grid) for s in scenarios),
        scenarios=tuple(scenarios),
    )
    verify_suite_t(dataset)
    return dataset


def verify_suite_t(dataset: SuiteTDataset) -> None:
    """Verify stratum balance, heuristic failure guards, dialogue fact presence, and audit grid."""
    if dataset.history_dependent_questions != dataset.present_state_questions:
        raise ValueError("stratum_imbalance_detected")
    for scenario in dataset.scenarios:
        hist_qs = [q for q in scenario.questions if q.stratum == "history-dependent"]
        pres_qs = [q for q in scenario.questions if q.stratum == "present-state"]
        if len(hist_qs) != 3 or len(pres_qs) != 3:
            raise ValueError(f"scenario_stratum_counts_invalid: {scenario.scenario_id}")
        modes = {q.mode for q in scenario.questions}
        if modes != {"current", "as_of", "what_changed", "before_change", "stable"}:
            raise ValueError(f"missing_question_modes_in_scenario: {scenario.scenario_id}")

        dialogue_text = " ".join(t.content for t in scenario.turns)
        for v in scenario.scripted_versions:
            if v.value not in dialogue_text:
                raise ValueError(f"scripted_version_value_missing_from_dialogue: {v.version_id}")

        for q in scenario.questions:
            if q.mode != "what_changed" and q.gold_value.lower() in q.query_text.lower():
                raise ValueError(f"question_leaks_gold_value: {q.question_id}")

        # Verify naive heuristics cannot score 100% on any scenario
        latest_acc = sum(1 for q in scenario.questions if q.naive_latest_heuristic_correct) / len(scenario.questions)
        prev_acc = sum(1 for q in scenario.questions if q.naive_previous_heuristic_correct) / len(scenario.questions)
        if latest_acc > 0.50 or prev_acc > 0.50:
            raise ValueError(f"naive_heuristic_scores_too_high: {scenario.scenario_id}")

        # Verify audit grid probes B and C query the exact same valid_time at different belief_times
        p_pre = scenario.audit_grid[1]
        p_post = scenario.audit_grid[2]
        if p_pre.valid_time != p_post.valid_time or p_pre.belief_time >= p_post.belief_time:
            raise ValueError(f"invalid_bitemporal_audit_pair: {scenario.scenario_id}")
        if p_pre.expected_values == p_post.expected_values:
            raise ValueError(f"audit_pair_fails_to_distinguish_belief_time: {scenario.scenario_id}")


def write_suite_t_files(data_dir: Path = DATA_DIR, manifest_path: Path = MANIFEST_PATH) -> dict[str, str]:
    """Generate dev and test splits for Suite T, write JSON files, and update MANIFEST.txt."""
    data_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    lines: list[str] = []
    for split in ("dev", "test"):
        dataset = generate_suite_t(split)
        filename = f"own_T_{split}.json"
        out_path = data_dir / filename
        payload = dataset.model_dump_json(indent=2) + "\n"
        out_path.write_text(payload, encoding="utf-8")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        digests[filename] = digest
        lines.append(f"{digest}  {filename}")
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return digests


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for generating and verifying Own Suite T."""
    parser = argparse.ArgumentParser(description="Generate and verify Own Suite T (temporal-change).")
    parser.add_argument("--write", action="store_true", help="Write JSON files to data/ and update MANIFEST.txt.")
    args = parser.parse_args(argv)
    if args.write:
        digests = write_suite_t_files()
        logger.info("suite_t_files_written", files=list(digests.keys()))
        console.print_json(json.dumps(digests, indent=2))
    else:
        for split in ("dev", "test"):
            ds = generate_suite_t(split)
            logger.info(
                "suite_t_verified",
                split=split,
                scenario_count=ds.scenario_count,
                total_questions=ds.total_questions,
            )
            console.print(
                f"{split}: scenarios={ds.scenario_count}, questions={ds.total_questions} "
                f"(hist={ds.history_dependent_questions}, pres={ds.present_state_questions})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
