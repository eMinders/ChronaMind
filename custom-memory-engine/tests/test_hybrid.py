"""``engine.hybrid``: fusion, vector literals, slots, the trigram probe and the judge's measured floor.

These are the pure parts of hybrid recall -- no database, no model -- so they run in seconds and pin the arithmetic
that decides which memory a caller sees first.
"""

import math

import pytest

from custom_memory_engine.engine.hybrid import (
    EMBEDDING_SLOTS,
    JUDGE_CANDIDATES,
    JUDGE_RELEVANCE_FLOOR,
    RRF_CONSTANT,
    ArmRanking,
    RecallError,
    RecallQuery,
    arm_depth,
    fuse,
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


# ------------------------------------------------------------------------------- the relevance judge (V2-D104)


def test_the_judge_floor_sits_between_the_weakest_answer_and_the_strongest_non_answer() -> None:
    """`JUDGE_RELEVANCE_FLOOR` is a *measured* operating point, pinned against the measurement it came from.

    The extremes are read from `reports/retrieval/evaluation.json`, which the retrieval-evaluation test regenerates
    from the real read path — so this is not a copy of a number, it is the number asserting the relation that justifies
    the constant. Re-measure and the file changes; set the constant outside the recorded gap and this fails.
    """
    import json
    from pathlib import Path


    report = json.loads(
        (Path(__file__).resolve().parents[1] / "reports" / "retrieval" / "evaluation.json").read_text("utf-8")
    )
    judge = report["judge"]
    assert judge["measured"] is True, "the report holds no judge measurement; run the retrieval evaluation"
    weakest_answer = judge["weakest_answerable"]
    strongest_non_answer = judge["strongest_unanswerable"]
    assert strongest_non_answer < JUDGE_RELEVANCE_FLOOR < weakest_answer, (strongest_non_answer, weakest_answer)
    # the constant is the midpoint of the two extremes: the point with the most slack on both sides, which is the only
    # definition of "best" that does not presuppose a cost ratio the labelled set cannot supply
    assert JUDGE_RELEVANCE_FLOOR == pytest.approx((weakest_answer + strongest_non_answer) / 2, abs=0.1)
    # the separation is wide enough to be a finding rather than a coincidence: > 2 logits on the tight side
    assert min(weakest_answer - JUDGE_RELEVANCE_FLOOR, JUDGE_RELEVANCE_FLOOR - strongest_non_answer) > 1.0
    assert JUDGE_CANDIDATES > 0


def test_the_judge_is_a_protocol_a_double_can_satisfy() -> None:
    """The engine takes an injected judge, so the seam must be structural: no base class to inherit."""
    from custom_memory_engine.engine.hybrid import RelevanceJudge

    class _Judge:
        model = "fake-judge"

        async def relevance(self, query: str, documents) -> list[float]:
            return [0.0 for _ in documents]

    assert isinstance(_Judge(), RelevanceJudge)
    assert not isinstance(object(), RelevanceJudge)


# ------------------------------------------------------- function-word pruning (V2-D100, `informative_terms`)


def test_the_pruning_statistic_selects_the_longest_terms_and_is_language_agnostic() -> None:
    """The candidate terms the document-frequency probe asks about: the longest, deduplicated, per script.

    Length is the only proxy this module may use for informativeness — a stopword list would be language-specific —
    so the *order* matters: a question with more than `MAX_STATISTIC_TERMS` tokens probes its longest ones, and a
    two-letter Latin token is dropped while a two-letter Arabic or CJK token is kept, because there it is a word.
    """
    from custom_memory_engine.engine.hybrid import MAX_STATISTIC_TERMS, statistic_terms

    assert statistic_terms("what is the wifi password at the office")[:3] == ("password", "office", "what")
    assert "is" not in statistic_terms("what is the wifi password at the office")
    # non-ASCII two-character tokens survive; ASCII ones do not
    assert "في" in statistic_terms("في البيت")
    assert "in" not in statistic_terms("in the house")
    # bounded, deterministic, deduplicated
    many = statistic_terms("alpha beta gamma delta epsilon zeta eta theta")
    assert len(many) == MAX_STATISTIC_TERMS
    assert statistic_terms("coffee coffee coffee") == ("coffee",)
