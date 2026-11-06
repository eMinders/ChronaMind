"""Pinned real Graphiti worker with explicit model, clock and embedding-role seams.

No Graphiti default LLM/embedder/reranker is constructed. Conditional inclusion
is measured later by D-21; implementing this worker does not claim inclusion.
"""

import asyncio
import copy
import hashlib
import inspect
import json
import platform
import re
import sys
from contextvars import ContextVar
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

import graphiti_core.utils.datetime_utils as datetime_utils
import graphiti_core.utils.maintenance.edge_operations as edge_operations
import graphiti_core.utils.maintenance.node_operations as node_operations
from falkordb.asyncio import FalkorDB
from graphiti_core import Graphiti
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig, ModelSize
from graphiti_core.nodes import EpisodeType
from graphiti_core.search.search_config_recipes import EDGE_HYBRID_SEARCH_RRF
from graphiti_core.search.search_filters import ComparisonOperator, DateFilter, SearchFilters
from pydantic import BaseModel
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from evaluation.baselines.worker_protocol import model_call, receive, send

_QUERY_ROLE: ContextVar[bool] = ContextVar("rl031_embedding_query", default=False)
_CLOCK: ContextVar[datetime | None] = ContextVar("rl031_recorded_clock", default=None)
_SHIMS_INSTALLED = False


def recorded_now() -> datetime:
    value = _CLOCK.get()
    if value is None:
        raise RuntimeError("injected_graph_clock_required")
    return value


def install_pinned_shims() -> None:
    global _SHIMS_INSTALLED
    if _SHIMS_INSTALLED:
        return
    expected = {
        node_operations: "1bcfb52cee7115075409834f6a1692767bd8fecd7f8dc9fc9ca46f9402ce2973",
        edge_operations: "3f894ecf6e2cfb7a60d40d750fb4d0cf42d10d694312d72b4bb10ba5f86d36f9",
    }
    for module, digest in expected.items():
        if hashlib.sha256(Path(inspect.getfile(module)).read_bytes()).hexdigest() != digest:
            raise ValueError("graphiti_internal_shim_source_mismatch")
    original_now = datetime_utils.utc_now
    # Imports copy this function into several maintenance modules. Patch every
    # already-loaded exact alias, including dangling endpoints/combined extraction;
    # the central replacement also covers future imports. Confined to this worker.
    for name, module in tuple(sys.modules.items()):
        if name.startswith("graphiti_core.") and getattr(module, "utc_now", None) is original_now:
            cast(Any, module).utc_now = recorded_now
    original_node_search = node_operations._semantic_candidate_search
    original_edge_search = vars(edge_operations)["search"]

    async def node_query(*args: Any, **kwargs: Any) -> Any:
        token = _QUERY_ROLE.set(True)
        try:
            return await original_node_search(*args, **kwargs)
        finally:
            _QUERY_ROLE.reset(token)

    async def edge_query(*args: Any, **kwargs: Any) -> Any:
        bound = inspect.signature(original_edge_search).bind(*args, **kwargs)
        bound.apply_defaults()
        bound.arguments["query_vector"] = model_call("embed", texts=[bound.arguments["query"]], query=True)[0]
        return await original_edge_search(*bound.args, **bound.kwargs)

    node_operations._semantic_candidate_search = node_query
    cast(Any, edge_operations).search = edge_query
    _SHIMS_INSTALLED = True


