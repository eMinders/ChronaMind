"""Storage boundaries and optional disposable PostgreSQL FTS integration."""

import os
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr, ValidationError

from custom_memory_engine.config import TestDatabaseConfig as DisposableDatabaseConfig
from evaluation.baselines import stores
from evaluation.baselines.b2h_hybrid import B2hHybridAdapter
from evaluation.baselines.context import answer_messages
from evaluation.baselines.tests.helpers import FakeServices, config, settings, turn, vector
from evaluation.harness.budget import AccountedOperationError
from evaluation.harness.models import QuestionRecord, TurnRecord

TEST_DSN = "postgresql://cme_test:fixture-only@127.0.0.1:5433/cme_compatibility"


def store_type(name: str) -> Any:
    result = getattr(stores, name, None)
    assert result is not None, f"required real storage API {name} is missing"
    return result


def chunk(chunk_id: str, turn_id: str = "turn", *, user: str = "user", text: str = "Running fast") -> Any:
    return store_type("RawChunk")(
        chunk_id=chunk_id,
        turn=TurnRecord(
            user_id=user,
            session_id="session",
            turn_id=turn_id,
            role="assistant",
            source_speaker="fictional speaker",
            content=text,
            occurred_at=datetime(2020, 1, 1, tzinfo=UTC),
        ),
        content=text,
        embedding=(1.0,) + (0.0,) * 383,
    )


def test_memory_store_preserves_chunks_source_and_explicit_insertion_order() -> None:
    store = store_type("MemoryRawStore")()
    first, second = chunk("z"), chunk("a", "second")
    store.put("generation", "user", (first, second))
    assert store.list("generation", "user") == (first, second)
    assert store.list("generation", "user")[0].turn.source_speaker == "fictional speaker"
    assert store.list("generation", "user")[0].turn.role == "assistant"


def test_memory_store_scopes_same_ids_by_user_and_generation() -> None:
    store = store_type("MemoryRawStore")()
    store.put("one", "user", (chunk("same"),))
    store.put("two", "user", (chunk("same", text="new generation"),))
    store.put("one", "other", (chunk("same", user="other", text="other user"),))
    assert store.list("one", "user")[0].content == "Running fast"
    assert store.list("two", "user")[0].content == "new generation"
    assert store.list("one", "other")[0].content == "other user"
    assert store.list("missing", "user") == ()


def test_memory_store_duplicate_batch_and_foreign_user_are_rejected_atomically() -> None:
    store = store_type("MemoryRawStore")()
    existing = chunk("one")
    store.put("generation", "user", (existing,))
    with pytest.raises(ValueError, match="duplicate_raw_chunk_id"):
        store.put("generation", "user", (chunk("new"), existing))
    assert store.list("generation", "user") == (existing,)
    with pytest.raises(ValueError, match="raw_chunk_user_mismatch"):
        store.put("generation", "user", (chunk("other", user="other"),))
    with pytest.raises(ValueError, match="duplicate_raw_chunk_id"):
        store.put("generation", "user", (chunk("new"), chunk("new")))
    assert store.list("generation", "user") == (existing,)


def test_memory_delete_removes_every_chunk_of_one_turn_and_only_its_scope() -> None:
    store = store_type("MemoryRawStore")()
    store.put("one", "user", (chunk("a"), chunk("b"), chunk("c", "survivor")))
    store.put("two", "user", (chunk("a"),))
    store.put("one", "other", (chunk("a", user="other"),))
    assert store.delete("one", "user", "memory", "turn") == 1
    assert [item.chunk_id for item in store.list("one", "user")] == ["c"]
    assert len(store.list("two", "user")) == len(store.list("one", "other")) == 1
    assert store.delete("one", "user", "all", "unused") == 1
    assert store.list("one", "user") == ()
    with pytest.raises(ValueError, match="unsupported_raw_deletion_scope"):
        store.delete("two", "user", "topic", "fictional")


@pytest.mark.parametrize("method", ["put", "list", "lexical", "delete"])
def test_missing_user_or_namespace_cannot_request_global_store(method: str) -> None:
    store = store_type("MemoryRawStore")()
    arguments = {"put": ((),), "list": (), "lexical": ("run", 2), "delete": ("all", "unused")}[method]
    for namespace, user in (("", "user"), ("generation", " ")):
        with pytest.raises(ValueError, match="explicit_raw_store_scope_required"):
            getattr(store, method)(namespace, user, *arguments)


