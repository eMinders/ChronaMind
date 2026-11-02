"""Atomic, checksum-bound provisioning of the independent RL-040 schema."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, LiteralString, cast

import structlog
from psycopg import AsyncConnection, Error, ProgrammingError, sql
from psycopg.conninfo import conninfo_to_dict
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from rich.console import Console

SCHEMA_SQL = Path(__file__).with_name("001_full_schema.sql")
# Ordered, append-only. A shipped entry is never edited or removed: its source checksum
# is recorded at apply time and any later change is reported as drift (MA-016 X1).
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, "001_full_schema.sql"),
    (2, "002_scope_grants.sql"),
    (3, "003_association_cap.sql"),
    (4, "004_tenant_integrity.sql"),
    (5, "005_axes.sql"),
    (6, "006_facet_floors.sql"),
    (7, "007_lifecycle_guard.sql"),
    (8, "008_source.sql"),
    (9, "009_clocks.sql"),
    (10, "010_structural_tables.sql"),
    (11, "011_scope.sql"),
)
VERSION = MIGRATIONS[-1][0]
OWNER_MARKER = "chronamind:RL-041:v1"
HISTORY_TABLE = "_cme_schema_migrations"
TABLES = (
    "app_user",
    "session",
    "turn",
    "predicate_schema",
    "memory_version",
    "memory_evidence",
    "memory_derivation",
    "memory_embedding",
    "memory_conflict",
    "audit_event",
    "deletion_request",
    "job",
    "scope_grant",
    "clock_watermark",
    "method",
    "association",
    "entity",
    "abstraction_stats",
    "verification",
    "negative_constraint",
)
FUNCTIONS = (
    "clock_now",
    "guard_update",
    "enforce_provenance",
    "enforce_archival_invariant",
    "guard_derivation_coverage",
    "guard_scope_grant_update",
    "enforce_association_cap",
    "sync_memory_version_axes",
    "mark_derived_version_inferred",
    "enforce_lifecycle_transition",
    "sync_memory_evidence_source",
    "sync_memory_version_clocks",
    "enforce_recorded_watermark",
    "guard_method_append_only",
    "assign_memory_version_method",
    "guard_abstraction_stats",
    "guard_verification_append_only",
    "guard_verification_no_delete",
    "create_zero_ledger",
    "guard_negative_constraint",
    "enforce_transition_records",
    "fill_scope_path",
)
logger = structlog.get_logger(__name__)


class MigrationError(RuntimeError):
    """Static public failure information, never a connection string or SQL payload."""

    def __init__(self, code: str, *, sqlstate: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.sqlstate = sqlstate


class MigrationConfig(BaseModel):
    """Explicit research connection; never discover or load a product dotenv file."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    database_url: SecretStr
    schema_name: str = Field(default="cme_engine", pattern=r"^cme_[a-z][a-z0-9_]*$", max_length=63)
    connect_timeout: int = Field(default=5, ge=1, le=60, strict=True)
    statement_timeout_ms: int = Field(default=30_000, ge=1, le=600_000, strict=True)
    lock_timeout_ms: int = Field(default=5_000, ge=1, le=60_000, strict=True)

    @field_validator("database_url")
    @classmethod
    def explicit_research_connection(cls, value: SecretStr) -> SecretStr:
        try:
            parameters = conninfo_to_dict(value.get_secret_value())
        except ProgrammingError:
            raise ValueError("invalid_research_database_connection") from None
        if not all(parameters.get(key) for key in ("host", "user", "dbname")) or parameters["dbname"] in {
            "postgres",
            "template0",
            "template1",
        }:
            raise ValueError("explicit_research_database_required")
        return value

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None, *, schema_name: str = "cme_engine"
    ) -> "MigrationConfig":
        values = os.environ if environ is None else environ
        return cls(database_url=SecretStr(values.get("RL_DATABASE_URL", "")), schema_name=schema_name)


class MigrationStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_name: str
    state: Literal["unapplied", "applied", "pending", "unmanaged", "drifted"]
    version: int | None = None
    checksum_sha256: str | None = None
    reason: str | None = None


class MigrationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["applied", "unchanged", "reset"]
    status: MigrationStatus


