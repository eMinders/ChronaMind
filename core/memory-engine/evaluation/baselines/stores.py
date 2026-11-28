"""User- and replay-generation-scoped raw-turn storage for the RL-031 baselines."""

import json
import re
from math import hypot, isfinite
from typing import Protocol
from uuid import uuid4

import psycopg
from psycopg import Connection, sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
from pydantic import Field, SecretStr, field_validator

from evaluation.harness.models import DeletionScope, Record, TurnRecord


class RawChunk(Record):
    """One raw source slice; original role/time/speaker/provenance stays intact."""

    chunk_id: str = Field(min_length=1)
    turn: TurnRecord
    content: str
    embedding: tuple[float, ...]

    @field_validator("embedding")
    @classmethod
    def validate_embedding(cls, vector: tuple[float, ...]) -> tuple[float, ...]:
        norm = hypot(*vector)
        if len(vector) != 384 or not all(isfinite(value) for value in vector) or not isfinite(norm) or norm == 0:
            raise ValueError("finite_nonzero_384_vector_required")
        return vector


class RawStore(Protocol):
    """Storage operations must run within their adapter's accounted methods."""

    def put(self, namespace: str, user_id: str, chunks: tuple[RawChunk, ...]) -> None: ...
    def list(self, namespace: str, user_id: str) -> tuple[RawChunk, ...]: ...
    def lexical(self, namespace: str, user_id: str, query: str, limit: int) -> tuple[str, ...]: ...
    def delete(self, namespace: str, user_id: str, scope: DeletionScope, target_ref: str) -> int: ...
    def close(self) -> None: ...


def _scope(namespace: str, user_id: str) -> None:
    if not namespace.strip() or not user_id.strip():
        raise ValueError("explicit_raw_store_scope_required")


def _batch(namespace: str, user_id: str, chunks: tuple[RawChunk, ...]) -> None:
    _scope(namespace, user_id)
    if any(chunk.turn.user_id != user_id for chunk in chunks):
        raise ValueError("raw_chunk_user_mismatch")
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise ValueError("duplicate_raw_chunk_id")


def _deletion(scope: DeletionScope, target_ref: str) -> None:
    if scope not in {"all", "memory"}:
        raise ValueError("unsupported_raw_deletion_scope")
    if not target_ref.strip():
        raise ValueError("explicit_raw_deletion_target_required")


