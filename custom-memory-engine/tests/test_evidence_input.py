"""EvidenceInput carries a turn (v1, unchanged) or an external source (M4, E-013); anything else is refused."""

import pytest
from pydantic import ValidationError

from custom_memory_engine.engine.transactions import EvidenceInput, TransactionError
from custom_memory_engine.grounding.sources import canonical_hash

HASH = canonical_hash("captured text")


def test_a_turn_is_constructed_exactly_as_in_v1() -> None:
    evidence = EvidenceInput(turn_id="t1", evidence_span="I am vegetarian")
    assert (evidence.source_type, evidence.turn_id, evidence.source_ref, evidence.content_hash) == (
        "turn",
        "t1",
        None,
        None,
    )
    assert evidence.evidence_role == "primary"


def test_an_external_source_needs_a_ref_a_locator_and_a_sha256_hash() -> None:
    evidence = EvidenceInput(source_type="external", source_ref="ci://run/1", content_hash=HASH, evidence_span="L1-2")
    assert (evidence.turn_id, evidence.source_ref, evidence.content_hash) == (None, "ci://run/1", HASH)


@pytest.mark.parametrize(
    "arguments",
    [
        {},  # a turn without a turn id
        {"turn_id": " "},  # blank
        {"turn_id": "t1", "source_ref": "x"},  # a turn may not carry an external ref
        {"turn_id": "t1", "content_hash": HASH},
        {"source_type": "external"},  # external without ref/hash
        {"source_type": "external", "source_ref": "x"},
        {"source_type": "external", "content_hash": HASH},
        {"source_type": "external", "source_ref": "x", "content_hash": HASH, "turn_id": "t1"},
        {"source_type": "external", "source_ref": "x", "content_hash": "abc"},
        {"source_type": "external", "source_ref": "x", "content_hash": HASH.upper()},
        {"source_type": "external", "source_ref": " ", "content_hash": HASH},
        {"source_type": "artifact", "source_ref": "x", "content_hash": HASH},  # an artifact is never evidence
        {"source_type": "tool", "source_ref": "x", "content_hash": HASH},
    ],
)
def test_malformed_evidence_is_refused(arguments: dict[str, str]) -> None:
    with pytest.raises((ValidationError, TransactionError)):
        EvidenceInput(evidence_span="s", **arguments)  # type: ignore[arg-type]


def test_a_blank_locator_is_refused() -> None:
    with pytest.raises((ValidationError, TransactionError)):
        EvidenceInput(turn_id="t1", evidence_span=" ")
