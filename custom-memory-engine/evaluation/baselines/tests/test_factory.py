"""Public real-adapter assembly and owned lifecycle, without inference or services."""

import json
import subprocess
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Literal, cast

import pytest
from pydantic import SecretStr, ValidationError

from evaluation.baselines.b0_session import B0SessionAdapter
from evaluation.baselines.b1_full_history import B1FullHistoryAdapter
from evaluation.baselines.b2_dense import B2DenseAdapter
from evaluation.baselines.b2h_hybrid import B2hHybridAdapter
from evaluation.baselines.contracts import ModelServices
from evaluation.baselines.factory import (
    EngineFactoryOptions,
    LocalGraphOptions,
    RuntimeOptions,
    assemble_adapter,
    baseline_adapter,
    create_adapter,
)
from evaluation.baselines.p_engine import EngineAdapter
from evaluation.baselines.stores import PostgresRawStore
from evaluation.baselines.tests.helpers import FakeServices, config, settings
from evaluation.baselines.tokenization import TiktokenTokenizer
from evaluation.baselines.vendor import B3Mem0Adapter, B4GraphitiAdapter, VendorProcess
from evaluation.harness.models import MetricScore, QuestionRecord, ScoreResult
from evaluation.harness.runner import run_evaluation
from evaluation.harness.tests.test_runner import fixture_case

TEST_DSN = "postgresql://cme_test:fictional-secret@127.0.0.1:5433/cme_compatibility"


def options_for(system: str, directory: Path) -> RuntimeOptions:
    return RuntimeOptions(
        assets_directory=directory,
        vendor_python=Path(sys.executable) if system in {"B3", "B4"} else None,
        postgres_dsn=SecretStr(TEST_DSN) if system in {"B2h", "P"} else None,
        local_graph=LocalGraphOptions(host="127.0.0.1", port=16379) if system == "B4" else None,
        engine=EngineFactoryOptions() if system == "P" else None,
    )


def production_settings(directory: Path):
    return settings(tokenizer_id=TiktokenTokenizer(directory / "o200k_base.tiktoken").identity)


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("B0", B0SessionAdapter),
        ("B1", B1FullHistoryAdapter),
        ("B2", B2DenseAdapter),
        ("B2h", B2hHybridAdapter),
        ("B3", B3Mem0Adapter),
        ("B4", B4GraphitiAdapter),
        ("P", EngineAdapter),
    ],
)
def test_injected_factory_routes_to_actual_adapters(system, expected, tmp_path: Path) -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = assemble_adapter(
        config(system, selected), selected, cast(ModelServices, services), options_for(system, tmp_path)
    )
    assert isinstance(adapter, expected)
    assert adapter.services is services
    try:
        metadata = adapter.adapter_metadata()
        provenance = cast(dict[str, object], metadata["factory"])
        assert provenance["assembly"] == "injected_model_services"
        assert provenance["embedding_inference_policy"] == selected.embedding_inference_policy
        assert provenance["tokenizer_id"] == selected.tokenizer_id
        if isinstance(adapter, B2hHybridAdapter):
            assert isinstance(adapter.store, PostgresRawStore)
            assert provenance["store_type"] == "PostgresRawStore"
        if isinstance(adapter, B3Mem0Adapter | B4GraphitiAdapter):
            assert adapter._worker.process is None
    finally:
        # ``EngineAdapter`` owns a private event loop; leaking it trips
        # ``filterwarnings = ["error"]`` in later tests.
        adapter.close()
    assert services.current is None and not services.requests and not services.embedding_requests


