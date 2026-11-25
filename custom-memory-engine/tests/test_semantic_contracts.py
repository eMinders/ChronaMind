"""``semantic.contracts``: the extraction boundary refuses what it cannot validate (W3.5, ``V2-D88``).

The contract is the only thing standing between a model's free-form output and the governed write path, so every field
that the engine will later compare, key on or render is checked here. These tests pin the *closed* vocabularies, the
*open* vocabulary's acceptance, and the two properties a caller must not be able to fake: a claim without a quote, and
a confidence outside its range.

Each closed vocabulary is also pinned to its canonical home, because a second list that drifts from the schema is
exactly the "two implementations of one rule" defect this project treats as a review finding.
"""

import pytest

from custom_memory_engine.engine.gate import AssertionMode as GateAssertionMode
from custom_memory_engine.engine.hybrid import EMBEDDING_SLOTS
from custom_memory_engine.model.axes import FACETS, KINDS
from custom_memory_engine.semantic.contracts import (
    ASSERTION_MODES,
    CARDINALITIES,
    CLAIM_KINDS,
    RELATIONS,
    UM_FACETS,
    AdjudicatedRelation,
    CandidateBelief,
    ContractError,
    Embedder,
    EntityMention,
    ExtractedClaim,
    ExtractionRequest,
    ExtractionResult,
    RelationAdjudicator,
    StructuredExtractor,
    TemporalBounds,
)

# --------------------------------------------------------------------------------------- closed vocabularies agree


def test_every_closed_vocabulary_equals_its_canonical_home() -> None:
    assert ASSERTION_MODES == set(GateAssertionMode.__value__.__args__)
    assert UM_FACETS == set(FACETS)
    assert CLAIM_KINDS <= set(KINDS)
    assert CARDINALITIES == {"single", "multi", "event"}
    assert RELATIONS == {"duplicate", "update", "correction", "contradiction", "additive", "unrelated"}


# ----------------------------------------------------------------------------------------------------- the claim


def test_a_claim_carries_what_the_write_path_will_need() -> None:
    claim = ExtractedClaim(
        content="Ahmed lives in Berlin",
        verbatim_quote="أحمد يسكن في برلين",
        cardinality="single",
        um_facet="context",
        semantic_slot="residence",
        entities=(EntityMention(label="أحمد", canonical="Ahmed"),),
        temporal=TemporalBounds(start="2026-01-01T00:00:00+00:00"),
        specifics=("Berlin",),
        confidence=0.8,
    )
    assert claim.single_valued is True
    assert claim.entities[0].canonical == "Ahmed"


@pytest.mark.parametrize("cardinality", ["multi", "event"])
def test_only_a_single_valued_claim_may_supersede(cardinality: str) -> None:
    """A series is not a contradiction: two events are two occurrences, so neither supersedes the other."""
    claim = ExtractedClaim(content="c", verbatim_quote="c", cardinality=cardinality)  # type: ignore[arg-type]
    assert claim.single_valued is False


@pytest.mark.parametrize(
    ("keyword", "value", "code"),
    [
        ("content", "   ", "claim_content_required"),
        ("verbatim_quote", "", "claim_quote_required"),
        ("kind", "abstraction", "claim_kind_invalid"),
        ("assertion_mode", "certain", "assertion_mode_invalid"),
        ("cardinality", "many", "cardinality_invalid"),
        ("um_facet", "inferred_preference", "um_facet_invalid"),
        ("semantic_slot", "  ", "semantic_slot_invalid"),
        ("confidence", 1.4, "confidence_out_of_range"),
    ],
)
def test_a_claim_outside_the_contract_is_refused_with_a_static_code(keyword: str, value: object, code: str) -> None:
    payload: dict[str, object] = {"content": "c", "verbatim_quote": "c", keyword: value}
    with pytest.raises(ContractError) as refused:
        ExtractedClaim(**payload)  # type: ignore[arg-type]
    assert refused.value.code == code