@pytest.mark.parametrize(
    "vector",
    [
        (0.0,) * 384,
        (1.0,) * 383,
        (float("nan"),) + (0.0,) * 383,
        (float("inf"),) + (0.0,) * 383,
        (1e308,) * 384,
    ],
)
def test_raw_chunks_reject_invalid_cosine_vectors(vector: tuple[float, ...]) -> None:
    original = chunk("valid")
    with pytest.raises(ValidationError):
        store_type("RawChunk").model_validate({**original.model_dump(), "embedding": vector})


def test_memory_lexical_is_explicitly_unsupported() -> None:
    with pytest.raises(NotImplementedError, match="postgresql_fts_required"):
        store_type("MemoryRawStore")().lexical("generation", "user", "run", 2)


def test_memory_close_erases_owned_state_and_refuses_reuse() -> None:
    store = store_type("MemoryRawStore")()
    store.put("generation", "user", (chunk("a"),))
    store.close()
    store.close()
    with pytest.raises(ValueError, match="raw_store_closed"):
        store.list("generation", "user")


def test_postgres_constructor_empty_queries_and_close_are_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: pytest.fail("unaccounted lazy-store connection"))
    store = store_type("PostgresRawStore")(TEST_DSN)
    assert store.schema.startswith("cme_baseline_") and len(store.schema) == 45
    assert store.list("generation", "user") == ()
    assert store.lexical("generation", "user", "run", 3) == ()
    assert store.delete("generation", "user", "all", "unused") == 0
    store.put("generation", "user", ())
    store.close()
    store.close()


@pytest.mark.parametrize("schema", ["public", "cme_baseline_x", "cme_baseline_" + "a" * 31, 'x"; DROP SCHEMA public'])
def test_postgres_schema_identifier_is_restricted_before_connect(schema: str) -> None:
    with pytest.raises(ValueError, match="dedicated_baseline_schema_required"):
        store_type("PostgresRawStore")(TEST_DSN, schema=schema)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://product:fixture-only@127.0.0.1/app",
        "dbname=product user=cme_test host=127.0.0.1 password=fixture-only",
        "dbname=cme_compatibility user=product host=127.0.0.1 password=fixture-only",
        "dbname=cme_compatibility user=cme_test host=127.0.0.1 hostaddr=203.0.113.1 password=fixture-only",
        "service=product",
    ],
)
def test_postgres_rejects_product_or_ambiguous_connection_configuration(dsn: str) -> None:
    with pytest.raises(ValueError, match="dedicated_cme_database_required"):
        store_type("PostgresRawStore")(dsn)


def test_postgres_connection_secrets_do_not_enter_repr() -> None:
    store = store_type("PostgresRawStore")(TEST_DSN)
    assert "fixture-only" not in repr(store)


