"""The retrieval evaluation set: a corpus, labelled questions, and the metrics that score them.

Retrieval quality is the one property that cannot be argued. The earlier set (16 questions, three configurations)
settled whether the dense arm earns its place; this one is large enough to decide the *ranking* questions that were
left open — whether a BM25-style term weighting or a second-stage reranker beats Reciprocal Rank Fusion on candidates
the engine already authorizes.

**What is in the corpus.** 49 memories, written the way a person writes: English, Arabic and mixed-script, with
deliberate near-duplicates (two different people's coffee preference, two cities, a superseded address) and distractors
that share vocabulary with the questions but answer nothing. A careless ranker returns the wrong one of each pair.

**What the labels mean.** Every question names the one memory that answers it, and a ``kind``:

``lexical``      a question that shares words with its answer — any arm can find it
``paraphrase``   a question that shares *none* of its content words with the answer — only meaning can find it
``cross_script`` the question is in another script (Arabic ↔ English) from the answer — only meaning can find it
``no_match``     nothing in the corpus answers it; the correct result is an empty page

The ``no_match`` rows are what keep a ranking from being scored on recall alone: an engine that answers everything
scores
perfectly on recall and fails the only question that matters in production.

**Metric.** ``recall@5`` (was the answer in the five returned rows), ``top1`` (was it first), ``mrr@5`` and ``ndcg@5``
(the ranking-sensitive one: nDCG at 5 with a single relevant document is ``1/log2(rank + 1)``), plus
``no_match_fp`` — the share of ``no_match`` questions that returned anything at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import log2
from typing import Literal

type QueryKind = Literal["lexical", "paraphrase", "cross_script", "no_match"]
KINDS: tuple[QueryKind, ...] = ("lexical", "paraphrase", "cross_script", "no_match")


@dataclass(frozen=True, slots=True)
class Memory:
    """One memory in the corpus. ``key`` is the label a query names; ids are assigned when the corpus is seeded."""

    key: str
    text: str
    #: Which language the memory itself is written in: ``en``, ``ar`` or ``mixed``.
    language: str


@dataclass(frozen=True, slots=True)
class Question:
    """One labelled question: what the user asks, which memory answers it, and what kind of question it is."""

    text: str
    answer: str | None
    kind: QueryKind
    language: str


#: 48 memories. Cross-script pairs sit next to each other on purpose (`residence_en`/`residence_ar`, ...) so a
#: cross-script question has exactly one right answer and its sibling is a decoy that a lexical arm will prefer.
MEMORIES: tuple[Memory, ...] = (
    Memory("residence_en", "I moved to Berlin last spring, so my address changed", "en"),
    Memory("residence_ar", "انتقلت إلى برلين في الربيع الماضي، لذلك تغيّر عنواني", "ar"),
    Memory("residence_old", "I used to live in Munich before the move", "en"),
    Memory("meeting_en", "Do not book me anything before ten in the morning", "en"),
    Memory("meeting_ar", "لا تحجز لي أي اجتماع قبل الساعة العاشرة صباحًا", "ar"),
    Memory("meeting_why", "I need the first two hours of the day to think without interruption", "en"),
    Memory("coffee_en", "I prefer dark roast coffee with no sugar", "en"),
    Memory("coffee_ar", "أفضّل القهوة المحمصة الداكنة بدون سكر", "ar"),
    Memory("coffee_other", "Sara drinks her coffee with oat milk", "en"),
    Memory("tea", "I only drink tea after four in the afternoon", "en"),
    Memory("diet_en", "I am vegetarian and I do not eat fish", "en"),
    Memory("diet_ar", "أنا نباتي ولا آكل السمك", "ar"),
    Memory("allergy", "I am allergic to peanuts, so check the ingredients", "en"),
    Memory("editor", "I write code in Neovim with a Vim keymap", "en"),
    Memory("editor_language", "My editor is set up for Rust and Python", "en"),
    Memory("language_pref", "I prefer to be answered in Arabic when I write in Arabic", "en"),
    Memory("language_ar", "أفضّل أن ترد عليّ بالعربية عندما أكتب بالعربية", "ar"),
    Memory("deploy", "The deploy pipeline needs a manual approval before it runs", "en"),
    Memory("deploy_ar", "خط أنابيب النشر يحتاج موافقة يدوية قبل التشغيل", "ar"),
    Memory("oncall", "I am on call every second weekend", "en"),
    Memory("standup", "The daily standup is at nine thirty and lasts fifteen minutes", "en"),
    Memory("friday", "I do not work on Fridays", "en"),
    Memory("friday_ar", "لا أعمل يوم الجمعة", "ar"),
    Memory("project_alpha", "Project Alpha ships the billing migration in November", "en"),
    Memory("project_beta", "Project Beta is the mobile rewrite and it is paused", "en"),
    Memory("billing", "Billing runs on Stripe and the invoices are in euros", "en"),
    Memory("budget", "The infrastructure budget is capped at two thousand euros a month", "en"),
    Memory("gym", "I go to the gym at six in the evening", "en"),
    Memory("gym_ar", "أذهب إلى الصالة الرياضية في السادسة مساءً", "ar"),
    Memory("running", "I run ten kilometres every Sunday morning", "en"),
    Memory("sister", "My sister Leila lives in Cairo and visits in summer", "en"),
    Memory("sister_ar", "أختي ليلى تعيش في القاهرة وتزورنا في الصيف", "ar"),
    Memory("father", "My father is a retired teacher in Alexandria", "en"),
    Memory("passport", "My passport expires in March and the renewal takes six weeks", "en"),
    Memory("flight", "The flight to Berlin leaves at seven in the evening", "en"),
    Memory("hotel", "I booked a hotel near the station, not near the airport", "en"),
    Memory("car", "The car is in the garage and the brakes need replacing", "en"),
    Memory("rent", "The rent is due on the first of each month", "en"),
    Memory("landlord", "The landlord's name is Herr Weber and he answers by email", "mixed"),
    Memory("doctor", "Dr. Yusuf is my dentist and the clinic is on the second floor", "mixed"),
    Memory("dentist_ar", "موعد طبيب الأسنان عند الدكتور يوسف", "ar"),
    Memory("book", "I am reading a book about the history of the Ottoman Empire", "en"),
    Memory("book_ar", "أقرأ كتابًا عن تاريخ الدولة العثمانية", "ar"),
    Memory("podcast", "I listen to a podcast about databases while commuting", "en"),
    Memory("talk", "The conference talk about retrieval was the best one", "en"),
    Memory("deadline", "The quarterly report is due on the fifteenth", "en"),
    Memory("crypto", "I do not invest in cryptocurrency", "en"),
    Memory("piano", "My daughter plays the piano and practises after dinner", "en"),
    Memory("weather", "It rains in Berlin all through November", "en"),
)


#: 48 questions: 20 lexical, 12 paraphrase, 8 cross-script, 8 no-match. The paraphrase and cross-script rows are the
#: reason the dense arm exists; the no-match rows are the reason a similarity floor exists.
QUESTIONS: tuple[Question, ...] = (
    Question("where do I live now", "residence_en", "paraphrase", "en"),
    Question("which address did I change when I relocated", "residence_en", "paraphrase", "en"),
    Question("أين أعيش الآن", "residence_ar", "paraphrase", "ar"),
    Question("where did I live before moving to Berlin", "residence_old", "paraphrase", "en"),
    Question("when should nobody schedule a meeting for me", "meeting_en", "paraphrase", "en"),
    Question("ما هو الوقت الذي لا أحب أن يُحجز لي فيه اجتماع", "meeting_ar", "paraphrase", "ar"),
    Question("why do I protect the early morning", "meeting_why", "paraphrase", "en"),
    Question("how do I take my coffee", "coffee_en", "paraphrase", "en"),
    Question("كيف أفضل قهوتي", "coffee_ar", "paraphrase", "ar"),
    Question("what does Sara drink", "coffee_other", "paraphrase", "en"),
    Question("which foods are off the table for me", "diet_en", "paraphrase", "en"),
    Question("ما الأطعمة التي لا آكلها", "diet_ar", "paraphrase", "ar"),
    Question("what should be checked before I eat something", "allergy", "paraphrase", "en"),
    Question("what do I write code in", "editor", "paraphrase", "en"),
    Question("which languages is my editor configured for", "editor_language", "paraphrase", "en"),
    Question("بأي لغة أحب أن تكون الإجابة", "language_pref", "paraphrase", "ar"),
    Question("what must happen before a deployment", "deploy", "paraphrase", "en"),
    Question("ماذا يحتاج النشر قبل أن يعمل", "deploy_ar", "paraphrase", "ar"),
    Question("how often am I on call", "oncall", "paraphrase", "en"),
    Question("what time is my day off", "friday", "paraphrase", "en"),
    Question("ما هو يومي الذي لا أعمل فيه", "friday_ar", "paraphrase", "ar"),
    Question("where does my sister live", "sister", "paraphrase", "en"),
    Question("أين تعيش أختي", "sister_ar", "paraphrase", "ar"),
    Question("when does my passport run out", "passport", "paraphrase", "en"),
    Question("what is broken on the car", "car", "paraphrase", "en"),
    Question("what am I reading about", "book", "paraphrase", "en"),
    Question("عن ماذا أقرأ هذه الأيام", "book_ar", "paraphrase", "ar"),
    Question("what am I listening to on the train", "podcast", "paraphrase", "en"),
    Question("where do I want to stay when I travel", "hotel", "paraphrase", "en"),
    Question("where do I exercise", "gym", "paraphrase", "en"),
    Question("أين أتمرن", "gym_ar", "paraphrase", "ar"),
    Question("what does my father do", "father", "paraphrase", "en"),
    Question("when is the rent payable", "rent", "paraphrase", "en"),
    Question("who is my dentist", "doctor", "lexical", "mixed"),
    Question("موعد طبيب الأسنان", "dentist_ar", "lexical", "ar"),
    Question("what is the daily standup", "standup", "lexical", "en"),
    Question("billing provider", "billing", "lexical", "en"),
    Question("infrastructure budget", "budget", "lexical", "en"),
    Question("project alpha", "project_alpha", "lexical", "en"),
    Question("project beta", "project_beta", "lexical", "en"),
    Question("quarterly report deadline", "deadline", "lexical", "en"),
    Question("flight to Berlin", "flight", "lexical", "en"),
    Question("conference talk", "talk", "lexical", "en"),
    Question("piano practice", "piano", "lexical", "en"),
    Question("cryptocurrency", "crypto", "lexical", "en"),
    Question("weather in Berlin", "weather", "lexical", "en"),
    Question("where do I live", "residence_en", "lexical", "en"),
    # cross-script: the question and its answer are in different scripts, so only meaning can connect them
    Question("أين يقع عنواني الحالي", "residence_en", "cross_script", "ar"),
    Question("متى أستيقظ في الصباح للعمل", "meeting_why", "cross_script", "ar"),
    Question("هل أشرب القهوة بالسكر", "coffee_en", "cross_script", "ar"),
    Question("هل أنا نباتي", "diet_en", "cross_script", "ar"),
    Question("متى تزورني أختي", "sister", "cross_script", "ar"),
    Question("where is the gym I go to", "gym_ar", "cross_script", "en"),
    Question("which day do I not work", "friday_ar", "cross_script", "en"),
    Question("when is the deploy pipeline allowed to run", "deploy_ar", "cross_script", "en"),
    Question("coffee with no sugar", "coffee_en", "lexical", "en"),
    Question("what colour is the paint in my flat", None, "no_match", "en"),
    Question("جيراننا يشكون من الضجيج كل ليلة", None, "no_match", "ar"),  # noqa: RUF001
    Question("which visa do I need for Japan", None, "no_match", "en"),
    Question("my bicycle chain keeps slipping", None, "no_match", "en"),
    Question("what did the doctor say about my knee", None, "no_match", "en"),
    Question("how much do I owe the tax office", None, "no_match", "en"),
    Question("what is the wifi password at the office", None, "no_match", "en"),
    Question("من هو مدير المشروع الجديد", None, "no_match", "ar"),
)


@dataclass(frozen=True, slots=True)
class Score:
    """One configuration's result over the whole set, plus the per-question detail a failure needs to be explained."""

    name: str
    recall: float
    top1: float
    mrr: float
    ndcg: float
    no_match_fp: float
    #: ``(question, kind, expected, returned top-3)`` for every question the configuration got wrong.
    misses: tuple[tuple[str, str, str | None, tuple[str, ...]], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "configuration": self.name,
            "recall@5": round(self.recall, 4),
            "top1": round(self.top1, 4),
            "mrr@5": round(self.mrr, 4),
            "ndcg@5": round(self.ndcg, 4),
            "no_match_false_positive": round(self.no_match_fp, 4),
            "misses": len(self.misses),
        }