def _read_one(filename: str) -> tuple[str, str]:
    try:
        source = SCHEMA_SQL.with_name(filename).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise MigrationError("migration_source_unreadable") from None
    if not source.strip():
        raise MigrationError("migration_source_empty")
    # Universal-newline reading binds the same migration on Windows and Linux.
    return source, hashlib.sha256(source.encode("utf-8")).hexdigest()


def _read_sources() -> tuple[tuple[int, str, str, str], ...]:
    """Return ``(version, filename, source, checksum)`` for every declared migration."""
    return tuple((version, filename, *_read_one(filename)) for version, filename in MIGRATIONS)


def migration_scripts(*, through: int | None = None) -> tuple[tuple[int, str], ...]:
    """Ordered ``(version, source)`` of every shipped migration up to ``through`` (default: all).

    The single source for anything that builds a disposable schema without the runner (integration
    tests, the Suite D harness), so the schema they measure is the schema that ships.
    """
    return tuple(
        (version, source) for version, _, source, _ in _read_sources() if through is None or version <= through
    )


def _read_source() -> tuple[str, str]:
    """Source and checksum of migration 1 (kept for callers that inspect the base schema)."""
    return _read_one(MIGRATIONS[0][1])


# Live structure is checked independently of the ledger's source checksum. Row
# data, statistics, relation sizes and sequence counters intentionally do not enter it.
_CATALOG_QUERIES = (
    """SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
              pg_catalog.pg_get_userbyid(c.relowner)
       FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname=%s ORDER BY c.relname""",
    """SELECT c.relname, a.attnum, a.attname, pg_catalog.format_type(a.atttypid,a.atttypmod),
              a.attnotnull, a.attidentity, a.attgenerated,
              pg_catalog.pg_get_expr(d.adbin,d.adrelid)
       FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
       WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum""",
    """SELECT c.relname, con.conname, con.contype, con.condeferrable, con.condeferred, con.convalidated,
              pg_catalog.pg_get_constraintdef(con.oid,true)
       FROM pg_catalog.pg_constraint con JOIN pg_catalog.pg_namespace n ON n.oid=con.connamespace
       LEFT JOIN pg_catalog.pg_class c ON c.oid=con.conrelid
       WHERE n.nspname=%s ORDER BY c.relname,con.conname""",
    """SELECT c.relname, pg_catalog.pg_get_indexdef(i.indexrelid), i.indisvalid, i.indisready
       FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname=%s ORDER BY c.relname""",
    """SELECT p.proname, pg_catalog.pg_get_functiondef(p.oid)
       FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname=%s ORDER BY p.proname,p.oid""",
    """SELECT c.relname, t.tgname, t.tgenabled, pg_catalog.pg_get_triggerdef(t.oid,true)
       FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""",
    # Internal foreign-key triggers have generated names containing OIDs. Bind
    # their stable identities and enforcement state without persisting those names.
    """SELECT c.relname, con.conname, con.conrelid::pg_catalog.regclass::text,
              t.tgconstrrelid::pg_catalog.regclass::text, t.tgfoid::pg_catalog.regprocedure::text,
              t.tgtype, t.tgenabled, t.tgdeferrable, t.tginitdeferred
       FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       JOIN pg_catalog.pg_constraint con ON con.oid=t.tgconstraint
       WHERE n.nspname=%s AND t.tgisinternal
       ORDER BY c.relname,con.conname,con.conrelid::pg_catalog.regclass::text,
                t.tgconstrrelid::pg_catalog.regclass::text,t.tgfoid::pg_catalog.regprocedure::text,t.tgtype""",
    """SELECT c.relname, p.polname, p.polcmd, p.polpermissive, p.polroles::text,
              pg_catalog.pg_get_expr(p.polqual,p.polrelid), pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)
       FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid=p.polrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname=%s ORDER BY c.relname,p.polname""",
    """SELECT t.typname, t.typtype, t.typnotnull, t.typdefault
       FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
       WHERE n.nspname=%s ORDER BY t.typname""",
)


