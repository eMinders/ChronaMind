"""B0: context only from the explicitly declared current question session."""

from datetime import datetime
from time import perf_counter

from evaluation.baselines.base import BaseAdapter
from evaluation.baselines.contracts import BaselineSettings, ModelServices
from evaluation.harness.config import HarnessConfig
from evaluation.harness.models import QueryMode, RetrievalResult


class B0SessionAdapter(BaseAdapter):
    def __init__(self, config: HarnessConfig, settings: BaselineSettings, services: ModelServices) -> None:
        super().__init__(config, settings, services)
        if self.config.system_id != "B0":
            raise ValueError("b0_system_identity_required")

    def retrieve(
        self, user_id: str, query: str, mode: QueryMode, as_of: datetime, token_budget: int
    ) -> RetrievalResult:
        question = self._question(user_id, query, mode, as_of, token_budget)
        started = perf_counter()
        # Public benchmark questions have no source session assignment. None is
        # an independent question session, never the final supplied history.
        turns = [
            turn
            for turn in self._state(user_id).turns
            if question.session_id is not None and turn.session_id == question.session_id
        ]
        result, usage = self._run("retrieve", lambda: self._history_retrieval(question, turns))
        result = result.model_copy(update={"usage": usage, "retrieval_latency_ms": (perf_counter() - started) * 1000})
        return self._remember(question, as_of, result)
