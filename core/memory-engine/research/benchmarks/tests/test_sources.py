"""Synthetic public-source and harness integration; no released benchmark payloads."""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from evaluation.harness.config import load_config
from evaluation.harness.dummy import DummyAdapter
from evaluation.harness.models import AnswerResult, QuestionEvent, RetrievalResult, RetrievedItem, Usage
from research.benchmarks.common import evidence_recall, iter_records, manifest_digest
from research.benchmarks.locomo.adapter import LoCoMoAdapter
from research.benchmarks.longmemeval.adapter import LongMemEvalAdapter

ENGINE = Path(__file__).resolve().parents[3]


def lme(qid: str = "synthetic") -> dict[str, Any]:
    return {
        "question_id": qid,
        "question_type": "single-session-user",
        "question": "Which fictional color?",
        "answer": "red",
        "question_date": "2024/01/01 (Mon) 10:00",
        "haystack_session_ids": ["session"],
        "haystack_dates": ["2024/01/01 (Mon) 09:00"],
        "haystack_sessions": [[{"role": "user", "content": "Fictional color red.", "has_answer": True}]],
        "answer_session_ids": ["session"],
    }


def locomo() -> dict[str, Any]:
    return {
        "sample_id": "fictional-conversation",
        "conversation": {
            "speaker_a": "Person A",
            "speaker_b": "Person B",
            "session_1_date_time": "9:00 am on 1 January, 2024",
            "session_1": [
                {"speaker": "Person A", "dia_id": "D1:1", "text": "A fictional red object."},
                {"speaker": "Person B", "dia_id": "D1:2", "text": "Another fictional object."},
            ],
        },
        "qa": [
            {"question": "Which color?", "category": n, "answer": "red", "evidence": ["D1:1"]} for n in range(1, 6)
        ],
    }


def raw_file(tmp_path: Path, records: list[dict], filename: str) -> tuple[Path, Path, str]:
    path = tmp_path / filename
    raw = json.dumps(records).encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    manifest = tmp_path / "MANIFEST.txt"
    manifest.write_text(f"{digest}  {filename}\n", encoding="utf-8")
    return path, manifest, digest


def selection(tmp_path: Path, suite: str, digest: str, ids: list[str]) -> Path:
    path = tmp_path / "selection.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suite_id": suite,
                "split": "dev",
                "dataset_sha256": digest,
                "case_ids": ids,
                "retrieval_k": 2,
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=30)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=x@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
        timeout=30,
    )
    return tmp_path


class SyntheticB0(DummyAdapter):
    """Test-only fixed answer with no provider, baseline implementation or gold access."""

    def answer(self, user_id, question, as_of) -> AnswerResult:
        return AnswerResult(answer="red", usage=Usage.zero())


def run_source(source, repo: Path, selected: Path, run_id: str, **kwargs) -> Path:
    config = load_config(ENGINE / "evaluation/harness/fixtures/config.json")
    return source.run(
        selection_path=selected,
        split="dev",
        config=config,
        adapter_factory=SyntheticB0,
        run_id=run_id,
        output_root=repo / "runs",
        repo_root=repo,
        k=2,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("variant", "filename", "suite"),
    [
        ("oracle", "longmemeval_oracle.json", "LongMemEval_oracle"),
        ("s", "longmemeval_s_cleaned.json", "LongMemEval_S"),
        ("m", "longmemeval_m_cleaned.json", "LongMemEval_M"),
    ],
)
def test_longmemeval_all_variants_run_through_official_prompt_and_harness(
    source_repo: Path,
    variant,
    filename: str,
    suite: str,
) -> None:
    raw, manifest, digest = raw_file(source_repo, [lme()], filename)
    before = raw.read_bytes(), manifest.read_bytes()
    source = LongMemEvalAdapter(raw, variant=variant, manifest_path=manifest)
    selected = selection(source_repo, suite, digest, ["synthetic"])
    prompts = []

    def local_judge(prompt: str) -> str:
        prompts.append(prompt)
        return "yes"

    first = run_source(source, source_repo, selected, "first", judge=local_judge)
    second = run_source(source, source_repo, selected, "second", judge=local_judge)
    assert len(prompts) == 2 and "Which fictional color?" in prompts[0]
    assert (first / "answers.jsonl").read_bytes() == (second / "answers.jsonl").read_bytes()
    assert (first / "scores.jsonl").read_bytes() == (second / "scores.jsonl").read_bytes()
    scores = json.loads((first / "scores.jsonl").read_bytes())
    values = {m["name"]: m["value"] for m in scores["metrics"]}
    assert values["official_qa"] == 1.0
    assert values["primary_evidence_recall_at_k"] == (1.0 if variant == "s" else None)
    metadata = json.loads((first / "manifest.json").read_bytes())
    assert metadata["suite_id"] == suite and metadata["cluster_key"] == "question_id"
    assert metadata["benchmark_source"]["dataset_sha256"] == digest
    assert metadata["benchmark_source"]["audit"][0]["replay_session_indices"] == [0]
    assert metadata["benchmark_source"]["official_scorers"]["LoCoMo"]["commit"].startswith("3eb6")
    assert metadata["usage"]["cost_usd"] == "0"
    assert (raw.read_bytes(), manifest.read_bytes()) == before


