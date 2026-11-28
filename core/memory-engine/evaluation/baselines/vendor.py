"""Uniform adapters for real vendor SDKs isolated behind accounted model bridges."""

import hashlib
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any

from evaluation.baselines.base import BaseAdapter
from evaluation.baselines.context import fit_ranked_items, join_items
from evaluation.baselines.contracts import EVIDENCE_METRICS, BaselineSettings, Message, ModelServices
from evaluation.baselines.worker_protocol import MAX_MESSAGE_BYTES
from evaluation.harness.config import HarnessConfig
from evaluation.harness.models import (
    DeletionReceipt,
    DeletionScope,
    IngestReceipt,
    QueryMode,
    RetrievalDiagnostics,
    RetrievalResult,
    RetrievedItem,
    TurnRecord,
    Usage,
)

_SAFE_ENV = {
    "APPDATA",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "OS",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}


class VendorProcess:
    """One owned process; all LLM/embedder requests return to the parent meter.

    The worker receives no provider credentials. A deadline terminates only this
    owned child; no background SDK task can dispatch after an operation returns.
    """

    def __init__(
        self,
        module: str,
        python: Path,
        settings: BaselineSettings,
        services: ModelServices,
        graph: dict[str, object] | None = None,
    ) -> None:
        if module not in {"b3_mem0", "b4_graphiti"}:
            raise ValueError("unknown_vendor_worker")
        self.module = module
        self.python = python
        self.settings = settings
        self.services = services
        self.graph = graph
        self.process: subprocess.Popen[bytes] | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._messages: queue.Queue[bytes | Exception] = queue.Queue(maxsize=32)
        self._graph_tenants: set[str] = set()
        self._cleanup_required = False
        self.runtime_metadata: dict[str, object] = {}

    def _reader(self, process: subprocess.Popen[bytes], messages: queue.Queue[bytes | Exception]) -> None:
        assert process.stdout is not None
        try:
            while True:
                row = process.stdout.readline(MAX_MESSAGE_BYTES + 2)
                if not row or len(row) > MAX_MESSAGE_BYTES or not row.endswith(b"\n"):
                    raise RuntimeError("vendor_protocol_closed_or_oversized")
                messages.put(row, timeout=1)
        except Exception as error:
            try:
                messages.put(error, timeout=1)
            except queue.Full:
                pass

    def _send(self, value: dict[str, object]) -> None:
        assert self.process is not None and self.process.stdin is not None
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("vendor_message_limit")
        self.process.stdin.write(raw)
        self.process.stdin.flush()

    def _receive(self, deadline: float) -> dict[str, Any]:
        timeout = deadline - monotonic()
        if timeout <= 0:
            raise TimeoutError("vendor_operation_timeout")
        row = self._messages.get(timeout=timeout)
        if isinstance(row, Exception):
            raise RuntimeError("vendor_protocol_failed") from None
        value = json.loads(row)
        if not isinstance(value, dict):
            raise ValueError("vendor_object_required")
        return value

    def _start(self, deadline: float) -> None:
        if self.process is not None:
            return
        if not self.python.is_file():
            raise ValueError("explicit_vendor_interpreter_missing")
        self._temporary = tempfile.TemporaryDirectory(prefix="cme-rl031-")
        engine = Path(__file__).resolve().parents[2]
        env = {k: v for k, v in os.environ.items() if k.upper() in _SAFE_ENV}
        env.update(
            {
                "MEM0_DIR": self._temporary.name,
                "MEM0_TELEMETRY": "false",
                "GRAPHITI_TELEMETRY_ENABLED": "false",
                "PYTHON_DOTENV_DISABLED": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
            }
        )
        program = (
            "import sys,runpy;sys.path[:0]="
            + repr([str(engine), str(engine / "src")])
            + ";runpy.run_module('evaluation.baselines.worker_bootstrap',run_name='__main__')"
        )
        self.process = subprocess.Popen(
            [str(self.python), "-u", "-c", program, self.module],
            cwd=self._temporary.name,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            text=False,
        )
        threading.Thread(target=self._reader, args=(self.process, self._messages), daemon=True).start()
        self._send({"settings": self.settings.model_dump(mode="json"), "graph": self.graph})
        response = self._receive(deadline)
        expected = ("mem0ai", "2.2.1") if self.module == "b3_mem0" else ("graphiti-core", "0.31.2")
        python_version = response.get("python")
        if (
            response.get("type") != "ready"
            or (response.get("vendor"), response.get("version")) != expected
            or not isinstance(python_version, str)
            or not re.fullmatch(r"3\.12\.\d+", python_version)
        ):
            raise ValueError("vendor_handshake_failed")
        self.runtime_metadata = {"vendor": expected[0], "version": expected[1], "python": python_version}

    def operation(self, payload: dict[str, object]) -> Any:
        deadline = monotonic() + self.settings.vendor_timeout_seconds
        retiring = payload.get("operation") == "retire"
        try:
            self._start(deadline)
            if self.module == "b4_graphiti":
                tenant = payload.get("tenant")
                if not isinstance(tenant, str) or not re.fullmatch(r"cme_[a-f0-9]{64}", tenant):
                    raise ValueError("owned_graph_namespace_required")
                # Retain the issued scope independently of the child. Even a
                # killed process may already have created its graph/indexes.
                if retiring:
                    if tenant not in self._graph_tenants:
                        raise ValueError("owned_graph_retirement_scope_required")
                else:
                    self._graph_tenants.add(tenant)
            self._send(payload)
            while True:
                response = self._receive(deadline)
                kind = response.get("type")
                if kind == "result":
                    result = response["result"]
                    if retiring:
                        if not isinstance(result, dict) or set(result) != {"retired"} or result["retired"] is not True:
                            raise ValueError("vendor_retirement_acknowledgement_required")
                        if self.module == "b4_graphiti":
                            self._graph_tenants.remove(str(payload["tenant"]))
                    return result
                if retiring and kind in {"llm", "embed"}:
                    raise RuntimeError("model_call_during_vendor_retirement")
                if kind == "llm":
                    messages = tuple(Message.model_validate(m) for m in response["messages"])
                    try:
                        result = self.services.complete(
                            messages, schema=response.get("schema"), json_object=response.get("json_object", False)
                        )
                    except BaseException:
                        self._send({"ok": False})
                        raise
                    self._send({"ok": True, "result": result.text})
                elif kind == "embed":
                    texts = response["texts"]
                    if not isinstance(texts, list) or not all(isinstance(text, str) for text in texts):
                        raise ValueError("vendor_embedding_texts_required")
                    query = response["query"]
                    if type(query) is not bool:
                        raise ValueError("vendor_embedding_role_required")
                    try:
                        embedded = self.services.embed(texts, query=query)
                    except BaseException:
                        self._send({"ok": False})
                        raise
                    self._send({"ok": True, "result": [list(v) for v in embedded.vectors]})
                else:
                    raise RuntimeError("vendor_operation_failed")
        except BaseException:
            # Kill after callbacks settle/raise, before returning to reconciliation.
            # Parent scope already retains every dispatched call's known/bound usage.
            self.close()
            raise

    def close(self) -> None:
        process = self.process
        # A previously exited child never acknowledged this close, even when
        # its OS status is zero. Its external graphs still require recovery.
        abnormal = self._cleanup_required or (process is not None and process.poll() is not None)
        if process is not None:
            if process.poll() is None:
                try:
                    self._send({"operation": "close"})
                    deadline = monotonic() + 5
                    while self._receive(deadline).get("type") != "result":
                        pass
                    process.wait(timeout=max(0.01, deadline - monotonic()))
                except (OSError, ValueError, RuntimeError, TimeoutError, queue.Empty, subprocess.TimeoutExpired):
                    abnormal = True
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
            abnormal = abnormal or process.returncode != 0
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        # Persist recovery before discarding the exited child: local teardown can
        # fail (for example a Windows file lock) and close() must remain retryable.
        self._cleanup_required = abnormal and bool(self._graph_tenants)
        self.process = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self._messages = queue.Queue(maxsize=32)
        if abnormal and self._graph_tenants:
            self._cleanup_required = True
            self._recover_graphs()
            self._cleanup_required = False
        self._graph_tenants.clear()

    def _recover_graphs(self) -> None:
        """No SDK/model calls: recover only names retained by this live parent."""
        engine = Path(__file__).resolve().parents[2]
        program = (
            "import sys,runpy;sys.path[:0]="
            + repr([str(engine), str(engine / "src")])
            + ";runpy.run_module('evaluation.baselines.graph_cleanup',run_name='__main__')"
        )
        environment = {k: v for k, v in os.environ.items() if k.upper() in _SAFE_ENV}
        environment.update({"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
        try:
            with tempfile.TemporaryDirectory(prefix="cme-rl031-cleanup-") as directory:
                result = subprocess.run(
                    [str(self.python), "-u", "-c", program],
                    input=json.dumps({"graph": self.graph, "tenants": sorted(self._graph_tenants)}).encode(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=directory,
                    env=environment,
                    timeout=15,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            if result.returncode or result.stdout.strip() != b'{"ok":true}':
                raise RuntimeError("owned_graph_cleanup_failed")
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("owned_graph_cleanup_failed") from None


class VendorAdapter(BaseAdapter):
    """Common answer path; real SDK owns ingestion/extraction/search behavior."""

    def __init__(
        self,
        config: HarnessConfig,
        settings: BaselineSettings,
        services: ModelServices,
        *,
        python: Path,
        graph: dict[str, object] | None = None,
    ) -> None:
        super().__init__(config, settings, services)
        if config.system_id not in {"B3", "B4"}:
            raise ValueError("vendor_baseline_identity_required")
        self._worker = VendorProcess(
            "b3_mem0" if config.system_id == "B3" else "b4_graphiti", python, settings, services, graph
        )
        self._poisoned = False
        self._episodes: dict[str, dict[str, str]] = {}

    @staticmethod
    def _tenant_scope(user_id: str, generation: str) -> str:
        return "cme_" + hashlib.sha256(f"{user_id}:{generation}".encode()).hexdigest()

    def _tenant(self, user_id: str) -> str:
        state = self._state(user_id)
        return self._tenant_scope(user_id, state.generation)

    def release_user(self, user_id: str) -> None:
        state = self._users.get(user_id)
        if state is not None:
            self._episodes.pop(state.generation, None)
        super().release_user(user_id)

    def _retire_backend(self, generation: str, user_id: str) -> None:
        if self._poisoned or self.clock is None:
            raise RuntimeError("failed_vendor_adapter_requires_new_instance")
        try:
            self._worker.operation(
                {
                    "operation": "retire",
                    "tenant": self._tenant_scope(user_id, generation),
                    "clock": self.clock.isoformat(),
                }
            )
        except BaseException:
            self._poisoned = True
            raise

    def _request(self, user_id: str, **value: object) -> Any:
        if self._poisoned:
            raise RuntimeError("failed_vendor_adapter_requires_new_instance")
        if self.clock is None:
            raise ValueError("baseline_clock_required")
        try:
            self._materialized.add(self._state(user_id).generation)
            return self._worker.operation({"tenant": self._tenant(user_id), "clock": self.clock.isoformat(), **value})
        except BaseException:
            self._poisoned = True
            raise

    def ingest_turn(self, turn: TurnRecord) -> IngestReceipt:
        state = self._validate_turn(turn)
        self._invalidate(turn.user_id)
        start = perf_counter()
        succeeded = False
        try:
            result, usage = self._run(
                "ingest_turn",
                lambda: self._request(turn.user_id, operation="ingest", turn=turn.model_dump(mode="json")),
            )
            if isinstance(result, dict) and isinstance(result.get("episode_id"), str):
                self._episodes.setdefault(state.generation, {})[result["episode_id"]] = turn.turn_id
            state.turns.append(turn)
            state.mutation += 1
            succeeded = True
            return IngestReceipt(turn_id=turn.turn_id, usage=usage)
        finally:
            metrics = getattr(self.services, "operation_metrics", lambda: {})()
            self._emit(
                {
                    "event": "vendor_episode",
                    "system_id": self.config.system_id,
                    "turn_id": turn.turn_id,
                    "success": succeeded,
                    "ingestion_wall_ms": (perf_counter() - start) * 1000,
                    "model_calls": metrics,
                }
            )

    def retrieve(
        self, user_id: str, query: str, mode: QueryMode, as_of: datetime, token_budget: int
    ) -> RetrievalResult:
        question = self._question(user_id, query, mode, as_of, token_budget)

        def execute() -> tuple[list[RetrievedItem], bool]:
            rows = self._request(user_id, operation="retrieve", query=query, limit=self.settings.retrieval_k)
            episodes = self._episodes.get(self._state(user_id).generation, {})
            items: list[RetrievedItem] = []
            provenance_complete = self.config.system_id == "B4"
            for row in rows:
                episode_ids = row.get("episode_ids", [])
                evidence = tuple(episodes[e] for e in episode_ids if e in episodes)
                if not episode_ids or len(evidence) != len(episode_ids):
                    provenance_complete = False
                items.append(RetrievedItem(reference_id=row["id"], content=row["content"], evidence_turn_ids=evidence))
            return items, provenance_complete

        (items, provenance_complete), usage = self._run("retrieve", execute)
        source_count = len(items)
        selected, dropped_tokens = fit_ranked_items(items, self.services.tokenizer, token_budget)
        rendered = join_items(selected)
        result = RetrievalResult(
            items=tuple(selected),
            rendered_context=rendered,
            context_tokens=len(self.services.tokenizer.encode(rendered)),
            usage=usage,
            unavailable_metrics=() if provenance_complete else EVIDENCE_METRICS,
            unavailable_metrics_reason=None if provenance_complete else "vendor_evidence_provenance_unavailable",
            diagnostics=RetrievalDiagnostics(
                context_policy="retrieved_memory",
                source_items=source_count,
                selected_items=len(selected),
                truncated_items=source_count - len(selected),
                truncated_tokens=dropped_tokens,
                candidate_count=source_count,
            ),
        )
        self._remember(question, as_of, result)
        return result

    def delete(self, user_id: str, scope: DeletionScope, target_ref: str) -> DeletionReceipt:
        self._state(user_id)
        # SDK removal does not establish P's seven-channel hard-erasure contract.
        # In particular Mem0 retains deleted text in SQLite history. Mark D-suite
        # capability unavailable rather than silently scoring incomplete deletion.
        return DeletionReceipt(supported=False, usage=Usage.zero())

    def adapter_metadata(self) -> dict[str, object]:
        return {
            **super().adapter_metadata(),
            "vendor": "mem0ai" if self.config.system_id == "B3" else "graphiti-core",
            "vendor_version": self.settings.mem0_version
            if self.config.system_id == "B3"
            else self.settings.graphiti_version,
            "isolated_worker": True,
            "worker_runtime": dict(self._worker.runtime_metadata),
            "mem0_variant": "semantic_only_without_spacy_fastembed" if self.config.system_id == "B3" else None,
            "replay_policy": self.settings.mem0_replay_policy
            if self.config.system_id == "B3"
            else self.settings.graphiti_replay_policy,
            "D21": "unmeasured" if self.config.system_id == "B4" else None,
            "temporal_query_policy": "native_default_no_P_time_filter",
            "deletion_capability": "not_supported_for_seven_channel_contract",
        }

    def close(self) -> None:
        super().close()
        self._worker.close()
        self._retired.clear()
        self._materialized.clear()
        self._episodes.clear()


class B3Mem0Adapter(VendorAdapter):
    def __init__(self, config: HarnessConfig, *args: Any, **kwargs: Any) -> None:
        if config.system_id != "B3":
            raise ValueError("B3_identity_required")
        super().__init__(config, *args, **kwargs)


class B4GraphitiAdapter(VendorAdapter):
    def __init__(self, config: HarnessConfig, *args: Any, **kwargs: Any) -> None:
        if config.system_id != "B4":
            raise ValueError("B4_identity_required")
        super().__init__(config, *args, **kwargs)
