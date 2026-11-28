"""Owned replay data is bounded while reset/release hooks remain local."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evaluation.baselines.b1_full_history import B1FullHistoryAdapter
from evaluation.baselines.b2_dense import B2DenseAdapter
from evaluation.baselines.stores import MemoryRawStore
from evaluation.baselines.tests.helpers import FakeServices, config, settings, turn
from evaluation.baselines.vendor import B4GraphitiAdapter, VendorProcess
from evaluation.harness.budget import AccountedOperationError
from evaluation.harness.models import QuestionRecord

CLOCK = datetime(2020, 1, 2, tzinfo=UTC)


class ScopedStore(MemoryRawStore):
    def __init__(self, services: FakeServices) -> None:
        super().__init__()
        self.services = services
        self.fail_retirement = False

    def delete(self, namespace, user_id, scope, target_ref):
        assert self.services.current is not None, "retirement escaped the accounted operation"
        if self.fail_retirement:
            raise RuntimeError("synthetic_retirement_failure")
        return super().delete(namespace, user_id, scope, target_ref)


def dense():
    selected = settings()
    services = FakeServices(selected)
    store = ScopedStore(services)
    adapter = B2DenseAdapter(config("B2", selected), selected, services, store)
    adapter.set_clock(CLOCK)
    return adapter, services, store


def test_repeated_reset_reclaims_old_raw_generations_without_io_in_reset() -> None:
    adapter, services, store = dense()
    try:
        for index in range(25):
            before = len(store._items)
            adapter.reset_user("same")
            assert len(store._items) == before and services.current is None
            adapter.ingest_turn(turn("same-turn", "X" * 200, user="same"))
            assert len(adapter._users["same"].turns) == 1
            assert len(store._items) == 1, f"old generation retained after replay {index}"
        # Repeated resets without any I/O cannot queue nonexistent backend data.
        for _ in range(100):
            adapter.reset_user("same")
        assert len(adapter._retired) == 1
        adapter.drain_queue("same")
        assert not store._items and not adapter._retired
    finally:
        adapter.close()


def test_completed_distinct_users_release_local_state_and_preserve_active_foreign_data() -> None:
    adapter, _, store = dense()
    try:
        adapter.reset_user("active")
        adapter.ingest_turn(turn("active-turn", "active content", user="active"))
        active_scope = adapter._state("active").generation
        for index in range(25):
            user = f"case-{index}"
            adapter.reset_user(user)
            adapter.ingest_turn(turn("reused-turn", "case content", user=user))
            assert len(store._items) == 2
            adapter.release_user(user)
            assert set(adapter._users) == {"active"}
            assert len(adapter._retired) == 1
            assert store.list(active_scope, "active")[0].turn.content == "active content"
        adapter.drain_queue("active")
        assert set(store._items) == {(active_scope, "active")}
    finally:
        adapter.close()


def test_failed_retirement_prevents_next_ingest_and_retains_retryable_scope() -> None:
    adapter, services, store = dense()
    try:
        adapter.reset_user("user")
        adapter.ingest_turn(turn("old", "old content"))
        original = adapter._state("user").generation
        adapter.reset_user("user")
        store.fail_retirement = True
        before = len(services.embedding_requests)
        with pytest.raises(AccountedOperationError):
            adapter.ingest_turn(turn("new", "new content"))
        assert len(services.embedding_requests) == before
        assert adapter._state("user").turns == []
        assert store.list(original, "user") and original in adapter._retired
        store.fail_retirement = False
        adapter.ingest_turn(turn("new", "new content"))
        assert store.list(original, "user") == () and not adapter._retired
        assert len(store._items) == 1
    finally:
        adapter.close()


def test_history_diagnostics_fold_completed_generations_without_losing_deduplicated_totals() -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B1FullHistoryAdapter(config("B1", selected), selected, services)
    adapter.set_clock(CLOCK)
    for index in range(30):
        user = f"case-{index}"
        adapter.reset_user(user)
        adapter.ingest_turn(turn("same-turn", "content", user=user))
        probe = QuestionRecord(question_id="same-question", user_id=user, question="What is known?")
        for _ in range(2):
            adapter.prepare_question(probe)
            adapter.retrieve(user, probe.question, "current", CLOCK, adapter.config.B_mem)
        adapter.release_user(user)
        assert not adapter._users and not adapter._questions and not adapter._prepared and not adapter._history_queries
        totals = adapter.adapter_metadata()["history_truncation"]
        assert isinstance(totals, dict) and totals["question_cases"] == index + 1
    assert not adapter._retired


def test_vendor_retirement_scopes_provenance_and_runs_before_next_dispatch(monkeypatch) -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B4GraphitiAdapter(
        config("B4", selected),
        selected,
        services,
        python=Path(sys.executable),
        graph={"host": "127.0.0.1", "port": 16389},
    )
    data = {}
    calls = []

    def operation(request):
        assert services.current is not None
        calls.append(request["operation"])
        tenant = request["tenant"]
        if request["operation"] == "retire":
            data.pop(tenant)
            return {"retired": True}
        if request["operation"] == "ingest":
            data[tenant] = request["turn"]["turn_id"]
            return {"episode_id": "same-episode-id"}
        return [{"id": "edge", "content": "fiction", "episode_ids": ["same-episode-id"]}]

    monkeypatch.setattr(adapter._worker, "operation", operation)
    monkeypatch.setattr(adapter._worker, "close", data.clear)
    adapter.set_clock(CLOCK)
    try:
        for user in ("active", "retiring"):
            adapter.reset_user(user)
            adapter.ingest_turn(turn(user + "-turn", "content", user=user))
        assert len(adapter._episodes) == 2
        retiring = adapter._state("retiring").generation
        adapter.release_user("retiring")
        assert retiring not in adapter._episodes and len(data) == 2
        probe = QuestionRecord(question_id="q", user_id="active", question="What is known?")
        adapter.prepare_question(probe)
        result = adapter.retrieve("active", probe.question, "current", CLOCK, adapter.config.B_mem)
        assert calls[-2:] == ["retire", "retrieve"]
        assert result.items[0].evidence_turn_ids == ("active-turn",)
        assert len(data) == len(adapter._episodes) == len(adapter._materialized) == 1
    finally:
        adapter.close()
    assert not adapter._users and not adapter._episodes and not adapter._retired and not data


@pytest.mark.parametrize("bad_reply", [False, True])
def test_parent_releases_graph_ownership_only_after_exact_retirement_ack(monkeypatch, bad_reply) -> None:
    selected = settings()
    services = FakeServices(selected)
    worker = VendorProcess(
        "b4_graphiti", Path(sys.executable), selected, services, {"host": "127.0.0.1", "port": 16389}
    )
    retired, active = "cme_" + "a" * 64, "cme_" + "b" * 64
    worker._graph_tenants.update((retired, active))
    recovered = []
    monkeypatch.setattr(worker, "_start", lambda deadline: None)
    monkeypatch.setattr(worker, "_send", lambda message: None)
    monkeypatch.setattr(worker, "_receive", lambda deadline: {"type": "result", "result": {"retired": not bad_reply}})
    # Failed acknowledgement closes the whole poisoned adapter and recovers
    # every still-owned graph; an acknowledged retirement removes only its name.
    if bad_reply:
        worker._cleanup_required = True
    monkeypatch.setattr(worker, "_recover_graphs", lambda: recovered.append(set(worker._graph_tenants)))
    if bad_reply:
        with pytest.raises(ValueError, match="acknowledgement"):
            worker.operation({"operation": "retire", "tenant": retired})
        assert recovered == [{retired, active}]
    else:
        assert worker.operation({"operation": "retire", "tenant": retired}) == {"retired": True}
        assert worker._graph_tenants == {active} and not recovered
