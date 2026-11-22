"""``engine.hybrid``: fusion, vector literals, slots, the trigram probe and the reranker seam (W3.5).

These are the pure parts of hybrid recall -- no database, no model -- so they run in seconds and pin the arithmetic
that decides which memory a caller sees first.
"""

import math

import pytest

from custom_memory_engine.engine.hybrid import (
    EMBEDDING_SLOTS,
    RRF_CONSTANT,
    ArmRanking,
    Recall,
    RecallError,
    RecallQuery,
    arm_depth,
    fuse,
    rerank,
    slot_for_dimension,
    trigram_token,
    vector_literal,
)

# ------------------------------------------------------------------------------------------------ vector literals


def test_a_vector_literal_is_the_bracketed_form_halfvec_accepts() -> None:
    """``halfvec`` refuses PostgreSQL's ``{...}`` array form; the literal must be ``[...]``.

    This is not cosmetic: psycopg's own ``list`` adapter renders ``{0.1,0.2}``, which ``vector`` accepts and every
    multilingual ``halfvec`` slot rejects, so relying on it would break exactly the arms this phase adds.
    """
    assert vector_literal([0.5, -1.25, 3.0]) == "[0.5,-1.25,3.0]"


def test_a_vector_literal_refuses_an_empty_non_finite_or_non_numeric_vector() -> None:
    for bad in ([], [math.inf], [math.nan], [True], ["0.1"]):
        with pytest.raises(RecallError):
            vector_literal(bad)  # type: ignore[arg-type]


def test_a_vector_literal_preserves_a_float_that_is_not_exact_in_binary() -> None:
    literal = vector_literal([0.1, 0.30000000000000004])
    assert literal == "[0.1,0.30000000000000004]"
    assert float(literal.strip("[]").split(",")[1]) == 0.30000000000000004


# ----------------------------------------------------------------------------------------------- dimension slots


def test_every_documented_slot_maps_to_a_column_and_nothing_else_does() -> None:
    for dimensions, column in EMBEDDING_SLOTS.items():
        assert slot_for_dimension(dimensions) == (
            column,
            "vector" if column == "embedding" else "halfvec",
        )
    for unsupported in (0, 1, 512, 4096, -1024):
        with pytest.raises(RecallError) as refused:
            slot_for_dimension(unsupported)
        assert refused.value.code == "embedding_dimension_unsupported"


def test_the_legacy_slot_keeps_the_vector_type_so_existing_data_is_untouched() -> None:
    assert slot_for_dimension(384) == ("embedding", "vector")


# -------------------------------------------------------------------------------------------------- recall query


def test_a_query_without_a_vector_runs_the_two_lexical_arms() -> None:
    query = RecallQuery(text="where do I live")
    assert query.arms == ("lexical", "trigram")


def test_a_query_with_a_vector_adds_the_dense_arm() -> None:
    query = RecallQuery(text="where do I live", embedding=(0.1,) * 1024, embedding_model="BAAI/bge-m3")
    assert query.arms == ("dense", "lexical", "trigram")


def test_a_query_modelled_search_requires_the_model_name_the_dense_arm_filters_on() -> None:
    with pytest.raises(RecallError) as refused:
        RecallQuery(text="x", embedding=(0.1,))
    assert refused.value.code == "nonblank_embedding_model_required"
    with pytest.raises(RecallError) as other:
        RecallQuery(text="x", embedding_model="BAAI/bge-m3")
    assert other.value.code == "embedding_model_without_embedding"


def test_a_query_refuses_blank_text_and_an_unindexable_dimension() -> None:
    with pytest.raises(RecallError) as blank:
        RecallQuery(text="   ")
    assert blank.value.code == "nonblank_query_text_required"
    with pytest.raises(RecallError) as dimension:
        RecallQuery(text="x", embedding=tuple([0.0] * 7), embedding_model="m")
    assert dimension.value.code == "embedding_dimension_unsupported"


# ----------------------------------------------------------------------------------------------- arm depth & RRF


def test_arm_depth_is_wider_than_the_page_and_never_below_the_floor() -> None:
    assert arm_depth(5) == 20
    assert arm_depth(10) == 40
    assert arm_depth(100) == 400