class MemoryRawStore:
    """Exact local raw storage for dense retrieval; never pretends to implement FTS."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], tuple[RawChunk, ...]] = {}
        self._closed = False

    def _ready(self) -> None:
        if self._closed:
            raise ValueError("raw_store_closed")

    def put(self, namespace: str, user_id: str, chunks: tuple[RawChunk, ...]) -> None:
        self._ready()
        _batch(namespace, user_id, chunks)
        key = (namespace, user_id)
        existing = self._items.get(key, ())
        ids = {chunk.chunk_id for chunk in existing}
        if any(chunk.chunk_id in ids for chunk in chunks):
            raise ValueError("duplicate_raw_chunk_id")
        self._items[key] = existing + chunks

    def list(self, namespace: str, user_id: str) -> tuple[RawChunk, ...]:
        self._ready()
        _scope(namespace, user_id)
        return self._items.get((namespace, user_id), ())

    def lexical(self, namespace: str, user_id: str, query: str, limit: int) -> tuple[str, ...]:
        self._ready()
        _scope(namespace, user_id)
        raise NotImplementedError("postgresql_fts_required")

    def delete(self, namespace: str, user_id: str, scope: DeletionScope, target_ref: str) -> int:
        self._ready()
        _scope(namespace, user_id)
        _deletion(scope, target_ref)
        key = (namespace, user_id)
        existing = self._items.get(key, ())
        selected = tuple(chunk for chunk in existing if scope == "all" or chunk.turn.turn_id == target_ref)
        selected_ids = {chunk.chunk_id for chunk in selected}
        remaining = tuple(chunk for chunk in existing if chunk.chunk_id not in selected_ids)
        if remaining:
            self._items[key] = remaining
        else:
            self._items.pop(key, None)
        return len({chunk.turn.turn_id for chunk in selected})

    def close(self) -> None:
        self._items.clear()
        self._closed = True


class PostgresRawStore:
    """Lazy isolated schema, actual English FTS, and exact Python dense-scan inputs.

    Only explicit dedicated CME connections are accepted. No extension, database,
    role, or product schema is created or altered. The owning adapter controls
    charged operation placement; construction/reset never connect to PostgreSQL.
    """

    def __init__(self, dsn: str, *, schema: str | None = None) -> None:
        selected = schema if schema is not None else f"cme_baseline_{uuid4().hex}"
        if re.fullmatch(r"cme_baseline_[a-f0-9]{32}", selected) is None:
            raise ValueError("dedicated_baseline_schema_required")
        try:
            params = conninfo_to_dict(dsn)
        except psycopg.ProgrammingError:
            raise ValueError("dedicated_cme_database_required") from None
        database, user = params.get("dbname"), params.get("user")
        if (
            not isinstance(database, str)
            or not database.startswith("cme_")
            or not isinstance(user, str)
            or not user.startswith("cme_")
            or params.get("host") not in {"127.0.0.1", "localhost", "::1"}
            or not params.get("password")
            or "service" in params
            or "hostaddr" in params
        ):
            raise ValueError("dedicated_cme_database_required")
        self.schema = selected
        self._dsn = SecretStr(dsn)
        self._owner_token = uuid4().hex
        self._created = False
        self._closed = False

    def _connect(self) -> Connection:
        return psycopg.connect(
            self._dsn.get_secret_value(),
            connect_timeout=10,
            options="-c statement_timeout=10000 -c lock_timeout=3000",
        )

    def _ready(self) -> None:
        if self._closed:
            raise ValueError("raw_store_closed")

    def _initialize(self, connection: Connection) -> None:
        # Deliberately no IF NOT EXISTS: collisions never adopt another owner's data.
        name = sql.Identifier(self.schema)
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(name))
        connection.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(name))
        connection.execute(sql.SQL("CREATE TABLE {}.owner (token text PRIMARY KEY)").format(name))
        connection.execute(sql.SQL("INSERT INTO {}.owner(token) VALUES (%s)").format(name), (self._owner_token,))
        connection.execute(
            sql.SQL(
                "CREATE TABLE {}.raw_chunks (namespace text NOT NULL, user_id text NOT NULL, "
                "chunk_id text NOT NULL, source_turn_id text NOT NULL, turn_json jsonb NOT NULL, "
                "content text NOT NULL, embedding double precision[] NOT NULL CHECK (cardinality(embedding)=384), "
                "sequence bigint GENERATED ALWAYS AS IDENTITY, PRIMARY KEY(namespace,user_id,chunk_id))"
            ).format(name)
        )
        connection.execute(
            sql.SQL("CREATE INDEX raw_chunks_fts ON {}.raw_chunks USING gin (to_tsvector('english', content))").format(
                name
            )
        )
        connection.execute(
            sql.SQL("CREATE INDEX raw_chunks_source ON {}.raw_chunks(namespace,user_id,source_turn_id)").format(name)
        )

    def put(self, namespace: str, user_id: str, chunks: tuple[RawChunk, ...]) -> None:
        self._ready()
        _batch(namespace, user_id, chunks)
        if not chunks:
            return
        try:
            with self._connect() as connection:
                if not self._created:
                    self._initialize(connection)
                statement = sql.SQL(
                    "INSERT INTO {}.raw_chunks(namespace,user_id,chunk_id,source_turn_id,turn_json,content,embedding) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)"
                ).format(sql.Identifier(self.schema))
                with connection.cursor() as cursor:
                    cursor.executemany(
                        statement,
                        (
                            (
                                namespace,
                                user_id,
                                chunk.chunk_id,
                                chunk.turn.turn_id,
                                Jsonb(chunk.turn.model_dump(mode="json")),
                                chunk.content,
                                list(chunk.embedding),
                            )
                            for chunk in chunks
                        ),
                    )
            self._created = True
        except psycopg.errors.UniqueViolation:
            raise ValueError("duplicate_raw_chunk_id") from None

    def list(self, namespace: str, user_id: str) -> tuple[RawChunk, ...]:
        self._ready()
        _scope(namespace, user_id)
        if not self._created:
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                sql.SQL(
                    "SELECT chunk_id,turn_json,content,embedding FROM {}.raw_chunks "
                    "WHERE namespace=%s AND user_id=%s ORDER BY sequence"
                ).format(sql.Identifier(self.schema)),
                (namespace, user_id),
            ).fetchall()
        return tuple(
            RawChunk(
                chunk_id=chunk_id,
                turn=TurnRecord.model_validate_json(json.dumps(turn_json)),
                content=content,
                embedding=tuple(embedding),
            )
            for chunk_id, turn_json, content, embedding in rows
        )

    def lexical(self, namespace: str, user_id: str, query: str, limit: int) -> tuple[str, ...]:
        self._ready()
        _scope(namespace, user_id)
        if type(limit) is not int or limit < 1:
            raise ValueError("positive_lexical_limit_required")
        if not self._created:
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                sql.SQL(
                    "SELECT chunk_id FROM {}.raw_chunks WHERE namespace=%s AND user_id=%s "
                    "AND to_tsvector('english', content) @@ plainto_tsquery('english', %s) "
                    "ORDER BY ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', %s)) DESC, "
                    "chunk_id ASC LIMIT %s"
                ).format(sql.Identifier(self.schema)),
                (namespace, user_id, query, query, limit),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def delete(self, namespace: str, user_id: str, scope: DeletionScope, target_ref: str) -> int:
        self._ready()
        _scope(namespace, user_id)
        _deletion(scope, target_ref)
        if not self._created:
            return 0
        predicate = sql.SQL("") if scope == "all" else sql.SQL(" AND source_turn_id=%s")
        parameters = (namespace, user_id) if scope == "all" else (namespace, user_id, target_ref)
        with self._connect() as connection:
            self._verify_owner(connection)
            rows = connection.execute(
                sql.SQL(
                    "DELETE FROM {}.raw_chunks WHERE namespace=%s AND user_id=%s{} RETURNING source_turn_id"
                ).format(sql.Identifier(self.schema), predicate),
                parameters,
            ).fetchall()
        return len({row[0] for row in rows})

    def _verify_owner(self, connection: Connection) -> None:
        marker = connection.execute(
            sql.SQL("SELECT token FROM {}.owner").format(sql.Identifier(self.schema))
        ).fetchall()
        if marker != [(self._owner_token,)]:
            raise ValueError("baseline_schema_ownership_mismatch")

    def close(self) -> None:
        """Drop only this object's successfully created, ownership-checked schema."""
        if self._closed:
            return
        if self._created:
            with self._connect() as connection:
                self._verify_owner(connection)
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self._closed = True
        self._created = False
