"""Explicit public assembly for the six real RL-031 adapters; no work on import."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, SecretStr, field_validator

from evaluation.baselines.b0_session import B0SessionAdapter
from evaluation.baselines.b1_full_history import B1FullHistoryAdapter
from evaluation.baselines.b2_dense import B2DenseAdapter
from evaluation.baselines.b2h_hybrid import B2hHybridAdapter
from evaluation.baselines.base import BaseAdapter
from evaluation.baselines.contracts import ENGINE_SYSTEM_IDS, BaselineSettings, ModelServices
from evaluation.baselines.embedding import OnnxBgeEmbedder
from evaluation.baselines.p_engine import EngineAdapter, EngineOptions
from evaluation.baselines.runtime import MeteredServices, OpenAITransport
from evaluation.baselines.stores import PostgresRawStore
from evaluation.baselines.tokenization import TiktokenTokenizer
from evaluation.baselines.vendor import B3Mem0Adapter, B4GraphitiAdapter
from evaluation.harness.config import HarnessConfig
from evaluation.harness.models import Record


class LocalGraphOptions(Record):
    host: Literal["localhost", "127.0.0.1"]
    port: int = Field(ge=1, le=65535)


class EngineFactoryOptions(Record):
    """Engine configuration needed to assemble ``P`` without importing a model.

    ``P`` keeps its database DSN in ``RuntimeOptions.postgres_dsn`` like ``B2h``;
    this record only carries the labelled write/read switches that must appear in
    the run manifest.
    """

    schema_name: str = "cme_engine"
    ablation: Literal["P", "A1", "A2", "A2b", "A8"] = "P"
    allow_a2b: bool = False

    @field_validator("schema_name")
    @classmethod
    def isolated_schema_identifier(cls, value: str) -> str:
        if re.fullmatch(r"cme_[a-z][a-z0-9_]*", value) is None:
            raise ValueError("isolated_engine_schema_identifier_required")
        return value


class RuntimeOptions(Record):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False, hide_input_in_errors=True)

    assets_directory: Path
    vendor_python: Path | None = None
    postgres_dsn: SecretStr | None = Field(default=None, repr=False)
    local_graph: LocalGraphOptions | None = None
    engine: EngineFactoryOptions | None = None

    @field_validator("assets_directory", "vendor_python")
    @classmethod
    def absolute_paths(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("explicit_absolute_runtime_path_required")
        return value


def assemble_adapter(
    config: HarnessConfig, settings: BaselineSettings, services: ModelServices, options: RuntimeOptions
) -> BaseAdapter:
    """Assemble actual adapters with caller-injected, fully accounted services.

    No asset is read, SDK imported, subprocess started or store connected here.
    The harness still owns authorization and dispatch reservations.
    """
    selected = BaselineSettings.model_validate(settings.model_dump())
    checked = selected.validate_harness(config)
    runtime = _validated_options(options, checked.system_id)
    if services.settings.sha256() != selected.sha256() or services.tokenizer.identity != selected.tokenizer_id:
        raise ValueError("baseline_model_services_configuration_mismatch")
    if checked.system_id == "B0":
        adapter = B0SessionAdapter(checked, selected, services)
    elif checked.system_id == "B1":
        adapter = B1FullHistoryAdapter(checked, selected, services)
    elif checked.system_id == "B2":
        adapter = B2DenseAdapter(checked, selected, services)
    elif checked.system_id == "B2h":
        assert runtime.postgres_dsn is not None
        adapter = B2hHybridAdapter(
            checked, selected, services, PostgresRawStore(runtime.postgres_dsn.get_secret_value())
        )
    elif checked.system_id == "B3":
        assert runtime.vendor_python is not None
        adapter = B3Mem0Adapter(checked, selected, services, python=runtime.vendor_python)
    elif checked.system_id in ENGINE_SYSTEM_IDS:
        # ``P`` and its ablations are first-class systems, not the B4 fallback: assembling
        # one without an engine block is a configuration error, never an AssertionError.
        assert runtime.postgres_dsn is not None and runtime.engine is not None
        engine = runtime.engine
        adapter = EngineAdapter(
            checked,
            selected,
            services,
            options=EngineOptions(
                database_url=runtime.postgres_dsn.get_secret_value(),
                schema_name=engine.schema_name,
                ablation=engine.ablation,
                allow_a2b=engine.allow_a2b,
            ),
        )
    else:
        assert runtime.vendor_python is not None and runtime.local_graph is not None
        adapter = B4GraphitiAdapter(
            checked,
            selected,
            services,
            python=runtime.vendor_python,
            graph=runtime.local_graph.model_dump(mode="json"),
        )
    adapter.factory_provenance = _provenance(adapter, selected, "injected_model_services")
    return adapter


def _validated_options(options: RuntimeOptions, system_id: str) -> RuntimeOptions:
    # Pydantic's model_copy(update=...) deliberately skips validation. Revalidate
    # all caller records before choosing a backend or retaining a credential.
    runtime = RuntimeOptions.model_validate(options.model_dump())
    is_engine_system = system_id in ENGINE_SYSTEM_IDS
    if (
        (runtime.postgres_dsn is not None) != (system_id == "B2h" or is_engine_system)
        or (runtime.vendor_python is not None) != (system_id in {"B3", "B4"})
        or (runtime.local_graph is not None) != (system_id == "B4")
        or (runtime.engine is not None) != is_engine_system
    ):
        raise ValueError("runtime_options_system_mismatch")
    if runtime.engine is not None and runtime.engine.ablation != system_id:
        # The engine block and the run label must agree before any backend is built.
        raise ValueError("engine_ablation_requires_matching_system_id")
    if runtime.engine is not None and runtime.engine.ablation == "A2b" and not runtime.engine.allow_a2b:
        raise ValueError("a2b_disabled_by_d15")
    return runtime


def _provenance(adapter: BaseAdapter, settings: BaselineSettings, assembly: str) -> dict[str, object]:
    packages: dict[str, str | None] = {}
    for name in ("pydantic", "psycopg", "tiktoken", "tokenizers", "onnxruntime"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "assembly": assembly,
        "tokenizer_id": settings.tokenizer_id,
        "embedding_model": settings.embedding_model,
        "embedding_revision": settings.embedding_revision,
        "embedding_inference_policy": settings.embedding_inference_policy,
        "store_type": _store_type(adapter),
        "assets_status": "not_checked_at_construction",
        "parent_runtime_packages": packages,
        "vendor_runtime_version_check": "on_worker_start"
        if isinstance(adapter, B3Mem0Adapter | B4GraphitiAdapter)
        else None,
    }


def _store_type(adapter: object) -> str:
    """Name the adapter's storage backend without assuming every adapter has ``.store``."""
    if isinstance(adapter, B2DenseAdapter):
        return type(adapter.store).__name__
    if isinstance(adapter, B3Mem0Adapter | B4GraphitiAdapter):
        return "isolated_vendor_worker"
    if isinstance(adapter, EngineAdapter):
        return f"engine_schema:{adapter.options.schema_name}"
    return "local_raw_history"