def test_locomo_all_categories_use_official_scores_and_conversation_strata(source_repo: Path) -> None:
    raw, manifest, digest = raw_file(source_repo, [locomo()], "locomo10.json")
    source = LoCoMoAdapter(raw, manifest_path=manifest)
    selected = selection(source_repo, "LoCoMo", digest, ["fictional-conversation"])
    path = run_source(source, source_repo, selected, "locomo")
    rows = [json.loads(line) for line in (path / "scores.jsonl").read_text().splitlines()]
    assert [row["stratum"] for row in rows] == [f"category_{n}" for n in range(1, 6)]
    assert [{m["name"]: m["value"] for m in row["metrics"]}["official_qa"] for row in rows] == [1, 1, 1, 1, 0]
    assert len((path / "per_cluster.jsonl").read_text().splitlines()) == 5
    metadata = json.loads((path / "manifest.json").read_bytes())
    assert metadata["cluster_key"] == "conversation_id"
    assert metadata["benchmark_source"]["audit"][0]["adjusted_clock_count"] == 1
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == digest


def test_loader_selects_only_declared_case_and_rejects_missing_duplicate_ids(tmp_path: Path) -> None:
    raw, manifest, _ = raw_file(tmp_path, [lme("one"), lme("two")], "longmemeval_s_cleaned.json")
    source = LongMemEvalAdapter(raw, manifest_path=manifest)
    cases = list(source.iter_cases(("two",)))
    assert len(cases) == 1 and cases[0].cluster_id.startswith("lme:s:")
    assert "two" not in cases[0].cluster_id
    with pytest.raises(ValueError, match="not_found"):
        list(source.iter_cases(("absent",)))
    with pytest.raises(ValueError, match="distinct"):
        list(source.iter_cases(("one", "one")))
    raw, manifest, _ = raw_file(tmp_path, [lme(), lme()], "longmemeval_s_cleaned.json")
    with pytest.raises(ValueError, match="duplicate_benchmark_case"):
        list(source.iter_cases(("synthetic",)))


@pytest.mark.parametrize("text", ["{}", '[{"x":1,"x":2}]', "[1]", "[{}] {}", '[{"x":NaN}]', "["])
def test_streaming_input_rejects_ambiguous_or_invalid_json(tmp_path: Path, text: str) -> None:
    path = tmp_path / "raw.json"
    path.write_bytes(text.encode())
    with pytest.raises(ValueError):
        list(iter_records(path, hashlib.sha256(path.read_bytes()).hexdigest()))


@pytest.mark.parametrize(
    "text",
    [
        '[{"x":1,"x":2}]',
        '[{"a":{"x":1,"x":2}}]',
        '[{"a":[{"x":1,"x":2}]}]',
        '[{"a":{"b":[{"x":1,"x":2}]}}]',
        '[{"a":{"x":1,"x":2},"a":{}}]',
        r'[{"a":{"x":1,"\u0078":2}}]',
        '[{"ok":1},{"a":{"x":1,"x":2}}]',
        '[{"a":{"x":1,"x":2}},{"ok":1}]',
    ],
)
def test_duplicate_json_keys_raise_validation_errors_at_any_depth(tmp_path: Path, text: str) -> None:
    path = tmp_path / "raw.json"
    original = text.encode()
    path.write_bytes(original)
    with pytest.raises(ValueError, match="duplicate_json_key"):
        list(iter_records(path, hashlib.sha256(original).hexdigest()))
    assert path.read_bytes() == original