@pytest.mark.parametrize("system", ["B0", "B1", "B2", "B2h", "B3", "B4", "P"])
def test_production_factory_is_lazy_and_reads_no_assets(
    system: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = production_settings(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("factory performed external work or read a model asset")

    monkeypatch.setattr("evaluation.baselines.assets.verified_bytes", forbidden)
    monkeypatch.setattr("evaluation.baselines.assets.verify_assets", forbidden)
    monkeypatch.setattr("evaluation.baselines.runtime.OpenAITransport.request", forbidden)
    monkeypatch.setattr("evaluation.baselines.stores.psycopg.connect", forbidden)
    monkeypatch.setattr("evaluation.baselines.vendor.subprocess.Popen", forbidden)
    adapter = create_adapter(
        config(system, selected), selected, options_for(system, tmp_path), api_key="fixture-only-key"
    )
    try:
        assert adapter.services.tokenizer.identity == selected.tokenizer_id
        metadata = adapter.adapter_metadata()
        provenance = cast(dict[str, object], metadata["factory"])
        assert provenance["assembly"] == "pinned_onnx_https_runtime"
        assert provenance["assets_status"] == "not_checked_at_construction"
        assert "fictional-secret" not in json.dumps(metadata) and "fixture-only-key" not in repr(adapter)
        assert str(tmp_path) not in json.dumps(metadata)
    finally:
        adapter.close()


@pytest.mark.parametrize(
    ("system", "changes"),
    [
        ("B2h", {"postgres_dsn": None}),
        ("B0", {"postgres_dsn": SecretStr(TEST_DSN)}),
        ("B3", {"vendor_python": None}),
        ("B4", {"vendor_python": None}),
        ("B4", {"local_graph": None}),
        ("B3", {"local_graph": LocalGraphOptions(host="localhost", port=16379)}),
        ("B2", {"vendor_python": Path(sys.executable)}),
        ("P", {"postgres_dsn": None}),
        ("P", {"engine": None}),
        ("B2h", {"engine": EngineFactoryOptions()}),
        ("B0", {"engine": EngineFactoryOptions()}),
    ],
)
def test_system_options_require_only_the_selected_backend(system, changes, tmp_path: Path) -> None:
    selected = settings()
    options = options_for(system, tmp_path).model_copy(update=changes)
    with pytest.raises(ValueError, match="runtime_options_system_mismatch"):
        assemble_adapter(config(system, selected), selected, cast(ModelServices, FakeServices(selected)), options)


def test_p_is_a_first_class_system_not_the_b4_fallback(tmp_path: Path) -> None:
    """Regression: ``P`` used to fall through to the B4 arm and raise AssertionError."""
    selected = settings()
    adapter = assemble_adapter(
        config("P", selected), selected, cast(ModelServices, FakeServices(selected)), options_for("P", tmp_path)
    )
    assert isinstance(adapter, EngineAdapter)
    try:
        provenance = cast(dict[str, object], adapter.adapter_metadata()["factory"])
        assert provenance["store_type"] == "engine_schema:cme_engine"
        assert adapter.extractor is None and adapter.replay_cache is None
    finally:
        adapter.close()


def test_p_without_engine_options_is_a_domain_error(tmp_path: Path) -> None:
    """Missing engine configuration must not surface as an AssertionError."""
    selected = settings()
    options = options_for("P", tmp_path).model_copy(update={"engine": None})
    with pytest.raises(ValueError, match="runtime_options_system_mismatch"):
        assemble_adapter(config("P", selected), selected, cast(ModelServices, FakeServices(selected)), options)


@pytest.mark.parametrize("ablation", ["A1", "A2", "A2b", "A8"])
def test_engine_ablation_options_are_carried_into_the_adapter(ablation: str, tmp_path: Path) -> None:
    """Ablations are first-class systems: each must be constructible under its own id."""
    selected = settings()
    mode = cast("Literal['P', 'A1', 'A2', 'A2b', 'A8']", ablation)
    options = RuntimeOptions(
        assets_directory=tmp_path,
        postgres_dsn=SecretStr(TEST_DSN),
        engine=EngineFactoryOptions(ablation=mode, allow_a2b=True),
    )
    adapter = assemble_adapter(
        config(ablation, selected), selected, cast(ModelServices, FakeServices(selected)), options
    )
    assert isinstance(adapter, EngineAdapter)
    try:
        assert adapter.options.ablation == ablation
    finally:
        adapter.close()


def test_engine_identity_must_match_the_configured_ablation(tmp_path: Path) -> None:
    """A run may not be labelled ``P`` while measuring an ablation."""
    selected = settings()
    options = RuntimeOptions(
        assets_directory=tmp_path,
        postgres_dsn=SecretStr(TEST_DSN),
        engine=EngineFactoryOptions(ablation="A2"),
    )
    with pytest.raises(ValueError, match="engine_ablation_requires_matching_system_id"):
        assemble_adapter(config("P", selected), selected, cast(ModelServices, FakeServices(selected)), options)


def test_a2b_stays_refused_by_default_through_the_factory(tmp_path: Path) -> None:
    selected = settings()
    options = RuntimeOptions(
        assets_directory=tmp_path,
        postgres_dsn=SecretStr(TEST_DSN),
        engine=EngineFactoryOptions(ablation="A2b", allow_a2b=False),
    )
    with pytest.raises(ValueError, match="a2b_disabled_by_d15"):
        assemble_adapter(config("A2b", selected), selected, cast(ModelServices, FakeServices(selected)), options)


@pytest.mark.parametrize("field", ["assets_directory", "vendor_python"])
def test_runtime_options_require_explicit_absolute_paths(field: str, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="explicit_absolute_runtime_path_required"):
        RuntimeOptions.model_validate({"assets_directory": tmp_path, field: Path("relative")})


def test_secret_dsn_is_not_printed_in_options_or_factory_failure(tmp_path: Path) -> None:
    options = RuntimeOptions(assets_directory=tmp_path, postgres_dsn=SecretStr("private-credential-invalid-dsn"))
    assert "private-credential" not in repr(options)
    selected = settings()
    with pytest.raises(ValueError) as failure:
        assemble_adapter(config("B2h", selected), selected, cast(ModelServices, FakeServices(selected)), options)
    assert "private-credential" not in str(failure.value)
    with pytest.raises(ValidationError) as validation:
        RuntimeOptions.model_validate({"assets_directory": tmp_path, "postgres_dsn": {"private-credential": "bad"}})
    assert "private-credential" not in str(validation.value)


def test_factory_refuses_frozen_config_or_services_mismatch_before_backend_construction(tmp_path: Path) -> None:
    selected = settings()
    options = options_for("B0", tmp_path)
    with pytest.raises(ValueError, match="baseline_frozen_configuration_mismatch"):
        assemble_adapter(
            config("B0", selected).model_copy(update={"model_id": "floating"}),
            selected,
            cast(ModelServices, FakeServices(selected)),
            options,
        )
    with pytest.raises(ValueError, match="baseline_model_services_configuration_mismatch"):
        assemble_adapter(
            config("B0", selected), selected, cast(ModelServices, FakeServices(settings(retrieval_k=3))), options
        )


def test_model_copy_cannot_bypass_graph_or_embedding_policy_validation(tmp_path: Path) -> None:
    selected = settings()
    invalid = options_for("B4", tmp_path).model_copy(
        update={"local_graph": LocalGraphOptions(host="localhost", port=16379).model_copy(update={"host": "remote"})}
    )
    with pytest.raises(ValidationError):
        assemble_adapter(config("B4", selected), selected, cast(ModelServices, FakeServices(selected)), invalid)
    invalid_settings = selected.model_copy(update={"embedding_inference_policy": "mean-pool-drift"})
    with pytest.raises(ValidationError):
        assemble_adapter(
            config("B0", selected),
            invalid_settings,
            cast(ModelServices, FakeServices(invalid_settings)),
            options_for("B0", tmp_path),
        )


def test_production_factory_requires_the_actual_pinned_tokenizer(tmp_path: Path) -> None:
    selected = settings()
    with pytest.raises(ValueError, match="production_model_runtime_identity_mismatch"):
        create_adapter(config("B0", selected), selected, options_for("B0", tmp_path), api_key="fixture-only")


def test_metadata_records_absent_optional_distributions_without_importing_them(tmp_path: Path, monkeypatch) -> None:
    def unavailable(package):
        raise PackageNotFoundError(package)

    monkeypatch.setattr("evaluation.baselines.factory.version", unavailable)
    selected = settings()
    adapter = assemble_adapter(
        config("B0", selected), selected, cast(ModelServices, FakeServices(selected)), options_for("B0", tmp_path)
    )
    provenance = cast(dict[str, object], adapter.adapter_metadata()["factory"])
    assert provenance["parent_runtime_packages"] == {
        "pydantic": None,
        "psycopg": None,
        "tiktoken": None,
        "tokenizers": None,
        "onnxruntime": None,
    }


@pytest.mark.parametrize("key", ["", " ", "key\nheader"])
def test_provider_key_errors_remain_static_and_never_enter_options(key: str, tmp_path: Path) -> None:
    selected = production_settings(tmp_path)
    with pytest.raises(ValueError) as failure:
        create_adapter(config("B0", selected), selected, options_for("B0", tmp_path), api_key=key)
    if "header" in key:
        assert "header" not in str(failure.value)


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("system", ["B2h", "B3", "B4"])
def test_context_manager_closes_owned_backend_on_success_or_failure(
    system, fails, tmp_path: Path, monkeypatch
) -> None:
    closed: list[str] = []
    original_store_close = PostgresRawStore.close
    original_worker_close = VendorProcess.close

    def close_store(store):
        closed.append("postgres")
        original_store_close(store)

    def close_worker(worker):
        closed.append(worker.module)
        original_worker_close(worker)

    monkeypatch.setattr(PostgresRawStore, "close", close_store)
    monkeypatch.setattr(VendorProcess, "close", close_worker)
    selected = settings()

    def run():
        with baseline_adapter(
            config(system, selected),
            selected,
            options_for(system, tmp_path),
            services=cast(ModelServices, FakeServices(selected)),
        ) as adapter:
            assert adapter.config.system_id == system
            if fails:
                raise RuntimeError("fixture caller failure")

    if fails:
        with pytest.raises(RuntimeError, match="fixture caller failure"):
            run()
    else:
        run()
    assert closed == [{"B2h": "postgres", "B3": "b3_mem0", "B4": "b4_graphiti"}[system]]


def test_context_manager_requires_exactly_one_explicit_service_source(tmp_path: Path) -> None:
    selected = settings()
    for kwargs in ({}, {"api_key": "fixture", "services": FakeServices(selected)}):
        with pytest.raises(ValueError, match="one_explicit_model_service_source_required"):
            with baseline_adapter(config("B0", selected), selected, options_for("B0", tmp_path), **kwargs):
                pytest.fail("ambiguous service source reached adapter")


def test_public_factory_runs_a_real_adapter_through_guarded_harness_and_persists_provenance(tmp_path: Path) -> None:
    repo = tmp_path
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=30)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
        timeout=30,
    )
    selected = settings()
    configured = config("B1", selected)
    services = FakeServices(selected)
    split = repo / "dev.json"
    split.write_bytes(b"explicit synthetic software case membership\n")
    with ExitStack() as cleanup:
        output = run_evaluation(
            config=configured,
            suite_id="T",
            split="dev",
            split_path=split,
            case_loader=lambda: (fixture_case(),),
            adapter_factory=lambda checked: cleanup.enter_context(
                baseline_adapter(checked, selected, options_for("B1", repo), services=cast(ModelServices, services))
            ),
            scorer=lambda q, r, a: ScoreResult(
                metrics=(MetricScore(name="software_answer", value=float(a.answer == services.answer_text)),)
            ),
            run_id="factory-integration",
            output_root=repo / "runs",
            repo_root=repo,
        )
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["adapter_metadata"]["factory"]["assembly"] == "injected_model_services"
    assert manifest["configuration"]["adapter_configuration_sha256"] == selected.sha256()
    assert len(services.requests) == 1
    assert "fictional café α" in services.requests[0][1].content
    assert json.loads((output / "scores.jsonl").read_bytes())["metrics"] == [{"name": "software_answer", "value": 1.0}]