def create_adapter(
    config: HarnessConfig, settings: BaselineSettings, options: RuntimeOptions, *, api_key: str
) -> BaseAdapter:
    """Build the pinned local-model/explicit HTTPS runtime without executing it."""
    selected = BaselineSettings.model_validate(settings.model_dump())
    checked = selected.validate_harness(config)
    runtime = _validated_options(options, checked.system_id)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("explicit_provider_api_key_required")
    tokenizer = TiktokenTokenizer(runtime.assets_directory / "o200k_base.tiktoken")
    embedder = OnnxBgeEmbedder(runtime.assets_directory)
    if tokenizer.identity != selected.tokenizer_id or embedder.inference_policy != selected.embedding_inference_policy:
        raise ValueError("production_model_runtime_identity_mismatch")
    services = MeteredServices(
        selected,
        tokenizer,
        embedder,
        OpenAITransport(api_key, timeout=selected.provider_timeout_seconds),
    )
    adapter = assemble_adapter(checked, selected, services, runtime)
    adapter.factory_provenance = _provenance(adapter, selected, "pinned_onnx_https_runtime")
    return adapter


@contextmanager
def baseline_adapter(
    config: HarnessConfig,
    settings: BaselineSettings,
    options: RuntimeOptions,
    *,
    api_key: str | None = None,
    services: ModelServices | None = None,
) -> Iterator[BaseAdapter]:
    """Close only the returned adapter's owned store/worker, even on caller failure."""
    if (api_key is None) == (services is None):
        raise ValueError("one_explicit_model_service_source_required")
    adapter = (
        assemble_adapter(config, settings, services, options)
        if services is not None
        else create_adapter(config, settings, options, api_key=api_key or "")
    )
    try:
        yield adapter
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