@pytest.mark.integration
def test_real_postgres_english_fts_tenant_generation_delete_and_schema_ownership() -> None:
    raw_dsn = os.environ.get("CME_BASELINE_TEST_DATABASE_URL")
    if raw_dsn is None:
        pytest.skip("root coordinates the dedicated disposable baseline PostgreSQL run")
    guarded = DisposableDatabaseConfig(database_url=SecretStr(raw_dsn))
    dsn = guarded.database_url.get_secret_value()
    store_class = store_type("PostgresRawStore")
    store = store_class(dsn)
    schema = store.schema
    try:
        items = (
            chunk("z", text="The runner was running swiftly"),
            chunk("a", "second", text="We run swiftly"),
            chunk("none", "third", text="Oranges are delicious"),
        )
        store.put("g1", "user", items)
        store.put("g2", "user", (chunk("a", text="running again"),))
        store.put("g1", "other", (chunk("a", user="other", text="running elsewhere"),))
        assert store.list("g1", "user") == items
        assert set(store.lexical("g1", "user", "run", 10)) == {"a", "z"}
        assert store.lexical("g1", "user", "the and of", 10) == ()
        assert store.lexical("g1", "user", "completelyunmatched", 10) == ()
        # Compare the real store to the independently authored normative expression.
        with psycopg.connect(dsn) as connection:
            expected = connection.execute(
                sql.SQL(
                    "SELECT chunk_id FROM {}.raw_chunks WHERE namespace=%s AND user_id=%s "
                    "AND to_tsvector('english', content) @@ plainto_tsquery('english', %s) "
                    "ORDER BY ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', %s)) DESC, "
                    "chunk_id ASC LIMIT %s"
                ).format(sql.Identifier(schema)),
                ("g1", "user", "run", "run", 10),
            ).fetchall()
        assert store.lexical("g1", "user", "run", 10) == tuple(row[0] for row in expected)
        with pytest.raises(ValueError, match="duplicate_raw_chunk_id"):
            store.put("g1", "user", (chunk("fresh"), items[0]))
        assert store.list("g1", "user") == items
        assert store.delete("g1", "user", "memory", "turn") == 1
        assert {item.turn.turn_id for item in store.list("g1", "user")} == {"second", "third"}
        assert len(store.list("g2", "user")) == len(store.list("g1", "other")) == 1
        # A second object must not adopt or destroy an existing schema.
        collision = store_class(dsn, schema=schema)
        with pytest.raises(psycopg.errors.DuplicateSchema):
            collision.put("g3", "user", (chunk("collision"),))
        collision.close()
        assert len(store.list("g1", "user")) == 2
        # Cleanup must also refuse an ownership marker changed after creation.
        with psycopg.connect(dsn) as connection:
            marker_row = connection.execute(
                sql.SQL("SELECT token FROM {}.owner").format(sql.Identifier(schema))
            ).fetchone()
            assert marker_row is not None
            original_marker = marker_row[0]
            connection.execute(
                sql.SQL("UPDATE {}.owner SET token=%s").format(sql.Identifier(schema)), ("other-owner",)
            )
        try:
            with pytest.raises(ValueError, match="baseline_schema_ownership_mismatch"):
                store.close()
        finally:
            with psycopg.connect(dsn) as connection:
                connection.execute(
                    sql.SQL("UPDATE {}.owner SET token=%s").format(sql.Identifier(schema)), (original_marker,)
                )
    finally:
        store.close()
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)).fetchone() is None


@pytest.mark.integration
def test_real_postgres_replay_retirement_bounds_rows_and_rechecks_schema_ownership() -> None:
    raw_dsn = os.environ.get("CME_BASELINE_TEST_DATABASE_URL")
    if raw_dsn is None:
        pytest.skip("root coordinates the dedicated disposable baseline PostgreSQL run")
    dsn = DisposableDatabaseConfig(database_url=SecretStr(raw_dsn)).database_url.get_secret_value()
    selected = settings()
    services = FakeServices(selected)
    store = stores.PostgresRawStore(dsn)
    adapter = B2hHybridAdapter(config("B2h", selected), selected, services, store)
    adapter.set_clock(datetime(2020, 1, 2, tzinfo=UTC))
    try:
        adapter.reset_user("active")
        adapter.ingest_turn(turn("active-turn", "active retained content", user="active"))
        active_generation = adapter._state("active").generation
        store.put("foreign-generation", "foreign-user", (chunk("foreign", user="foreign-user"),))
        for index in range(20):
            user = f"case-{index}"
            adapter.reset_user(user)
            adapter.ingest_turn(turn("same-turn", "case content", user=user))
            with psycopg.connect(dsn) as connection:
                scopes = connection.execute(
                    sql.SQL("SELECT DISTINCT namespace,user_id FROM {}.raw_chunks").format(
                        sql.Identifier(store.schema)
                    )
                ).fetchall()
            assert len(scopes) == 3
            adapter.release_user(user)
        # A changed marker must prevent pending retirement from deleting rows.
        pending = dict(adapter._retired)
        with psycopg.connect(dsn) as connection:
            connection.execute(
                sql.SQL("UPDATE {}.owner SET token='foreign-owner'").format(sql.Identifier(store.schema))
            )
        try:
            with pytest.raises(AccountedOperationError, match="baseline_operation_failed"):
                adapter.drain_queue("active")
            assert adapter._retired == pending
        finally:
            with psycopg.connect(dsn) as connection:
                connection.execute(
                    sql.SQL("UPDATE {}.owner SET token=%s").format(sql.Identifier(store.schema)), (store._owner_token,)
                )
        adapter.drain_queue("active")
        assert store.list(active_generation, "active")
        assert store.list("foreign-generation", "foreign-user")
        with psycopg.connect(dsn) as connection:
            scopes = connection.execute(
                sql.SQL("SELECT DISTINCT namespace,user_id FROM {}.raw_chunks").format(sql.Identifier(store.schema))
            ).fetchall()
        assert set(scopes) == {(active_generation, "active"), ("foreign-generation", "foreign-user")}
        assert set(adapter._users) == {"active"} and not adapter._retired
    finally:
        adapter.close()