class MigrationRunner:
    """Own one marked schema; serialize migrations and preserve outside objects."""

    def __init__(self, config: MigrationConfig) -> None:
        self.config = MigrationConfig.model_validate(config.model_dump())

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[AsyncConnection]:
        try:
            async with await AsyncConnection.connect(
                self.config.database_url.get_secret_value(),
                autocommit=True,
                connect_timeout=self.config.connect_timeout,
            ) as connection:
                async with connection.transaction():
                    await connection.execute("SET LOCAL search_path TO pg_catalog, public")
                    for setting, value in (
                        ("statement_timeout", self.config.statement_timeout_ms),
                        ("lock_timeout", self.config.lock_timeout_ms),
                    ):
                        await connection.execute("SELECT pg_catalog.set_config(%s,%s,true)", (setting, str(value)))
                    # One DB-wide migration lock also serializes first extension
                    # bootstrap across different target schemas. No runtime user lock.
                    await connection.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s,%s)", (0x434D4531, 41))
                    yield connection
        except Error as error:
            raise MigrationError("migration_database_failure", sqlstate=error.sqlstate) from None

    async def _scope(self, connection: AsyncConnection) -> None:
        await connection.execute(
            sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(self.config.schema_name))
        )

    async def _forced_relations(self, connection: AsyncConnection) -> list[str]:
        cursor = await connection.execute(
            """SELECT c.relname FROM pg_catalog.pg_class c
               JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname=%s AND c.relkind IN ('r','p') AND c.relforcerowsecurity ORDER BY c.relname""",
            (self.config.schema_name,),
        )
        return [str(row[0]) for row in await cursor.fetchall()]

    async def _release_force(self, connection: AsyncConnection) -> list[sql.Identifier]:
        """Lift ``FORCE ROW LEVEL SECURITY`` on this schema's own relations; return them for restoration.

        ``FORCE`` binds the table owner, and a migration is applied by that owner, so a backfill's unqualified
        ``UPDATE``/``INSERT ... SELECT`` matches zero rows and a following ``SET NOT NULL`` then fails. Migrations 5, 8
        and 9 backfill that way, so a populated schema could not be upgraded by any role that does not bypass RLS. The
        migration sources are checksum-bound and cannot carry the fix, so the runner lifts ``FORCE`` around each
        migration body and restores it before recording the catalog. Only this schema's relations are touched.
        """
        forced = await self._forced_relations(connection)
        relations = [sql.Identifier(self.config.schema_name, name) for name in forced]
        for relation in relations:
            await connection.execute(sql.SQL("ALTER TABLE {} NO FORCE ROW LEVEL SECURITY").format(relation))
        return relations

    async def _restore_force(self, connection: AsyncConnection, relations: list[sql.Identifier]) -> None:
        """Restore ``FORCE``, judging pending deferred events first.

        A migration body ends with trigger events still queued, and ``ALTER TABLE`` is refused while its relation has
        pending events. They are dispatched ``IMMEDIATE`` here and each deferrable trigger is put back to the mode its
        own DDL declares, so a body's deferral state does not leak into the rest of the transaction or the session.
        """
        cursor = await connection.execute(
            """SELECT t.tgname, t.tginitdeferred FROM pg_catalog.pg_trigger t
               JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
               JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname=%s AND NOT t.tgisinternal AND t.tgdeferrable ORDER BY t.tgname""",
            (self.config.schema_name,),
        )
        deferrable = [(str(name), bool(initially_deferred)) for name, initially_deferred in await cursor.fetchall()]
        await connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for name, initially_deferred in deferrable:
            if initially_deferred:
                await connection.execute(
                    sql.SQL("SET CONSTRAINTS {}.{} DEFERRED").format(
                        sql.Identifier(self.config.schema_name), sql.Identifier(name)
                    )
                )
        for relation in relations:
            await connection.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(relation))

    async def _run_source(self, connection: AsyncConnection, source: str, *, lift_force: bool = True) -> None:
        """Execute one migration body, lifting ``FORCE`` first when it can contain a backfill."""
        relations = await self._release_force(connection) if lift_force else []
        cursor = await connection.execute(cast(LiteralString, source), prepare=False, binary=False)
        while cursor.nextset():
            pass
        if relations:
            await self._restore_force(connection, relations)

    async def _catalog(self, connection: AsyncConnection) -> str:
        rows = []
        for query in _CATALOG_QUERIES:
            cursor = await connection.execute(cast(LiteralString, query), (self.config.schema_name,))
            rows.append(await cursor.fetchall())
        cursor = await connection.execute(
            """SELECT e.extname,e.extversion,n.nspname FROM pg_catalog.pg_extension e
               JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
               WHERE e.extname IN ('vector','btree_gist') ORDER BY e.extname"""
        )
        rows.append(await cursor.fetchall())
        return hashlib.sha256(json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()

    async def _status(
        self, connection: AsyncConnection, sources: tuple[tuple[int, str, str, str], ...]
    ) -> MigrationStatus:
        schema = self.config.schema_name
        cursor = await connection.execute(
            """SELECT pg_catalog.obj_description(n.oid,'pg_namespace'),
                      pg_catalog.pg_get_userbyid(n.nspowner)=current_user
               FROM pg_catalog.pg_namespace n WHERE n.nspname=%s""",
            (schema,),
        )
        namespace = await cursor.fetchone()
        if namespace is None:
            return MigrationStatus(schema_name=schema, state="unapplied")
        if namespace != (OWNER_MARKER, True):
            return MigrationStatus(schema_name=schema, state="unmanaged", reason="schema_ownership_required")
        await self._scope(connection)
        try:
            # A malformed history must not abort a read-only status inspection.
            async with connection.transaction():
                cursor = await connection.execute(
                    sql.SQL("SELECT version,filename,checksum_sha256,catalog_sha256 FROM {}.{} ORDER BY 1").format(
                        sql.Identifier(schema), sql.Identifier(HISTORY_TABLE)
                    )
                )
                history = await cursor.fetchall()
        except Error:
            return MigrationStatus(schema_name=schema, state="drifted", reason="invalid_history")
        # History must be a non-empty, gap-free prefix of the declared migration list.
        if not history or len(history) > len(sources):
            return MigrationStatus(schema_name=schema, state="drifted", reason="unsupported_history")
        for row, (version, filename, _, _) in zip(history, sources, strict=False):
            if type(row[0]) is not int or tuple(row[0:2]) != (version, filename):
                return MigrationStatus(schema_name=schema, state="drifted", reason="unsupported_history")
            if not all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for value in row[2:4]):
                return MigrationStatus(schema_name=schema, state="drifted", reason="invalid_history")
        current = history[-1][0]
        fields = {"schema_name": schema, "version": current, "checksum_sha256": history[-1][2]}
        for row, (_, _, _, checksum) in zip(history, sources, strict=False):
            if row[2] != checksum:
                return MigrationStatus(**fields, state="drifted", reason="source_checksum_mismatch")
        if history[-1][3] != await self._catalog(connection):
            return MigrationStatus(**fields, state="drifted", reason="catalog_drift")
        if len(history) < len(sources):
            return MigrationStatus(**fields, state="pending", reason="newer_migrations_available")
        return MigrationStatus(**fields, state="applied")

    async def _record(self, connection: AsyncConnection, version: int, filename: str, checksum: str) -> None:
        catalog = await self._catalog(connection)
        await connection.execute(
            sql.SQL("INSERT INTO {} (version,filename,checksum_sha256,catalog_sha256) VALUES (%s,%s,%s,%s)").format(
                sql.Identifier(HISTORY_TABLE)
            ),
            (version, filename, checksum, catalog),
        )

    async def _apply_from(
        self, connection: AsyncConnection, sources: tuple[tuple[int, str, str, str], ...], start: int
    ) -> None:
        """Apply migrations after version ``start`` in order, recording each one's catalog."""
        await self._scope(connection)
        for version, filename, source, checksum in sources:
            if version <= start:
                continue
            await self._run_source(connection, source)
            await self._record(connection, version, filename, checksum)

    async def _install(self, connection: AsyncConnection, sources: tuple[tuple[int, str, str, str], ...]) -> None:
        _, base_filename, source, checksum = sources[0]
        # Install shared extensions before making the managed schema the creation
        # target. They never belong to, or get erased by, clean reset.
        for extension in ("vector", "btree_gist"):
            await connection.execute(
                sql.SQL("CREATE EXTENSION IF NOT EXISTS {} WITH SCHEMA public").format(sql.Identifier(extension))
            )
        cursor = await connection.execute(
            """SELECT e.extname,n.nspname FROM pg_catalog.pg_extension e
               JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
               WHERE e.extname IN ('vector','btree_gist') ORDER BY e.extname"""
        )
        if await cursor.fetchall() != [("btree_gist", "public"), ("vector", "public")]:
            raise MigrationError("extensions_must_be_in_public")
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.config.schema_name)))
        await connection.execute(
            sql.SQL("COMMENT ON SCHEMA {} IS {}").format(
                sql.Identifier(self.config.schema_name), sql.Literal(OWNER_MARKER)
            )
        )
        await self._scope(connection)
        await self._run_source(connection, source)
        await connection.execute(
            sql.SQL("""CREATE TABLE {} (
                version integer PRIMARY KEY, filename text NOT NULL,
                checksum_sha256 text NOT NULL CHECK (checksum_sha256 ~ '^[a-f0-9]{{64}}$'),
                catalog_sha256 text NOT NULL CHECK (catalog_sha256 ~ '^[a-f0-9]{{64}}$'),
                applied_at timestamptz NOT NULL DEFAULT transaction_timestamp()
            )""").format(sql.Identifier(HISTORY_TABLE))
        )
        await self._record(connection, sources[0][0], base_filename, checksum)
        await self._apply_from(connection, sources, sources[0][0])

    async def status(self) -> MigrationStatus:
        sources = _read_sources()
        async with self._connection() as connection:
            return await self._status(connection, sources)

    async def apply(self) -> MigrationResult:
        sources = _read_sources()
        async with self._connection() as connection:
            status = await self._status(connection, sources)
            if status.state == "applied":
                return MigrationResult(action="unchanged", status=status)
            if status.state == "pending" and status.version is not None:
                await self._apply_from(connection, sources, status.version)
            elif status.state == "unapplied":
                await self._install(connection, sources)
            else:
                raise MigrationError(status.reason or "schema_not_applicable")
            status = await self._status(connection, sources)
            if status.state != "applied":
                raise MigrationError("migration_verification_failed")
        logger.info(
            "schema_migration_completed", action="applied", schema_name=self.config.schema_name, version=status.version
        )
        return MigrationResult(action="applied", status=status)

    async def reset(self, *, confirm_schema: str) -> MigrationResult:
        if confirm_schema != self.config.schema_name:
            raise MigrationError("reset_schema_confirmation_required")
        sources = _read_sources()
        async with self._connection() as connection:
            status = await self._status(connection, sources)
            if status.state not in {"unapplied", "applied", "pending"} and not (
                status.state == "drifted" and status.reason == "catalog_drift"
            ):
                raise MigrationError(status.reason or "schema_not_resettable")
            if status.state != "unapplied":
                # A grouped RESTRICT drop permits internal FKs while refusing
                # outside dependencies. Never CASCADE into another namespace.
                targets = sql.SQL(", ").join(
                    sql.Identifier(self.config.schema_name, table) for table in (*TABLES, HISTORY_TABLE)
                )
                await connection.execute(sql.SQL("DROP TABLE IF EXISTS {} RESTRICT").format(targets))
                functions = sql.SQL(", ").join(
                    sql.SQL("{}()").format(sql.Identifier(self.config.schema_name, name)) for name in FUNCTIONS
                )
                await connection.execute(sql.SQL("DROP FUNCTION IF EXISTS {} RESTRICT").format(functions))
                await connection.execute(
                    sql.SQL("DROP SCHEMA {} RESTRICT").format(sql.Identifier(self.config.schema_name))
                )
            await self._install(connection, sources)
            status = await self._status(connection, sources)
            if status.state != "applied":
                raise MigrationError("migration_verification_failed")
        logger.info(
            "schema_migration_completed", action="reset", schema_name=self.config.schema_name, version=status.version
        )
        return MigrationResult(action="reset", status=status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision the independent ChronaMind research schema")
    parser.add_argument("command", choices=("apply", "status", "reset"))
    parser.add_argument("--schema", default="cme_engine")
    parser.add_argument("--confirm-schema")
    args = parser.parse_args(argv)
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()], logger_factory=structlog.PrintLoggerFactory(file=sys.stderr)
    )
    try:
        runner = MigrationRunner(MigrationConfig.from_environment(schema_name=args.schema))
        if args.command == "reset":
            operation = runner.reset(confirm_schema=args.confirm_schema or "")
        elif args.command == "apply":
            operation = runner.apply()
        else:
            operation = runner.status()
        # psycopg's async transport requires a selector loop on Python 3.12 Windows.
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runtime:
            result = runtime.run(operation)
        Console().print_json(data=result.model_dump(mode="json"))
        return 0
    except ValidationError:
        Console(stderr=True).print_json(data={"error": "invalid_migration_configuration"})
    except MigrationError as error:
        Console(stderr=True).print_json(data={"error": error.code, "sqlstate": error.sqlstate})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