def test_streaming_json_accepts_repeated_keys_in_distinct_objects(tmp_path: Path) -> None:
    path = tmp_path / "raw.json"
    original = b'[{"a":{"x":1},"b":[{"x":2},{"x":3}]}]'
    path.write_bytes(original)
    assert list(iter_records(path, hashlib.sha256(original).hexdigest())) == [
        {"a": {"x": 1}, "b": [{"x": 2}, {"x": 3}]}
    ]
    assert path.read_bytes() == original


def test_hash_failures_never_repair_or_mutate_raw_inputs(tmp_path: Path) -> None:
    raw, manifest, _ = raw_file(tmp_path, [lme()], "longmemeval_s_cleaned.json")
    raw.write_bytes(raw.read_bytes() + b" ")
    before = raw.read_bytes(), manifest.read_bytes()
    with pytest.raises(ValueError, match="sha256"):
        list(LongMemEvalAdapter(raw, manifest_path=manifest).iter_cases(("synthetic",)))
    assert (raw.read_bytes(), manifest.read_bytes()) == before
    manifest.write_text("a" * 64 + " x\n" + "b" * 64 + " x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        manifest_digest(manifest, "x")


def test_source_test_guard_denies_before_any_source_or_selection_read(tmp_path: Path, monkeypatch) -> None:
    source = LongMemEvalAdapter(tmp_path / "not-read.json", manifest_path=tmp_path / "not-read.txt")
    config = load_config(ENGINE / "evaluation/harness/fixtures/config.json")

    def forbidden(*args, **kwargs):
        pytest.fail("read happened before authorization")

    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ValueError, match="confirmation_and_freeze"):
        source.run(
            selection_path=tmp_path / "missing-selection.json",
            split="test",
            config=config,
            adapter_factory=SyntheticB0,
            run_id="denied",
            output_root=tmp_path / "runs",
            repo_root=tmp_path,
            k=1,
        )
    assert not (tmp_path / "runs").exists()


def test_missing_evidence_is_unscored_and_primary_population_is_not_erased(source_repo: Path) -> None:
    item = lme()
    del item["haystack_sessions"][0][0]["has_answer"]
    raw, manifest, digest = raw_file(source_repo, [item], "longmemeval_s_cleaned.json")
    source = LongMemEvalAdapter(raw, manifest_path=manifest)
    selected = selection(source_repo, "LongMemEval_S", digest, ["synthetic"])
    path = run_source(source, source_repo, selected, "missing")
    scores = json.loads((path / "scores.jsonl").read_bytes())
    assert all(metric["value"] is None for metric in scores["metrics"])
    meta = json.loads((path / "manifest.json").read_bytes())["benchmark_source"]["audit"][0]
    assert meta["primary_recall_eligible_count"] == meta["missing_gold_evidence_count"] == 1


def test_evidence_recall_counts_unique_gold_links_in_top_k_memories() -> None:
    retrieved = RetrievalResult(
        items=(
            RetrievedItem(reference_id="m1", content="x", evidence_turn_ids=("t1", "t1")),
            RetrievedItem(reference_id="m2", content="y", evidence_turn_ids=("t1", "t2")),
        ),
        context_tokens=0,
        usage=Usage.zero(),
    )
    assert evidence_recall(retrieved, ("t1", "t2", "t3"), 1) == 1 / 3
    assert evidence_recall(retrieved, ("t1", "t2", "t3"), 2) == 2 / 3
    assert evidence_recall(retrieved, (), 2) is None
    assert evidence_recall(retrieved.model_copy(update={"supported": False}), ("t1",), 2) is None
    with pytest.raises(ValueError):
        evidence_recall(retrieved, ("t1",), 0)


def test_end_of_run_recheck_rejects_changed_source_without_success_manifest(source_repo: Path) -> None:
    raw, manifest, digest = raw_file(source_repo, [lme()], "longmemeval_s_cleaned.json")
    source = LongMemEvalAdapter(raw, manifest_path=manifest)
    selected = selection(source_repo, "LongMemEval_S", digest, ["synthetic"])

    def mutating_judge(_: str) -> str:
        raw.write_bytes(raw.read_bytes() + b" ")
        return "yes"

    with pytest.raises(ValueError, match="benchmark_changed"):
        run_source(source, source_repo, selected, "changed", judge=mutating_judge)
    assert not list((source_repo / "runs").rglob("manifest.json"))
    assert len(list((source_repo / "runs").rglob("FAILED.json"))) == 1


