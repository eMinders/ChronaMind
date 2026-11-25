"""The model-selection dataset and its scorers, importable by tests as well as by the harness.

They live here rather than inside `scripts/eval_models.py` for one reason: `tests/` may not import `scripts/` (a
boundary rule with a test behind it), and a measuring instrument nobody can unit-test is how the `quote_grounded`
argument order stayed wrong until it had already ranked three models.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from custom_memory_engine.semantic.slots import normalize_slot

#: Every task the engine asks a model to do. A candidate is scored per task, never as one aggregate: a model that
#: wins extraction can lose adjudication, and picking one for both is how a deployment ends up with a pipeline that
#: is subtly wrong in one stage nobody measured.
#: `query_rewrite` is measured but **advisory**, and deliberately not part of the selection: the only honest measure
#: of a rewrite is whether it retrieves the right memory, which needs the retrieval harness. Scoring it by string
#: overlap here would rank models on their phrasing rather than on their usefulness.
TASKS = ("extraction_en", "extraction_ar", "extraction_mixed", "schema", "adjudication", "query_rewrite")


@dataclass(frozen=True, slots=True)
class Case:
    """One realistic memory-engine input, with what a correct answer must contain."""

    id: str
    text: str
    timestamp: str
    language: str
    #: Substrings that a correct extraction must produce in some claim's `content` (case-insensitive).
    must_extract: tuple[str, ...]
    #: Quote fragments that, if returned as a quote, must appear verbatim in the text (the grounding check itself).
    must_quote: tuple[str, ...] = ()
    #: For adjudication: `(new, existing, expected_relation)`.
    adjudication: tuple[str, str, str] | None = None
    #: For query rewriting: the memory the rewritten query must retrieve.
    rewrite_target: str | None = None


ENGLISH: tuple[Case, ...] = (
    Case(
        "en_move",
        "I moved to Berlin last spring, so my address changed.",
        "2026-03-10T09:00:00+00:00",
        "en",
        ("berlin",),
        ("I moved to Berlin last spring",),
    ),
    Case(
        "en_allergy",
        "Please remember I am allergic to peanuts and shellfish.",
        "2026-03-11T18:30:00+00:00",
        "en",
        ("allerg", "peanut", "shellfish"),
        ("allergic to peanuts",),
    ),
    Case(
        "en_employer",
        "I just started at Northwind Traders as a data engineer, I left Acme in January.",
        "2026-02-02T08:00:00+00:00",
        "en",
        ("northwind",),
        ("Northwind Traders",),
    ),
    Case(
        "en_preference",
        "Honestly iced americano is the only coffee I drink these days.",
        "2026-03-12T07:15:00+00:00",
        "en",
        ("coffee", "americano"),
        ("iced americano",),
    ),
    Case(
        "en_nothing",
        "What is the capital of France?",
        "2026-03-12T09:00:00+00:00",
        "en",
        (),
    ),
    Case(
        "en_negation",
        "I do not eat fish, not even sushi.",
        "2026-03-13T20:00:00+00:00",
        "en",
        ("fish",),
        ("do not eat fish",),
    ),
)

ARABIC: tuple[Case, ...] = (
    Case(
        "ar_residence",
        "انتقلت إلى القاهرة في يناير الماضي، وأبحث الآن عن شقة قريبة من العمل.",
        "2026-03-10T09:00:00+00:00",
        "ar",
        ("القاهرة",),
        ("انتقلت إلى القاهرة",),
    ),
    Case(
        "ar_allergy",
        "تذكر أنني أعاني من حساسية من المكسرات، خصوصًا الفول السوداني.",
        "2026-03-11T18:30:00+00:00",
        "ar",
        ("حساسية", "مكسرات"),
        ("حساسية من المكسرات",),
    ),
    Case(
        "ar_preference",
        "أفضل الاجتماعات في الصباح الباكر قبل التاسعة.",
        "2026-03-12T07:15:00+00:00",
        "ar",
        ("الصباح",),
        ("الصباح الباكر",),
    ),
    Case(
        "ar_nothing",
        "ما هي عاصمة فرنسا؟",
        "2026-03-12T09:00:00+00:00",
        "ar",
        (),
    ),
)

MIXED: tuple[Case, ...] = (
    Case(
        "mix_arabizi",
        "أنا بستخدم VS Code و PyCharm، بس مش بحب Vim خالص.",
        "2026-03-14T11:00:00+00:00",
        "ar",
        ("vs code", "pycharm"),
        ("VS Code",),
    ),
    Case(
        "mix_switch",
        "My manager in القاهرة says the deadline is الأحد القادم.",
        "2026-03-15T10:00:00+00:00",
        "ar",
        ("deadline", "القاهرة"),
        ("deadline is الأحد القادم",),
    ),
    Case(
        "mix_code",
        "I hate Vim, never open it again, I only use VS Code.",
        "2026-03-16T12:00:00+00:00",
        "en",
        ("vim",),
        ("I hate Vim",),
    ),
)

ADJUDICATION: tuple[Case, ...] = (
    Case(
        "adj_contradiction",
        "",
        "",
        "",
        (),
        adjudication=("I live in Berlin.", "I live in Munich.", "contradiction"),
    ),
    Case(
        "adj_duplicate",
        "",
        "",
        "",
        (),
        adjudication=("I live in Berlin.", "My home is in Berlin.", "duplicate"),
    ),
    Case(
        "adj_update",
        "",
        "",
        "",
        (),
        adjudication=("I moved to Munich.", "I live in Berlin.", "update"),
    ),
    Case(
        "adj_unrelated",
        "",
        "",
        "",
        (),
        adjudication=("I am allergic to peanuts.", "I live in Berlin.", "unrelated"),
    ),
    Case(
        "adj_ar_contradiction",
        "",
        "",
        "",
        (),
        adjudication=("أنا أسكن في برلين.", "أنا أسكن في ميونخ.", "contradiction"),
    ),
    Case(
        "adj_ar_unrelated",
        "",
        "",
        "",
        (),
        adjudication=("أنا أعاني من حساسية الفول السوداني.", "أنا أسكن في برلين.", "unrelated"),
    ),
)

REWRITE: tuple[Case, ...] = (
    Case(
        "rw_en",
        "where am I based these days?",
        "",
        "en",
        (),
        rewrite_target="I moved to Berlin last spring",
    ),
    Case(
        "rw_ar",
        "وين أسكن حالياً؟",
        "",
        "ar",
        (),
        rewrite_target="انتقلت إلى القاهرة",
    ),
)


#: A second block added for `V2-D102`. The first 20 cases were enough to choose a model for *extraction*; they were not
#: enough to separate the candidates on the three things that actually differ in a deployment: entities (a mention has
#: to be named, and named the way the text writes it), facets (the user-model facet is what routes an artifact into the
#: evolving user model, and a model that never emits one leaves that half of the schema unpopulated), and
#: contradictions phrased the way a person phrases them rather than as bare assertion pairs.
ENGLISH_V2: tuple[Case, ...] = (
    Case(
        "en2_employer",
        "I started at Northwind Traders in February as a data engineer.",
        "2026-02-14T10:00:00+00:00",
        "en",
        ("northwind", "data engineer"),
    ),  # fmt: skip
    Case(
        "en2_pronoun",
        "We adopted a dog called Rufus last weekend.",
        "2026-04-02T16:00:00+00:00",
        "en",
        ("rufus", "dog"),
    ),  # fmt: skip
    Case(
        "en2_negation", "I don't drink coffee any more, only tea.", "2026-05-01T08:00:00+00:00", "en", ("coffee",)
    ),  # fmt: skip
    Case(
        "en2_number",
        "The flat is 74 square metres and the rent is 1450 euros.",
        "2026-01-09T12:00:00+00:00",
        "en",
        ("74", "1450"),
    ),  # fmt: skip
    Case(
        "en2_name_change",
        "My new manager is Priya, she replaced Tom in March.",
        "2026-03-20T09:30:00+00:00",
        "en",
        ("priya",),
    ),  # fmt: skip
    Case(
        "en2_location_rel",
        "My brother Karim moved to Lisbon and works remotely.",
        "2026-06-11T14:00:00+00:00",
        "en",
        ("karim", "lisbon"),
    ),  # fmt: skip
    Case(
        "en2_constraint",
        "Never schedule anything for me on Friday afternoons.",
        "2026-02-02T11:00:00+00:00",
        "en",
        ("friday",),
    ),  # fmt: skip
    Case(
        "en2_preference",
        "I would rather get a short answer than a long one.",
        "2026-07-07T19:00:00+00:00",
        "en",
        ("short",),
    ),  # fmt: skip
)

ARABIC_V2: tuple[Case, ...] = (
    Case(
        "ar2_employer",
        "بدأت العمل في شركة النور في يناير كمهندس برمجيات.",
        "2026-01-15T08:00:00+00:00",
        "ar",
        ("النور",),
    ),  # fmt: skip
    Case(
        "ar2_family", "أمي تسكن في عمّان وتزورنا في رمضان.", "2026-03-01T20:00:00+00:00", "ar", ("عمّان",)
    ),  # fmt: skip
    Case(
        "ar2_negation", "لم أعد أشرب القهوة، أشرب الشاي فقط.", "2026-04-04T07:30:00+00:00", "ar", ("القهوة", "الشاي")
    ),  # fmt: skip
    Case(
        "ar2_number", "الإيجار ١٤٥٠ يورو شهريًا والشقة ٧٤ مترًا.", "2026-02-20T13:00:00+00:00", "ar", ("١٤٥٠",)
    ),  # fmt: skip
    Case(
        "ar2_place", "سافرت إلى دبي في مايو لحضور مؤتمر تقني.", "2026-05-19T09:00:00+00:00", "ar", ("دبي",)
    ),  # fmt: skip
    Case(
        "ar2_constraint", "لا تحجز لي أي شيء يوم الجمعة بعد الظهر.", "2026-02-02T11:05:00+00:00", "ar", ("الجمعة",)
    ),  # fmt: skip
)

MIXED_V2: tuple[Case, ...] = (
    Case(
        "mx2_arabizi",
        "Bossi 3ayez report el 3asher el gai, ya3ni next Sunday.",
        "2026-06-01T15:00:00+00:00",
        "mixed",
        ("report",),
    ),  # fmt: skip
    Case(
        "mx2_code_switch",
        "المشروع اسمه Atlas، والـ deadline يوم 15 نوفمبر.",
        "2026-09-09T10:00:00+00:00",
        "mixed",
        ("atlas", "نوفمبر"),
    ),  # fmt: skip
    Case(
        "mx2_transliteration",
        "My landlord is called Herr Weber, sa7eb el shaqqa.",
        "2026-03-03T17:20:00+00:00",
        "mixed",
        ("weber",),
    ),  # fmt: skip
)

ADJUDICATION_V2: tuple[Case, ...] = (
    # a move: `update` in the model's vocabulary ("a later value of the same attribute"). `supersede` is the engine's
    # *strategy* name, not a relation a model may emit -- the first version of this block used it, and every candidate
    # was scored wrong for answering "update", which is correct.
    Case("adj2_move", "", "", "", (), adjudication=("I live in Munich now", "I live in Berlin", "update")),
    Case(
        "adj2_reinforce_ar",
        "",
        "",
        "",
        (),
        adjudication=("أنا نباتي ولا آكل اللحوم", "أنا نباتي ولا آكل السمك", "duplicate"),
    ),  # fmt: skip
    Case(
        "adj2_unrelated",
        "",
        "",
        "",
        (),
        adjudication=("I bought a new laptop", "I prefer dark roast coffee", "unrelated"),
    ),  # fmt: skip
    Case(
        "adj2_refinement",
        "",
        "",
        "",
        (),
        # the new statement is narrower than the old one: it does not contradict it, so it is the same belief said
        # more precisely -- `duplicate`, not a new value
        adjudication=("I work remotely three days a week", "I work remotely", "duplicate"),
    ),  # fmt: skip
    Case(
        "adj2_time_change",
        "",
        "",
        "",
        (),
        adjudication=("The standup moved to eleven", "The daily standup is at nine thirty", "update"),
    ),  # fmt: skip
    Case(
        "adj2_negation",
        "",
        "",
        "",
        (),
        adjudication=("I stopped eating fish", "I am vegetarian and I do not eat fish", "duplicate"),
    ),  # fmt: skip
    Case(
        "adj2_cross_lang", "", "", "", (), adjudication=("I moved to Berlin", "انتقلت إلى برلين", "duplicate")
    ),  # fmt: skip
    Case(
        "adj2_different_person",
        "",
        "",
        "",
        (),
        adjudication=("Sara likes dark roast", "I prefer dark roast coffee", "unrelated"),
    ),  # fmt: skip
)


ALL_CASES: tuple[Case, ...] = (
    ENGLISH + ARABIC + MIXED + ADJUDICATION + REWRITE + ENGLISH_V2 + ARABIC_V2 + MIXED_V2 + ADJUDICATION_V2
)


def overlap(left: str, right: str) -> float:
    """Share of the target's character trigrams that appear in the rewrite. Symmetric enough, and script-neutral."""
    target = trigrams(right)
    if not target:
        return 0.0
    return len(target & trigrams(left)) / len(target)


def trigrams(text: str) -> set[str]:
    folded = " ".join(text.casefold().split())
    return {folded[index : index + 3] for index in range(max(0, len(folded) - 2))}


def schema_ok(document: Any) -> tuple[bool, str]:
    """Every claim must satisfy the fields the ingestor actually reads, with the types it requires."""
    if not isinstance(document, Mapping):
        return False, "not_an_object"
    claims = document.get("claims")
    if not isinstance(claims, list):
        return False, "no_claims_array"
    for claim in claims:
        if not isinstance(claim, Mapping):
            return False, "claim_not_an_object"
        if not isinstance(claim.get("content"), str) or not claim["content"].strip():
            return False, "content_missing"
        if not isinstance(claim.get("quote"), str) or not claim["quote"].strip():
            return False, "quote_missing"
        if claim.get("cardinality") not in (None, "single", "multi", "event"):
            return False, "cardinality_invalid"
        slot = claim.get("semantic_slot")
        if slot is not None:
            try:
                normalize_slot(slot)
            except Exception:
                return False, "slot_unrepresentable"
        confidence = claim.get("confidence")
        if confidence is not None and not (isinstance(confidence, int | float) and 0.0 <= float(confidence) <= 1.0):
            return False, "confidence_invalid"
    return True, ""
