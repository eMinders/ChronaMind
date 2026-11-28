"""Pinned real Mem0 worker. This is the sole allowed Mem0 import boundary.

The vendor's own ADD-only extraction, deduplication and vector search execute.
Only its LLM/embedder factories are replaced inside this isolated process with
accounted parent bridges. No vendor OpenAI/HF defaults are constructed.
"""

import hashlib
import importlib.util
import platform
import re
from datetime import UTC, datetime, tzinfo
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

from mem0 import Memory
from mem0.configs import prompts as mem0_prompts
from mem0.configs.base import MemoryConfig
from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.configs.llms.base import BaseLlmConfig
from mem0.embeddings.base import EmbeddingBase
from mem0.llms.base import LLMBase
from mem0.memory import main as mem0_main
from mem0.memory import storage as mem0_storage
from mem0.utils.factory import EmbedderFactory, LlmFactory
from qdrant_client import QdrantClient

from evaluation.baselines.worker_protocol import model_call, receive, send

_CLOCK: datetime | None = None
_OBSERVATION: datetime | None = None
_SOURCE_HASHES = {
    "main": "f329ca4a17af1fbefb32ac10d4cc7ae7ddbdb7435a87eb74c30915cff98b8b7c",
    "prompts": "10bc8a34b3b5f0ce24560a2a3190c9112b979a891b981f48393bbd168d915a5c",
    "storage": "5f5ecdadd2b260fd14f36a0f338336eb760275465dd1056a99a47e1ef07702bd",
}