def score(name: str, ranked: dict[str, tuple[str, ...]]) -> Score:
    """Score one configuration. ``ranked`` maps a question's text to the answer keys it returned, best first.

    Read ``ndcg`` as the ranking-sensitive headline: with one relevant document per question it is
    ``1/log2(rank + 1)``, so position 1 scores 1.0, position 2 scores 0.63 and a miss scores 0.
    """
    hits = firsts = 0.0
    reciprocal = discounted = 0.0
    answerable = no_match = no_match_hits = 0
    misses: list[tuple[str, str, str | None, tuple[str, ...]]] = []
    for question in QUESTIONS:
        returned = ranked.get(question.text, ())
        if question.kind == "no_match":
            no_match += 1
            if not returned:
                no_match_hits += 1
            else:
                misses.append((question.text, question.kind, None, returned[:3]))
            continue
        answerable += 1
        position = returned.index(question.answer) + 1 if question.answer in returned else 0
        if position:
            hits += 1
            if position == 1:
                firsts += 1
            reciprocal += 1 / position
            discounted += 1 / log2(position + 1)
        else:
            misses.append((question.text, question.kind, question.answer, returned[:3]))
    return Score(
        name=name,
        recall=hits / answerable if answerable else 0.0,
        top1=firsts / answerable if answerable else 0.0,
        mrr=reciprocal / answerable if answerable else 0.0,
        ndcg=discounted / answerable if answerable else 0.0,
        no_match_fp=(no_match - no_match_hits) / no_match if no_match else 0.0,
        misses=tuple(misses),
    )


def by_kind(result: Score, ranked: dict[str, tuple[str, ...]]) -> dict[str, float]:
    """Recall per question kind, so a regression can be attributed to a capability rather than to an average."""
    shares: dict[str, float] = {}
    for kind in KINDS:
        rows = [question for question in QUESTIONS if question.kind == kind]
        if kind == "no_match":
            correct = sum(1 for question in rows if not ranked.get(question.text))
        else:
            correct = sum(1 for question in rows if question.answer in ranked.get(question.text, ()))
        shares[kind] = round(correct / len(rows), 4) if rows else 0.0
    del result  # the per-kind view is computed from the rankings, not from the aggregate
    return shares


__all__ = [
    "KINDS",
    "MEMORIES",
    "QUESTIONS",
    "Memory",
    "QueryKind",
    "Question",
    "Score",
    "by_kind",
    "score",
]