class ParentLLM(LLMClient):
    def __init__(self, model: str) -> None:
        super().__init__(LLMConfig(model=model, small_model=model, temperature=0, max_tokens=2048), cache=False)

    async def _generate_response(
        self,
        messages: list[Any],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 16384,
        model_size: ModelSize = ModelSize.medium,
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        if model is not None and model != self.model:
            raise ValueError("graphiti_model_override_rejected")
        # Vendor requests of 16,384 are deliberately capped by the shared parent
        # at the frozen 2,048, not allowed to override D-01 or silently reroute.
        payload = [{"role": m.role, "content": m.content} for m in messages]
        schema = response_model.model_json_schema() if response_model is not None else None
        raw = model_call(
            "llm",
            messages=payload,
            schema=schema,
            requested_max_tokens=max_tokens,
            effective_max_tokens=2048,
        )
        result = json.loads(raw)
        if response_model is not None:
            response_model.model_validate(result)
        if not isinstance(result, dict):
            raise ValueError("graphiti_structured_object_required")
        return result

    # The vendor method is tenacity-wrapped (typed as a retry-decorated callable since tenacity 9.2). Replacing it
    # with a plain coroutine is the point: the parent's single retry policy owns every attempt, so no vendor retry
    # may add unaccounted calls.
    async def _generate_response_with_retry(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
        messages: list[Any],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 16384,
        model_size: ModelSize = ModelSize.medium,
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        # One parent retry policy owns every attempted API call and reservation.
        return await self._generate_response(messages, response_model, max_tokens, model_size, model=model)


class ParentEmbedder(EmbedderClient):
    async def create(self, input_data: Any) -> list[float]:
        if not isinstance(input_data, str):
            raise ValueError("graphiti_embedding_text_required")
        return model_call("embed", texts=[input_data], query=_QUERY_ROLE.get())[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return model_call("embed", texts=input_data_list, query=_QUERY_ROLE.get())


class NoReranker(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        raise RuntimeError("unexpected_graphiti_cross_encoder")


class GraphitiWorker:
    def __init__(self, configuration: dict[str, Any]) -> None:
        if version("graphiti-core") != "0.31.2":
            raise ValueError("pinned_graphiti_environment_required")
        graph = configuration.get("graph")
        if not isinstance(graph, dict) or graph.get("host") not in {"127.0.0.1", "localhost"}:
            raise ValueError("explicit_local_owned_graph_required")
        if type(graph.get("port")) is not int or not 1 <= graph["port"] <= 65535:
            raise ValueError("explicit_graph_port_required")
        self.configuration = configuration
        self.graph = graph
        self.clients: dict[str, Graphiti] = {}
        install_pinned_shims()

    async def client(self, tenant: str) -> Graphiti:
        if not re.fullmatch(r"cme_[a-f0-9]{64}", tenant):
            raise ValueError("owned_graph_namespace_required")
        if tenant not in self.clients:
            # Dedicated database equals the tenant group, preventing later
            # clone(database) from creating hidden drivers/index tasks.
            connection = FalkorDB(
                host=self.graph["host"],
                port=self.graph["port"],
                socket_timeout=3,
                socket_connect_timeout=3,
                retry_on_error=[],
            )
            # FalkorDB's constructor copies its Redis kwargs into a synchronous
            # cluster probe; passing an async Retry there breaks that probe. Set
            # the async policy only after this pinned constructor has returned.
            connection.connection.set_retry(Retry(NoBackoff(), 0))
            driver = FalkorDriver(falkor_db=connection, database=tenant)
            model = self.configuration["settings"]["answer_model"]
            client = Graphiti(
                graph_driver=driver,
                llm_client=ParentLLM(model),
                embedder=ParentEmbedder(),
                cross_encoder=NoReranker(),
                max_coroutines=1,
                store_raw_episode_content=True,
            )
            self.clients[tenant] = client
            # Accounted ingestion/retrieval initialization; await any constructor
            # index task before proceeding, never in reset_user or set_clock.
            task = getattr(driver, "_init_task", None)
            if task is not None:
                await task
            else:
                await driver.build_indices_and_constraints()
        return self.clients[tenant]

    async def operation(self, request: dict[str, Any]) -> Any:
        clock = datetime.fromisoformat(request["clock"])
        if clock.tzinfo is None:
            raise ValueError("aware_graph_clock_required")
        token = _CLOCK.set(clock)
        try:
            tenant = request["tenant"]
            if request["operation"] == "retire":
                await self.retire(tenant)
                return {"retired": True}
            client = await self.client(tenant)
            if request["operation"] == "ingest":
                turn = request["turn"]
                result = await client.add_episode(
                    name=turn["turn_id"],
                    episode_body=f"[speaker={turn.get('source_speaker')};role={turn['role']}]\n{turn['content']}",
                    source_description="RL-031 source turn; original timestamp and speaker retained",
                    reference_time=datetime.fromisoformat(turn["occurred_at"]),
                    source=EpisodeType.message,
                    group_id=tenant,
                )
                return {"episode_id": result.episode.uuid}
            if request["operation"] == "retrieve":
                config = copy.deepcopy(EDGE_HYBRID_SEARCH_RRF)
                config.limit = request["limit"]
                query = request["query"]
                query_vector = model_call("embed", texts=[query], query=True)[0]
                # Native recorded-belief visibility at the injected replay clock.
                # Validity semantics stay native; the free-text question goes to
                # the common answer model rather than adding P's query analyzer.
                filters = SearchFilters(
                    created_at=[[DateFilter(date=clock, comparison_operator=ComparisonOperator.less_than_equal)]],
                    expired_at=[
                        [DateFilter(date=None, comparison_operator=ComparisonOperator.is_null)],
                        [DateFilter(date=clock, comparison_operator=ComparisonOperator.greater_than)],
                    ],
                )
                result = await client.search_(
                    query=query,
                    config=config,
                    group_ids=[tenant],
                    query_vector=query_vector,
                    search_filter=filters,
                )
                return [{"id": edge.uuid, "content": edge.fact, "episode_ids": edge.episodes} for edge in result.edges]
            raise ValueError("unsupported_graphiti_operation")
        finally:
            _CLOCK.reset(token)

    async def settled_operation(self, request: dict[str, Any]) -> Any:
        before = asyncio.all_tasks()
        succeeded = False
        try:
            result = await self.operation(request)
            succeeded = True
            return result
        finally:
            tasks = [t for t in asyncio.all_tasks() - before if not t.done()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not succeeded:
                # No model calls occur during graph deletion. Do this before the
                # failure reply so the parent cannot reconcile while tasks run or
                # ephemeral graph data remains after an ordinary SDK failure.
                await self.close()

    async def retire(self, tenant: str) -> None:
        if re.fullmatch(r"cme_[a-f0-9]{64}", tenant) is None:
            raise ValueError("owned_graph_cleanup_scope_mismatch")
        client = self.clients.get(tenant)
        if client is None:
            return
        driver = client.driver
        failed = False
        if not isinstance(driver, FalkorDriver) or driver._database != tenant:
            raise ValueError("owned_graph_cleanup_scope_mismatch")
        try:
            # Delete only this parent's exact graph; release its Redis client
            # and constructor task through the pinned native close API.
            falkor: Any = driver.client
            if await falkor.connection.exists(tenant):
                await falkor.select_graph(tenant).delete()
        except Exception:
            failed = True
        try:
            await client.close()
        except Exception:
            failed = True
        if failed:
            raise RuntimeError("owned_graph_cleanup_failed")
        self.clients.pop(tenant)

    async def close(self) -> None:
        failed = False
        for tenant in tuple(self.clients):
            try:
                await self.retire(tenant)
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("owned_graph_cleanup_failed")


def main() -> None:
    worker = GraphitiWorker(receive())
    send({"type": "ready", "vendor": "graphiti-core", "version": "0.31.2", "python": platform.python_version()})
    with asyncio.Runner() as runner:
        try:
            while True:
                request = receive()
                if request.get("operation") == "close":
                    runner.run(worker.close())
                    send({"type": "result", "result": None})
                    return
                try:
                    send({"type": "result", "result": runner.run(worker.settled_operation(request))})
                except Exception as error:
                    send({"type": "error", "exception_type": type(error).__name__})
        finally:
            # EOF/invalid control messages also release this worker's namespaces.
            # An OS-forced kill requires the parent's separate cleanup fallback.
            runner.run(worker.close())


if __name__ == "__main__":
    main()