def test_harness_consumes_one_history_before_requesting_the_next(source_repo: Path, monkeypatch) -> None:
    raw, manifest, digest = raw_file(source_repo, [lme("one"), lme("two")], "longmemeval_s_cleaned.json")
    source = LongMemEvalAdapter(raw, manifest_path=manifest)
    selected = selection(source_repo, "LongMemEval_S", digest, ["one", "two"])
    normalizer = source._normalizer
    answers: list[str] = []

    def normalize(row):
        if row["question_id"] == "two":
            assert answers == ["first answered"], "harness eagerly retained multiple histories"
        return normalizer(row)

    def judge(_: str) -> str:
        answers.append("first answered")
        return "yes"

    monkeypatch.setattr(source, "_normalizer", normalize)
    path = run_source(source, source_repo, selected, "streamed", judge=judge)
    assert len(answers) == 2
    assert json.loads((path / "manifest.json").read_bytes())["case_count"] == 2


def test_unselected_annotations_never_reach_normalization_or_scoring(tmp_path: Path) -> None:
    unselected = {"question_id": "held-back", "answer": "UNSELECTED_SENTINEL"}
    raw, manifest, _ = raw_file(tmp_path, [unselected, lme("selected")], "longmemeval_s_cleaned.json")
    source = LongMemEvalAdapter(raw, manifest_path=manifest)
    cases = list(source.iter_cases(("selected",)))
    assert len(cases) == 1
    assert "UNSELECTED_SENTINEL" not in cases[0].model_dump_json()
    assert "held-back" not in source._gold


def test_selected_source_suite_and_hash_are_bound_to_guarded_selection(source_repo: Path) -> None:
    raw, manifest, digest = raw_file(source_repo, [lme()], "longmemeval_s_cleaned.json")
    source = LongMemEvalAdapter(raw, manifest_path=manifest)
    wrong_suite = selection(source_repo, "LoCoMo", digest, ["synthetic"])
    with pytest.raises(ValueError, match="suite_or_split"):
        run_source(source, source_repo, wrong_suite, "suite")
    wrong_hash = selection(source_repo, "LongMemEval_S", "0" * 64, ["synthetic"])
    with pytest.raises(ValueError, match="dataset_hash"):
        run_source(source, source_repo, wrong_hash, "hash")
    assert not list((source_repo / "runs").rglob("manifest.json"))


def test_locomo_fractional_answer_survives_the_actual_streaming_parser(tmp_path: Path) -> None:
    item = locomo()
    item["qa"][0]["answer"] = 2.5
    raw, manifest, _ = raw_file(tmp_path, [item], "locomo10.json")
    source = LoCoMoAdapter(raw, manifest_path=manifest)
    assert len(list(source.iter_cases(("fictional-conversation",)))) == 1
    assert next(iter(source._gold.values())).answer == "2.5"


def test_multiple_gold_units_report_all_evidence_found_separately(tmp_path: Path) -> None:
    item = lme()
    item["haystack_sessions"][0].append({"role": "user", "content": "Another fact.", "has_answer": True})
    raw, manifest, _ = raw_file(tmp_path, [item], "longmemeval_s_cleaned.json")
    source = LongMemEvalAdapter(raw, manifest_path=manifest)
    case = next(source.iter_cases(("synthetic",)))
    event = case.events[-1]
    assert isinstance(event, QuestionEvent)
    question = event.question
    ids = source._gold[question.question_id].gold_turn_ids
    retrieval = RetrievalResult(
        items=(RetrievedItem(reference_id="memory", content="partial", evidence_turn_ids=ids[:1]),),
        context_tokens=0,
        usage=Usage.zero(),
    )
    scorer = source.scorer(k=1)
    partial = {m.name: m.value for m in scorer(question, retrieval, AnswerResult(usage=Usage.zero())).metrics}
    assert partial["primary_evidence_recall_at_k"] == 0.5
    assert partial["primary_all_evidence_found"] == 0.0
    complete = retrieval.model_copy(
        update={"items": (RetrievedItem(reference_id="memory", content="complete", evidence_turn_ids=ids),)}
    )
    full = {m.name: m.value for m in scorer(question, complete, AnswerResult(usage=Usage.zero())).metrics}
    assert full["primary_all_evidence_found"] == 1.0