def test_clean_interpreter_factory_construction_imports_no_heavy_optional_sdk(tmp_path: Path) -> None:
    program = """
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from evaluation.baselines.factory import create_adapter,RuntimeOptions,LocalGraphOptions
from pydantic import SecretStr
from evaluation.baselines.tests.helpers import config,settings
from evaluation.baselines.tokenization import TiktokenTokenizer
p=Path(sys.argv[1]);s=settings(tokenizer_id=TiktokenTokenizer(p/'o200k_base.tiktoken').identity)
dsn='postgresql://cme_test:fixture@127.0.0.1:5433/cme_compatibility'
for system in ('B0','B1','B2','B2h','B3','B4'):
    options=RuntimeOptions(assets_directory=p,
        vendor_python=Path(sys.executable) if system in ('B3','B4') else None,
        postgres_dsn=SecretStr(dsn) if system=='B2h' else None,
        local_graph=LocalGraphOptions(host='localhost',port=16379) if system=='B4' else None)
    create_adapter(config(system,s),s,options,api_key='fixture-only')
for name in ('mem0','graphiti_core','onnxruntime','tokenizers','tiktoken'):
    assert name not in sys.modules,name
print(json.dumps({'status':'lazy_factory_verified'}))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(tmp_path),
            str(Path(__file__).resolve().parents[3] / "src"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "lazy_factory_verified"}


@pytest.mark.parametrize("system", ["B0", "B1", "B2", "B2h", "B3", "B4"])
def test_every_real_adapter_uses_same_clean_utc_reference_mode_and_target_wrapper(system: str, tmp_path: Path) -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = assemble_adapter(
        config(system, selected), selected, cast(ModelServices, services), options_for(system, tmp_path)
    )
    reference = datetime(2020, 2, 1, 2, 30, tzinfo=timezone(timedelta(hours=2)))
    target = datetime(2019, 12, 1, 3, 0, tzinfo=timezone(timedelta(hours=3)))
    adapter.set_clock(reference)
    probe = QuestionRecord(
        question_id="PRIVATE_QUESTION_IDENTIFIER",
        user_id="PRIVATE_USER_IDENTIFIER",
        question="What held at the earlier time?",
        mode="as_of",
        as_of_time=target,
        stratum="PRIVATE_CATEGORY_LABEL",
    )
    messages = adapter._answer_messages(probe, "same visible context")
    expected = (
        "Reference time (UTC): 2020-02-01T00:30:00+00:00\n"
        "Query mode: as_of\n"
        "Target time (UTC): 2019-12-01T00:00:00+00:00\n\n"
        "Context:\nsame visible context\n\nQuestion:\nWhat held at the earlier time?"
    )
    assert messages[0].content == selected.answer_prompt
    assert messages[1].content == expected
    assert "PRIVATE_" not in messages[1].content
    assert not services.requests and not services.embedding_requests