@pytest.mark.integration
def test_real_b2h_adapter_rrf_source_dedup_replay_reset_and_tenant_deletion() -> None:
    """Real adapter and PostgreSQL collaborate; only paid model calls are replaced."""
    raw_dsn = os.environ.get("CME_BASELINE_TEST_DATABASE_URL")
    if raw_dsn is None:
        pytest.skip("root coordinates the dedicated disposable baseline PostgreSQL run")
    guarded = DisposableDatabaseConfig(database_url=SecretStr(raw_dsn))
    dsn = guarded.database_url.get_secret_value()
    selected = settings(retrieval_k=3)
    services = FakeServices(selected)
    services.chunk_size = 180
    store = stores.PostgresRawStore(dsn)
    schema = store.schema
    adapter = B2hHybridAdapter(config("B2h", selected), selected, services, store=store)
    clock = datetime(2020, 1, 2, tzinfo=UTC)
    try:
        adapter.reset_user("user")
        adapter.reset_user("other")
        adapter.set_clock(clock)
        for item, embedding in (
            (turn("a", "oranges delicious"), vector(1.0)),
            (
                turn("b", "running " * 80, speaker="Fictional Bob"),
                vector(0.9, 0.4358898943540673),
            ),
            (turn("c", "run"), vector(0.5, 0.8660254037844386)),
            (turn("foreign", "running other tenant", user="other"), vector(0.5, 0.8660254037844386)),
        ):
            # Every slice of this document has the deliberately assigned vector;
            # splitting the metadata token cannot alter the independent oracle.
            services.vector_for_text = lambda text, query, assigned=embedding: assigned
            adapter.ingest_turn(item)
        services.vector_for_text = lambda text, query: vector(1.0)
        question = QuestionRecord(question_id="q", user_id="user", question="running")
        adapter.prepare_question(question)
        retrieved = adapter.retrieve("user", "running", "current", clock, adapter.config.B_mem)
        with psycopg.connect(dsn) as connection:
            rows = connection.execute(
                sql.SQL(
                    "SELECT source_turn_id FROM {}.raw_chunks WHERE user_id=%s "
                    "AND to_tsvector('english', content) @@ plainto_tsquery('english', %s) "
                    "ORDER BY ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', %s)) DESC, "
                    "chunk_id ASC"
                ).format(sql.Identifier(schema)),
                ("user", "running", "running"),
            ).fetchall()
        lexical = tuple(dict.fromkeys(row[0] for row in rows))
        dense = ("a", "b", "c")
        scores = {source: 1 / (60 + rank) for rank, source in enumerate(dense, 1)}
        for rank, source in enumerate(lexical, 1):
            scores[source] = scores.get(source, 0) + 1 / (60 + rank)
        expected = sorted(scores, key=lambda source: (-scores[source], source))
        assert lexical == ("b", "c")
        assert expected[0] == "b"  # Dense champion has no lexical overlap.
        assert [item.reference_id for item in retrieved.items] == expected
        assert len(retrieved.items) == len({item.reference_id for item in retrieved.items}) == 3
        assert "Fictional Bob" in retrieved.rendered_context
        assert all(item.evidence_turn_ids == (item.reference_id,) for item in retrieved.items)
        adapter.answer("user", question, clock)
        assert services.requests[-1] == answer_messages(selected, question, retrieved.rendered_context, clock)
        # Reset changes the replay generation locally; old rows cannot reappear.
        adapter.reset_user("user")
        adapter.prepare_question(question)
        assert not adapter.retrieve("user", "running", "current", clock, adapter.config.B_mem).items
        adapter.ingest_turn(turn("new", "running fresh generation"))
        assert adapter.delete("user", "all", "unused").deleted_count == 1
        adapter.prepare_question(question)
        assert not adapter.retrieve("user", "running", "current", clock, adapter.config.B_mem).items
        other_question = question.model_copy(update={"question_id": "other-q", "user_id": "other"})
        adapter.prepare_question(other_question)
        other = adapter.retrieve("other", "running", "current", clock, adapter.config.B_mem)
        assert [item.reference_id for item in other.items] == ["foreign"]
    finally:
        store.close()