def test_fusion_rewards_agreement_and_breaks_ties_on_id() -> None:
    """A version found by two arms outranks one found by a single arm at the same position."""
    dense = ArmRanking("dense", ("a", "b", "c"))
    lexical = ArmRanking("lexical", ("b", "d"))
    fused = fuse((dense, lexical), limit=10)
    # b is first in both arms; then the single-arm rankings in rank order: a (dense 1), d (lexical 2), c (dense 3).
    assert [version_id for version_id, _ in fused] == ["b", "a", "d", "c"]
    assert fused[0][1] == pytest.approx(1 / (RRF_CONSTANT + 1) + 1 / (RRF_CONSTANT + 2))
    assert fused[1][1] == pytest.approx(1 / (RRF_CONSTANT + 1))


def test_fusion_is_a_total_order_so_two_identical_calls_agree_byte_for_byte() -> None:
    arms = (ArmRanking("dense", ("b", "a")), ArmRanking("lexical", ("a", "b")))
    first = fuse(arms, limit=10)
    second = fuse(tuple(reversed(arms)), limit=10)
    assert first == second
    assert [version_id for version_id, _ in first] == ["a", "b"]


def test_fusion_truncates_to_the_page_and_rejects_a_nonpositive_constant() -> None:
    arms = (ArmRanking("dense", ("a", "b", "c", "d")),)
    assert len(fuse(arms, limit=2)) == 2
    with pytest.raises(RecallError):
        fuse(arms, limit=0)
    with pytest.raises(RecallError):
        fuse(arms, limit=1, k=0)


def test_fusion_of_no_arms_is_an_empty_page_not_an_error() -> None:
    assert fuse((), limit=5) == ()


def test_an_arms_repeated_id_contributes_once() -> None:
    assert fuse((ArmRanking("dense", ("a", "a", "a")),), limit=5) == (("a", pytest.approx(1 / (RRF_CONSTANT + 1))),)


# --------------------------------------------------------------------------------------------------- trigram arm


def test_the_trigram_probe_takes_the_longest_token_in_any_script() -> None:
    assert trigram_token("What is my residence in Berlin?") == "residence"
    # Arabic: two characters is a word, not a prefix, so a short token is still worth probing
    assert trigram_token("أين أسكن الآن") == "أسكن"
    # CJK: same reason
    assert trigram_token("我家住在哪里") == "我家住在哪里"
    # mixed script: the longest token wins regardless of script
    assert trigram_token("my manager أحمد said") == "manager"


def test_the_trigram_probe_declines_a_query_that_would_match_most_of_the_corpus() -> None:
    assert trigram_token("a b c") is None
    assert trigram_token("") is None
    assert trigram_token("?!,.") is None


# --------------------------------------------------------------------------------------------------- the reranker


class _Reverse:
    """A reranker that reverses the fused order: the simplest scorer with an observable effect."""

    def score(self, query_text: str, candidates) -> list[float]:
        assert query_text
        return [float(index) for index in range(len(candidates))]


def _recall() -> Recall:
    fused = fuse((ArmRanking("dense", ("a", "b", "c")),), limit=10)
    return Recall(ordered=tuple(v for v, _ in fused), scored=fused, arms=(), candidates=3)


def test_a_reranker_reorders_the_page_and_cannot_admit_a_new_row() -> None:
    reranked = rerank(_recall(), "question", _Reverse(), limit=2)
    assert reranked.ordered == ("c", "b")
    assert set(reranked.ordered) <= {"a", "b", "c"}


def test_no_reranker_is_the_default_and_changes_nothing() -> None:
    original = _recall()
    assert rerank(original, "question", None, limit=10) is original


def test_a_reranker_returning_the_wrong_number_of_scores_is_refused() -> None:
    class _Short:
        def score(self, query_text: str, candidates) -> list[float]:
            return [1.0]

    with pytest.raises(RecallError) as refused:
        rerank(_recall(), "question", _Short(), limit=3)
    assert refused.value.code == "reranker_score_count_mismatch"


def test_a_reranker_returning_a_non_finite_score_is_refused() -> None:
    class _Infinite:
        def score(self, query_text: str, candidates) -> list[float]:
            return [math.inf for _ in candidates]

    with pytest.raises(RecallError) as refused:
        rerank(_recall(), "question", _Infinite(), limit=3)
    assert refused.value.code == "reranker_score_must_be_finite"


def test_an_empty_page_is_returned_unchanged_by_a_reranker() -> None:
    empty = Recall(ordered=(), scored=(), arms=(), candidates=0)
    assert rerank(empty, "question", _Reverse(), limit=3) is empty
