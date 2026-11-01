"""Unit tests for transactional supersession and archival models and guards (RL-042)."""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.engine.transactions import (
    AblationDisabledError,
    CandidateVersionInput,
    DerivationInput,
    EvidenceInput,
    TransactionError,
    commit_initial_version,
    end_multivalued,
    supersede_single,
)

T1 = datetime(2026, 1, 15, tzinfo=UTC)
T2 = datetime(2026, 6, 1, tzinfo=UTC)


def _valid_candidate(**overrides: object) -> CandidateVersionInput:
    payload: dict[str, object] = {
        "version_id": "v1",
        "memory_key": "user:residence",
        "user_id": "u1",
        "subject_key": "user",
        "predicate_key": "residence",
        "single_valued": True,
        "content": "Lives in Cairo",
        "valid_from": T1,
        "evidence": (EvidenceInput(turn_id="turn-1", evidence_span="I live in Cairo"),),
    }
    payload.update(overrides)
    return CandidateVersionInput(**payload)  # type: ignore[arg-type]


def test_candidate_version_input_validates_bounds_and_provenance() -> None:
    cand = _valid_candidate(valid_until=T2)
    assert cand.valid_from == T1
    assert cand.valid_until == T2
    cand.require_provenance()

    # Empty / inverted event_range rejected
    with pytest.raises(TransactionError, match="event_range_must_be_nonempty"):
        _valid_candidate(valid_from=T2, valid_until=T1)

    with pytest.raises(TransactionError, match="event_range_must_be_nonempty"):
        _valid_candidate(valid_from=T1, valid_until=T1)

    # Naive datetime rejected
    with pytest.raises(TransactionError, match="transaction_aware_instant_required"):
        _valid_candidate(valid_from=datetime(2026, 1, 15))

    # Self-derivation rejected
    with pytest.raises(TransactionError, match="self_derivation_forbidden"):
        _valid_candidate(
            version_id="v-self",
            derivations=(DerivationInput(source_version_id="v-self", derivation_type="consolidation"),),
        )

    # Missing provenance rejected when require_provenance() is called
    no_prov = _valid_candidate(evidence=(), derivations=())
    with pytest.raises(TransactionError, match="active_or_archived_version_requires_provenance"):
        no_prov.require_provenance()


@pytest.mark.asyncio
async def test_a2b_disabled_by_default_raises_ablation_disabled_error() -> None:
    dummy_conn = cast(AsyncConnection, object())
    cand = _valid_candidate()

    with pytest.raises(AblationDisabledError, match="a2b_disabled_by_d15"):
        await commit_initial_version(dummy_conn, cand, ablation="A2b", allow_a2b=False)

    with pytest.raises(AblationDisabledError, match="a2b_disabled_by_d15"):
        await supersede_single(dummy_conn, cand, ablation="A2b", allow_a2b=False)

    with pytest.raises(AblationDisabledError, match="a2b_disabled_by_d15"):
        await end_multivalued(
            dummy_conn,
            user_id="u1",
            version_id="v1",
            valid_end=T1 + timedelta(days=30),
            ablation="A2b",
            allow_a2b=False,
        )


@pytest.mark.asyncio
async def test_supersede_single_rejects_multivalued_candidate_under_p() -> None:
    dummy_conn = cast(AsyncConnection, object())
    cand = _valid_candidate(single_valued=False)
    with pytest.raises(TransactionError, match="supersede_single_requires_single_valued_candidate"):
        await supersede_single(dummy_conn, cand, ablation="P")


@pytest.mark.asyncio
async def test_end_multivalued_rejects_a1_flat_mode() -> None:
    dummy_conn = cast(AsyncConnection, object())
    with pytest.raises(TransactionError, match="a1_flat_disables_multivalued_truncation"):
        await end_multivalued(
            dummy_conn,
            user_id="u1",
            version_id="v1",
            valid_end=T2,
            ablation="A1",
        )