def test_the_open_slot_accepts_vocabulary_no_catalog_could_hold() -> None:
    """The defect `V2-D87` fixes: "domain semantics" must not need a migration to be sayable."""
    for slot in ("residence", "meeting_time_constraint", "preferred_editor", "إقامة", "住居"):
        assert ExtractedClaim(content="c", verbatim_quote="c", semantic_slot=slot).semantic_slot == slot


def test_an_entity_mention_needs_a_surface_form_but_not_a_resolved_id() -> None:
    with pytest.raises(ContractError) as refused:
        EntityMention(label=" ")
    assert refused.value.code == "entity_label_required"
    # resolution is the cascade's job (`semantic.entities`); a mention may arrive unresolved
    assert EntityMention(label="my boss").entity_id is None


def test_the_candidate_pair_makes_a_claim_without_its_source_unrepresentable() -> None:
    """Grounding needs claim *and* text; a claim travelling alone must not type-check."""
    claim = ExtractedClaim(content="c", verbatim_quote="c")
    assert CandidateBelief(claim=claim, source_text="c", source_ref="t1").source_ref == "t1"
    with pytest.raises(ContractError):
        CandidateBelief(claim=claim, source_text="", source_ref="t1")
    with pytest.raises(ContractError):
        CandidateBelief(claim=claim, source_text="c", source_ref=" ")


# ----------------------------------------------------------------------------------------------- other payloads


def test_an_extraction_request_carries_the_anchor_a_relative_date_needs() -> None:
    request = ExtractionRequest(text="I moved last spring", occurred_at="2026-03-10T09:00:00+00:00")
    assert request.context == ()
    with pytest.raises(ContractError) as refused:
        ExtractionRequest(text="x", occurred_at=" ")
    assert refused.value.code == "extraction_occurred_at_required"


def test_an_extraction_result_names_the_extractor_that_produced_it() -> None:
    """`extractor_version` is stored on every version the extraction writes, so re-extraction is comparable."""
    result = ExtractionResult(claims=(), extractor_version="m@v1")
    assert result.extractor_version == "m@v1"
    with pytest.raises(ContractError) as refused:
        ExtractionResult(claims=(), extractor_version="")
    assert refused.value.code == "extractor_version_required"


def test_an_adjudicated_relation_must_name_an_existing_belief_and_a_known_relation() -> None:
    assert AdjudicatedRelation(existing_id="v1", relation="update").relation == "update"
    with pytest.raises(ContractError) as no_target:
        AdjudicatedRelation(existing_id="", relation="update")
    assert no_target.value.code == "adjudicated_relation_requires_an_existing_id"
    with pytest.raises(ContractError) as bad_relation:
        AdjudicatedRelation(existing_id="v1", relation="overwrites")  # type: ignore[arg-type]
    assert bad_relation.value.code == "adjudicated_relation_invalid"


# ------------------------------------------------------------------------------------------------ the protocols


def test_the_three_model_shaped_seams_are_structural_protocols_not_base_classes() -> None:
    """A deployment implements one by shape; the engine imports no provider and defines no ABC to inherit."""

    class Extractor:
        def extract(self, request: ExtractionRequest) -> ExtractionResult:
            return ExtractionResult(claims=(), extractor_version="x")

    class Adjudicator:
        def adjudicate(self, claim, existing, *, language=None):  # type: ignore[no-untyped-def]
            return ()

    class EmbedderImpl:
        @property
        def model(self) -> str:
            return "BAAI/bge-m3"

        @property
        def dimensions(self) -> int:
            return 1024

        def embed(self, texts):  # type: ignore[no-untyped-def]
            return [tuple([0.0] * 1024) for _ in texts]

    assert isinstance(Extractor(), StructuredExtractor)
    assert isinstance(Adjudicator(), RelationAdjudicator)
    assert isinstance(EmbedderImpl(), Embedder)


def test_an_embedder_declares_a_width_a_slot_can_index() -> None:
    class Embedder1024:
        model = "BAAI/bge-m3"
        dimensions = 1024

    assert Embedder1024.dimensions in EMBEDDING_SLOTS
