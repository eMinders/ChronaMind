"""Local wrappers around pinned official QA scorers; no provider transport.

The injected LongMemEval judge must be local and uncharged. RL-033 owns actual
budgeted provider dispatch and judge validation. Synthetic judge replies prove
prompt/response plumbing only, never scientific judge accuracy.
"""

from collections.abc import Callable
from math import isfinite

from research.benchmarks._official.locomo import eval_question_answering
from research.benchmarks._official.longmemeval import get_anscheck_prompt

LONGMEMEVAL_TASKS = frozenset(
    {
        "single-session-user",
        "single-session-assistant",
        "single-session-preference",
        "multi-session",
        "knowledge-update",
        "temporal-reasoning",
    }
)

SCORER_PINS = {
    "LongMemEval": {
        "repository": "xiaowu0162/LongMemEval",
        "commit": "9e0b455f4ef0e2ab8f2e582289761153549043fc",
        "path": "src/evaluation/evaluate_qa.py",
        "sha256": "ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251",
        "license": "MIT",
    },
    "LoCoMo": {
        "repository": "snap-research/locomo",
        "commit": "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376",
        "path": "task_eval/evaluation.py",
        "sha256": "8e3be5d57ff2ff9ec5cd05939592f468c5f3f1fd95d13e431932bdf6bf0fd6fd",
        "license": "CC-BY-NC-4.0",
    },
    "PorterStemmer": {
        "repository": "nltk/nltk",
        "commit": "63a63b1a7793aceceb1efc23bc1fd87e7095f9d5",
        "release_tag": "3.8.1",
        "path": "nltk/stem/porter.py",
        "sha256": "40797f3ce2d69f3a8d816eaef26d0d9a0cb0b373b77230a7e2de39a6f1e25ea6",
        "license": "Apache-2.0",
        "policy": "exact legacy PorterStemmer and StemmerI classes; no full NLTK import",
    },
}


def scorer_provenance() -> dict[str, dict[str, str]]:
    """Return fresh metadata; callers cannot mutate the next manifest's pins."""
    return {name: dict(pins) for name, pins in SCORER_PINS.items()}


def _strings(**values: str) -> None:
    if any(not isinstance(value, str) for value in values.values()):
        raise TypeError("scorer_string_inputs_required")


def get_longmemeval_prompt(
    question_type: str,
    question: str,
    gold_answer: str,
    hypothesis: str,
    *,
    abstention: bool = False,
) -> str:
    """Return the exact pinned task/abstention prompt without importing its CLI."""
    _strings(question_type=question_type, question=question, gold_answer=gold_answer, hypothesis=hypothesis)
    if type(abstention) is not bool:
        raise TypeError("boolean_abstention_required")
    if not abstention and question_type not in LONGMEMEVAL_TASKS:
        raise ValueError("unsupported_longmemeval_question_type")
    return get_anscheck_prompt(question_type, question, gold_answer, hypothesis, abstention=abstention)


def score_longmemeval(
    question_type: str,
    question: str,
    gold_answer: str,
    hypothesis: str,
    *,
    abstention: bool,
    judge: Callable[[str], str],
) -> float:
    """Replicate the official CLI's case-insensitive ``yes`` substring label.

    This intentionally accepts replies such as "yesterday", just as the pinned
    script does. A stricter label parser is a different measuring instrument,
    requiring a separately documented and validated RL-033 judge implementation.
    """
    prompt = get_longmemeval_prompt(question_type, question, gold_answer, hypothesis, abstention=abstention)
    if not callable(judge):
        raise TypeError("local_judge_callable_required")
    reply = judge(prompt)
    if not isinstance(reply, str):
        raise TypeError("string_judge_response_required")
    return float("yes" in reply.strip().lower())


def score_locomo(category: int, gold_answer: str, hypothesis: str) -> float:
    """Return exact official category QA scoring, excluding legacy recall defaults.

    Category 1 is comma-separated partial F1; 2–4 use stemmed token F1 (3 trims
    the first semicolon suffix); 5 checks the official literal abstention phrases.
    Empty string predictions/gold retain upstream behavior, including a zero
    empty/empty F1. Project retrieval missingness is handled by the caller.
    """
    if type(category) is not int or category not in range(1, 6):
        raise ValueError("locomo_numeric_category_1_to_5_required")
    _strings(gold_answer=gold_answer, hypothesis=hypothesis)
    # The pinned scorer always indexes answer, including category 5. The caller
    # may supply an empty placeholder there; no adversarial option is fabricated.
    scores, _, _legacy_recall = eval_question_answering(
        [{"category": category, "answer": gold_answer, "prediction": hypothesis, "evidence": []}]
    )
    result = float(scores[0])
    if not isfinite(result) or not 0 <= result <= 1:
        raise ValueError("invalid_official_qa_score")
    return result
