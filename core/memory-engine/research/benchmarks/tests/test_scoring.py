"""Synthetic official-scorer compatibility and independently computed score checks."""

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from research.benchmarks import scoring
from research.benchmarks._official import locomo, longmemeval

OFFICIAL = Path(scoring.__file__).parent / "_official"


def _official_functions(source: Path, names: tuple[str, ...], imports: dict[str, object]) -> ModuleType:
    """Execute only reviewed pure functions, never an upstream import or CLI node."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions: list[ast.stmt] = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {node.name for node in functions if isinstance(node, ast.FunctionDef)} == set(names)
    namespace = ModuleType("pinned_synthetic_reference")
    namespace.__dict__.update(imports)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace.__dict__)
    return namespace


def test_recorded_source_bytes_and_selected_function_asts_match_pins() -> None:
    manifest = json.loads((OFFICIAL / "source_manifest.json").read_text(encoding="utf-8"))
    for reference in manifest["sources"]:
        source = OFFICIAL / reference["archive_path"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == reference["sha256"]
        original = ast.parse(source.read_text(encoding="utf-8"))
        vendored = ast.parse((OFFICIAL / reference["module_path"]).read_text(encoding="utf-8"))
        for name in reference["selected_functions"]:
            expected = next(node for node in original.body if isinstance(node, ast.FunctionDef) and node.name == name)
            actual = next(node for node in vendored.body if isinstance(node, ast.FunctionDef) and node.name == name)
            assert ast.dump(actual, include_attributes=False) == ast.dump(expected, include_attributes=False)
        assert (
            hashlib.sha256((OFFICIAL / reference["license_path"]).read_bytes()).hexdigest()
            == reference["license_sha256"]
        )


@pytest.mark.parametrize("index", [0, 1])
def test_legacy_stemming_algorithm_is_exactly_the_pinned_nltk_class(index: int) -> None:
    manifest = json.loads((OFFICIAL / "source_manifest.json").read_text(encoding="utf-8"))
    reference = manifest["algorithm_dependencies"][index]
    source = OFFICIAL / reference["archive_path"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == reference["sha256"]
    expected = next(n for n in ast.parse(source.read_bytes()).body if isinstance(n, ast.ClassDef))
    vendored = OFFICIAL / reference["module_path"]
    actual = next(n for n in ast.parse(vendored.read_bytes()).body if isinstance(n, ast.ClassDef))
    assert ast.dump(actual, include_attributes=False) == ast.dump(expected, include_attributes=False)
    assert (
        hashlib.sha256((OFFICIAL / reference["license_path"]).read_bytes()).hexdigest() == reference["license_sha256"]
    )


@pytest.mark.parametrize(
    ("category", "gold", "prediction", "expected"),
    [
        (1, "red, blue", "red", 0.5),
        (1, "red, blue", "blue, red, green", 1.0),
        (1, "red, blue", "green", 0.0),
        (2, "cat cat dog", "cat dog dog", 2 / 3),
        (2, "The cats and dogs!", "cat dog", 1.0),
        (2, "abc,def", "abcdef", 1.0),
        (2, "blue whale", "blue", 2 / 3),
        (2, "", "", 0.0),
        (3, "Oslo; additional annotation", "Oslo", 1.0),
        (4, "growing flowers", "grow flower", 1.0),
        (4, "green", "", 0.0),
        (5, "", "No information available.", 1.0),
        (5, "", "That was NOT MENTIONED here.", 1.0),
        (5, "", "I don't know", 0.0),
        (5, "", "not-mentioned", 0.0),
    ],
)
def test_official_locomo_category_scores_match_independent_examples(
    category: int, gold: str, prediction: str, expected: float, capsys: pytest.CaptureFixture[str]
) -> None:
    assert scoring.score_locomo(category, gold, prediction) == pytest.approx(expected)
    assert capsys.readouterr().out == ""


def test_locomo_wrapper_matches_unmodified_pinned_function() -> None:
    reference = _official_functions(
        OFFICIAL / "sources/locomo_evaluation.py.txt",
        ("normalize_answer", "f1_score", "f1", "eval_question_answering"),
        {"Counter": locomo.Counter, "string": locomo.string, "regex": locomo.regex, "np": locomo.np, "ps": locomo.ps},
    )
    for category in range(1, 6):
        for gold, prediction in (("cats, dogs", "cat"), ("red red", "red"), ("Oslo; note", "Oslo"), ("", "")):
            expected = reference.eval_question_answering(
                [{"category": category, "answer": gold, "prediction": prediction, "evidence": []}]
            )[0][0]
            assert scoring.score_locomo(category, gold, prediction) == pytest.approx(float(expected))


@pytest.mark.parametrize("category", [0, 6, True, "1", None])
def test_invalid_locomo_category_is_rejected(category: object) -> None:
    with pytest.raises(ValueError, match="category"):
        scoring.score_locomo(category, "gold", "prediction")  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["gold", "prediction"])
def test_locomo_requires_string_boundary_inputs(field: str) -> None:
    with pytest.raises(TypeError, match="string"):
        scoring.score_locomo(1, None if field == "gold" else "x", None if field == "prediction" else "x")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "task",
    [
        "single-session-user",
        "single-session-assistant",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
        "single-session-preference",
    ],
)
@pytest.mark.parametrize("abstention", [False, True])
def test_longmemeval_prompts_match_actual_pinned_source(task: str, abstention: bool) -> None:
    reference = _official_functions(OFFICIAL / "sources/longmemeval_evaluate_qa.py.txt", ("get_anscheck_prompt",), {})
    expected = reference.get_anscheck_prompt(task, "synthetic question?", "gold", "answer", abstention=abstention)
    assert (
        scoring.get_longmemeval_prompt(task, "synthetic question?", "gold", "answer", abstention=abstention)
        == expected
    )


def test_longmemeval_sends_exact_prompt_to_only_the_injected_judge() -> None:
    prompts: list[str] = []

    def local_judge(prompt: str) -> str:
        prompts.append(prompt)
        return " YES \n"

    score = scoring.score_longmemeval(
        "knowledge-update",
        "synthetic question?",
        "updated fact",
        "old then updated fact",
        abstention=False,
        judge=local_judge,
    )
    assert score == 1.0
    assert prompts == [
        longmemeval.get_anscheck_prompt(
            "knowledge-update",
            "synthetic question?",
            "updated fact",
            "old then updated fact",
            abstention=False,
        )
    ]


@pytest.mark.parametrize(("reply", "expected"), [("no", 0.0), ("", 0.0), ("YES", 1.0), ("yesterday", 1.0)])
def test_longmemeval_legacy_yes_substring_rule_is_explicit(reply: str, expected: float) -> None:
    assert (
        scoring.score_longmemeval(
            "multi-session", "question?", "gold", "prediction", abstention=False, judge=lambda _: reply
        )
        == expected
    )


def test_longmemeval_rejects_unknown_task_before_judge_dispatch() -> None:
    def forbidden(_: str) -> str:
        pytest.fail("judge called for unsupported task")

    with pytest.raises(ValueError, match="question_type"):
        scoring.score_longmemeval("unknown", "question?", "gold", "prediction", abstention=False, judge=forbidden)


def test_longmemeval_rejects_nonstring_judge_response() -> None:
    with pytest.raises(TypeError, match="judge_response"):
        scoring.score_longmemeval(
            "multi-session",
            "question?",
            "gold",
            "prediction",
            abstention=False,
            judge=lambda _: None,  # type: ignore[arg-type]
        )


def test_import_never_loads_provider_or_bert_modules() -> None:
    script = """
import sys
from research.benchmarks import scoring
forbidden = {'openai', 'backoff', 'bert_score', 'torch', 'transformers', 'rouge', 'nltk'}
assert not any(name.split('.')[0] in forbidden for name in sys.modules)
assert scoring.score_locomo(2, 'cats', 'cat') == 1.0
"""
    completed = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
