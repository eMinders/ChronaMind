"""B1: full ingested history up to the complete answering-model context window."""

from datetime import datetime
from time import perf_counter

from evaluation.baselines.base import BaseAdapter
from evaluation.baselines.contracts import BaselineSettings, ModelServices
from evaluation.harness.config import HarnessConfig
from evaluation.harness.models import QueryMode, RetrievalResult


class B1FullHistoryAdapter(BaseAdapter):
    def __init__(self, config: HarnessConfig, settings: BaselineSettings, services: ModelServices) -> None:
        super().__init__(config, settings, services)
        if self.config.system_id != "B1":
            raise ValueError("b1_system_identity_required")

    def retrieve(
        self, user_id: str, query: str, mode: QueryMode, as_of: datetime, token_budget: int
    ) -> RetrievalResult:
        question = self._question(user_id, query, mode, as_of, token_budget)
        started = perf_counter()
        result, usage = self._run("retrieve", lambda: self._history_retrieval(question, self._state(user_id).turns))
        result = result.model_copy(update={"usage": usage, "retrieval_latency_ms": (perf_counter() - started) * 1000})
        return self._remember(question, as_of, result)