class ReplayDatetime(datetime):
    """One bound clock for the worker operation and any synchronous SDK threads."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        if _CLOCK is None:
            raise ValueError("mem0_operation_clock_required")
        return _CLOCK.astimezone(tz) if tz is not None else _CLOCK.replace(tzinfo=None)


def _install_clock_shims() -> None:
    for name, module in (("main", mem0_main), ("prompts", mem0_prompts), ("storage", mem0_storage)):
        if (
            module.__file__ is None
            or hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() != _SOURCE_HASHES[name]
        ):
            raise ValueError("pinned_mem0_clock_source_mismatch")
    original_prompt = mem0_prompts.generate_additive_extraction_prompt

    def replay_prompt(*args: Any, **kwargs: Any) -> str:
        if _CLOCK is None or _OBSERVATION is None:
            raise ValueError("mem0_observation_clock_required")
        kwargs["current_date"] = _CLOCK.date().isoformat()
        kwargs["timestamp"] = _OBSERVATION.date().isoformat()
        return original_prompt(*args, **kwargs)

    # SDK imports these datetime classes/functions by value; patch every active
    # alias inside this disposable process. The original vendor prompt is kept.
    mem0_main.datetime = ReplayDatetime
    mem0_prompts.datetime = ReplayDatetime
    mem0_storage.datetime = ReplayDatetime
    mem0_main.generate_additive_extraction_prompt = replay_prompt


def _aware_clock(raw: str) -> datetime:
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_mem0_replay_clock_required")
    return value.astimezone(UTC)


class ParentLLM(LLMBase):
    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__(BaseLlmConfig(model=settings["answer_model"], temperature=0, top_p=1, max_tokens=2048))

    def generate_response(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        messages: list[dict[str, Any]],
        response_format: Any = None,
        tools: Any = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> str:
        if tools or tool_choice:
            raise ValueError("unexpected_mem0_tool_request")
        if response_format not in (None, {"type": "json_object"}):
            raise ValueError("unsupported_mem0_response_format")
        return model_call("llm", messages=messages, schema=None, json_object=response_format is not None)


class ParentEmbedder(EmbeddingBase):
    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__(BaseEmbedderConfig(model=settings["embedding_model"], embedding_dims=384))

    def embed(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, text: str, memory_action: str | None = None
    ) -> list[float]:
        return model_call("embed", texts=[text], query=memory_action == "search")[0]

    def embed_batch(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, texts: list[str], memory_action: str = "add"
    ) -> list[list[float]]:
        return model_call("embed", texts=texts, query=memory_action == "search")


class Mem0Worker:
    def __init__(self, configuration: dict[str, Any]) -> None:
        if version("mem0ai") != "2.2.1" or version("qdrant-client") != "1.20.0":
            raise ValueError("pinned_mem0_environment_required")
        if any(importlib.util.find_spec(name) for name in ("spacy", "fastembed")):
            raise ValueError("uncontrolled_optional_mem0_models_present")
        _install_clock_shims()
        self.configuration = configuration
        self.memories: dict[str, Memory] = {}
        self.clients: dict[str, QdrantClient] = {}

    def initialize(self, tenant: str) -> Memory:
        if re.fullmatch(r"cme_[a-f0-9]{64}", tenant) is None:
            raise ValueError("owned_mem0_namespace_required")
        if tenant in self.memories:
            return self.memories[tenant]
        settings = self.configuration["settings"]
        # Pinned factory seams, scoped to this disposable worker only. Ordinary
        # provider names retain actual SDK validation; no global parent patches.
        # Vendor abstract stubs infer None returns; real implementations return
        # text/vectors. This pinned dynamic factory seam cannot use those stubs.
        cast(Any, LlmFactory).create = classmethod(lambda cls, *args, **kwargs: ParentLLM(settings))
        cast(Any, EmbedderFactory).create = classmethod(lambda cls, *args, **kwargs: ParentEmbedder(settings))
        client = QdrantClient(location=":memory:")
        self.clients[tenant] = client
        config = MemoryConfig.model_validate(
            {
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": settings["answer_model"],
                        "temperature": 0,
                        "max_tokens": 2048,
                    },
                },
                "embedder": {
                    "provider": "openai",
                    "config": {
                        "model": settings["embedding_model"],
                        "embedding_dims": 384,
                    },
                },
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "client": client,
                        "path": ":memory:",
                        "collection_name": "rl031_memories",
                        "embedding_model_dims": 384,
                    },
                },
                "history_db_path": ":memory:",
                "reranker": None,
            }
        )
        memory = Memory(config)
        self.memories[tenant] = memory
        return memory

    def retire(self, tenant: str) -> None:
        """Dispose one owned in-memory vector store and its SQLite history.

        Mem0.delete leaves content in native history; separate instances let
        retirement free that history without modifying another active tenant.
        Keep failed cleanup handles for a retry during final close.
        """
        if re.fullmatch(r"cme_[a-f0-9]{64}", tenant) is None:
            raise ValueError("owned_mem0_namespace_required")
        failed = False
        memory = self.memories.get(tenant)
        client = self.clients.get(tenant)
        if memory is not None:
            try:
                memory.close()
            except Exception:
                failed = True
        if client is not None:
            try:
                client.close()
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("owned_mem0_cleanup_failed")
        self.memories.pop(tenant, None)
        self.clients.pop(tenant, None)

    def close(self) -> None:
        failed = False
        for tenant in tuple(self.clients.keys() | self.memories.keys()):
            try:
                self.retire(tenant)
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("owned_mem0_cleanup_failed")

    def operation(self, request: dict[str, Any]) -> Any:
        global _CLOCK, _OBSERVATION
        if _CLOCK is not None:
            raise ValueError("nested_mem0_operation_clock")
        _CLOCK = _aware_clock(request["clock"])
        try:
            if request["operation"] == "ingest":
                _OBSERVATION = _aware_clock(request["turn"]["occurred_at"])
                if _OBSERVATION > _CLOCK:
                    raise ValueError("source_time_exceeds_mem0_replay_clock")
            return self._operation(request)
        finally:
            # The synchronous SDK joins its own executor work before return;
            # parent RPC bridges also serialize dispatch. Do not leak dates
            # across the next tenant/operation in the same owned worker.
            _CLOCK = None
            _OBSERVATION = None

    def _operation(self, request: dict[str, Any]) -> Any:
        tenant = request["tenant"]
        if request["operation"] == "retire":
            self.retire(tenant)
            return {"retired": True}
        memory = self.initialize(tenant)
        if request["operation"] == "ingest":
            turn = request["turn"]
            speaker = turn.get("source_speaker")
            content = (
                f"[source speaker: {speaker}; role: {turn['role']}; source time: {turn['occurred_at']}]\n"
                f"{turn['content']}"
            )
            memory.add(
                [{"role": turn["role"], "content": content}],
                user_id=tenant,
                infer=True,
                metadata={"source_occurred_at": turn["occurred_at"], "source_turn_id": turn["turn_id"]},
            )
            return {"accepted": True}
        if request["operation"] == "retrieve":
            result = memory.search(query=request["query"], filters={"user_id": tenant}, top_k=request["limit"])
            return [{"id": row["id"], "content": row["memory"], "evidence_turn_ids": []} for row in result["results"]]
        if request["operation"] == "delete_memory":
            result = memory.get_all(filters={"user_id": tenant})
            ids = {row["id"] for row in result["results"]}
            if request["reference_id"] not in ids:
                return {"deleted_count": 0}
            memory.delete(request["reference_id"])
            return {"deleted_count": 1}
        if request["operation"] == "delete_all":
            result = memory.get_all(filters={"user_id": tenant})
            for row in result["results"]:
                memory.delete(row["id"])
            return {"deleted_count": len(result["results"])}
        raise ValueError("unsupported_mem0_operation")


def main() -> None:
    worker = Mem0Worker(receive())
    send({"type": "ready", "vendor": "mem0ai", "version": "2.2.1", "python": platform.python_version()})
    try:
        while True:
            request = receive()
            if request.get("operation") == "close":
                worker.close()
                send({"type": "result", "result": None})
                return
            try:
                send({"type": "result", "result": worker.operation(request)})
            except Exception as error:
                send({"type": "error", "exception_type": type(error).__name__})
    finally:
        worker.close()


if __name__ == "__main__":
    main()