def test_retrieval_k_is_recorded_and_bound_by_the_frozen_selection(source_repo: Path) -> None:
    raw, manifest, digest = raw_file(source_repo, [lme()], "longmemeval_s_cleaned.json")
    source = LongMemEvalAdapter(raw, manifest_path=manifest)
    selected = selection(source_repo, "LongMemEval_S", digest, ["synthetic"])
    path = run_source(source, source_repo, selected, "recorded")
    assert json.loads((path / "manifest.json").read_bytes())["benchmark_source"]["retrieval_k"] == 2
    record = json.loads(selected.read_bytes())
    record["retrieval_k"] = 3
    selected.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="retrieval_k_mismatch"):
        run_source(source, source_repo, selected, "mismatched")


@pytest.mark.parametrize("benchmark", ["locomo", "longmemeval"])
def test_fractional_gold_uses_official_json_float_text_not_decimal_lexeme(tmp_path: Path, benchmark: str) -> None:
    item = locomo() if benchmark == "locomo" else lme()
    if benchmark == "locomo":
        item["qa"][0]["answer"] = 2.5
        filename, identity = "locomo10.json", "fictional-conversation"
    else:
        item["answer"] = 2.5
        filename, identity = "longmemeval_s_cleaned.json", "synthetic"
    raw, manifest, _ = raw_file(tmp_path, [item], filename)
    raw.write_bytes(raw.read_bytes().replace(b'"answer": 2.5', b'"answer": 2.50'))
    manifest.write_text(f"{hashlib.sha256(raw.read_bytes()).hexdigest()}  {filename}\n", encoding="utf-8")
    source = (
        LoCoMoAdapter(raw, manifest_path=manifest)
        if benchmark == "locomo"
        else LongMemEvalAdapter(raw, manifest_path=manifest)
    )
    list(source.iter_cases((identity,)))
    assert next(iter(source._gold.values())).answer == "2.5"


def test_answerability_metadata_is_kept_out_of_system_inputs(source_repo: Path) -> None:
    item = lme("source_abs")
    item["haystack_session_ids"] = ["source_abs-session"]
    item["answer_session_ids"] = ["source_abs-session"]
    raw, manifest, digest = raw_file(source_repo, [item], "longmemeval_s_cleaned.json")
    source = LongMemEvalAdapter(raw, manifest_path=manifest)
    selected = selection(source_repo, "LongMemEval_S", digest, ["source_abs"])
    seen = []

    class Spy(SyntheticB0):
        def ingest_turn(self, turn):
            assert "_abs" not in turn.turn_id and "_abs" not in turn.session_id
            return super().ingest_turn(turn)

        def answer(self, user_id, question, as_of):
            assert question.stratum is None
            assert "_abs" not in question.question_id and "_abs" not in user_id
            seen.append(question)
            return super().answer(user_id, question, as_of)

    config = load_config(ENGINE / "evaluation/harness/fixtures/config.json")
    path = source.run(
        selection_path=selected,
        split="dev",
        config=config,
        adapter_factory=Spy,
        run_id="private-labels",
        output_root=source_repo / "runs",
        repo_root=source_repo,
        k=2,
    )
    assert len(seen) == 1
    assert json.loads((path / "scores.jsonl").read_bytes())["stratum"] == "abstention"


def test_unresolved_evidence_keeps_qa_but_never_claims_perfect_recall(source_repo: Path) -> None:
    item = locomo()
    item["qa"][0]["evidence"] = ["D1:1", "D1:99"]
    raw, manifest, digest = raw_file(source_repo, [item], "locomo10.json")
    source = LoCoMoAdapter(raw, manifest_path=manifest)
    selected = selection(source_repo, "LoCoMo", digest, ["fictional-conversation"])
    path = run_source(source, source_repo, selected, "unresolved")
    first = json.loads((path / "scores.jsonl").read_text().splitlines()[0])
    values = {metric["name"]: metric["value"] for metric in first["metrics"]}
    assert values["official_qa"] == 1.0
    assert values["evidence_recall_at_k"] is None
    audit = json.loads((path / "manifest.json").read_bytes())["benchmark_source"]["audit"][0]
    assert audit["unresolved_evidence_question_count"] == 1
