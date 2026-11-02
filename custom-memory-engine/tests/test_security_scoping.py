"""Unit tests for the tenant-scoping guard and provisioning inputs (RL-071); no database needed."""

import re

import pytest
from psycopg.errors import InsufficientPrivilege

from custom_memory_engine.schema import migrate
from custom_memory_engine.security import (
    USER_TABLES,
    RlsProvisioningError,
    TenantScopeError,
    provision_runtime_role,
    tenant_scope,
    validate_user_id,
    verify_forced_rls,
)
from custom_memory_engine.security.provisioning import RLS_SQL, TABLE_GRANTS, _sections

SCHEMA_SQL_TABLES: tuple[str, ...] = migrate.TABLES


class _UntouchedConnection:
    """Any attribute access fails: proves a rejection happened before any I/O."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"connection_touched:{name}")


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (None, "user_id_must_be_a_string"),
        (7, "user_id_must_be_a_string"),
        ("", "nonblank_user_id_required"),
        ("   ", "nonblank_user_id_required"),
        (" u1", "user_id_must_not_have_surrounding_whitespace"),
        ("u1\n", "user_id_must_not_have_surrounding_whitespace"),
        ("u" * 257, "user_id_too_long"),
        ("u1\x00", "user_id_must_not_contain_nul"),
    ],
)
def test_invalid_user_identity_is_rejected(value: object, code: str) -> None:
    with pytest.raises(TenantScopeError) as raised:
        validate_user_id(value)
    assert raised.value.code == code


def test_valid_user_identity_is_returned_unchanged() -> None:
    assert validate_user_id("user-42") == "user-42"


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [None, "", "  ", 5])
async def test_scope_rejects_bad_identity_before_touching_the_connection(identity: object) -> None:
    with pytest.raises(TenantScopeError):
        async with tenant_scope(_UntouchedConnection(), identity, role="cme_runtime"):  # type: ignore[arg-type]
            pytest.fail("block_must_not_run")


@pytest.mark.asyncio
async def test_scope_rejects_a_mismatched_requested_tenant_before_touching_the_connection() -> None:
    with pytest.raises(InsufficientPrivilege, match="tenant_scope_mismatch"):
        async with tenant_scope(_UntouchedConnection(), "u1", role="cme_runtime", requested_user_id="u2"):  # type: ignore[arg-type]
            pytest.fail("block_must_not_run")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["", "Bad Role", "role;drop", "1role", "r" * 64, "ROLE"])
async def test_scope_rejects_an_unsafe_role_name_before_touching_the_connection(role: str) -> None:
    with pytest.raises(TenantScopeError, match="valid_runtime_role_required"):
        async with tenant_scope(_UntouchedConnection(), "u1", role=role):  # type: ignore[arg-type]
            pytest.fail("block_must_not_run")


@pytest.mark.asyncio
async def test_provisioning_rejects_unsafe_identifiers_before_touching_the_connection() -> None:
    for schema, role in (("public", "cme_runtime"), ("cme_x; DROP", "cme_runtime"), ("cme_x", "Bad;Role")):
        with pytest.raises(RlsProvisioningError):
            await provision_runtime_role(_UntouchedConnection(), schema, role)  # type: ignore[arg-type]
    with pytest.raises(RlsProvisioningError):
        await verify_forced_rls(_UntouchedConnection(), "public")  # type: ignore[arg-type]


def test_user_tables_are_every_table_except_the_shared_catalog() -> None:
    # `clock_watermark` (migration 9) is engine-internal: the runtime role has no privilege on it and only a definer
    # trigger writes it, so it is neither a tenant table nor the shared catalog.
    assert set(USER_TABLES) == set(SCHEMA_SQL_TABLES) - {"predicate_schema", "clock_watermark", "method"}
    assert len(USER_TABLES) == 18  # 11 v1 tables + scope_grant + 5 structural tables (10) + api_idempotency (12)


def test_rls_sql_grants_cover_every_user_table_and_only_reads_the_catalog() -> None:
    grants = _sections()["grants"]
    for table in USER_TABLES:
        assert f"{{{{schema}}}}.{table}" in grants, table
    assert "GRANT SELECT ON {{schema}}.predicate_schema" in grants
    assert "BYPASSRLS" not in RLS_SQL.read_text(encoding="utf-8").replace("NOBYPASSRLS", "")
    assert "NOBYPASSRLS" in _sections()["create-role"]
    assert "NOSUPERUSER" in _sections()["create-role"]
    assert "NOLOGIN" in _sections()["create-role"]


def test_rls_sql_grants_equal_the_least_privilege_matrix_exactly() -> None:
    """``rls.sql`` and ``TABLE_GRANTS`` are two statements of one contract (V2-D54): they must agree."""
    parsed: dict[str, set[str]] = {}
    for statement in _sections()["grants"].split(";"):
        body = " ".join(line for line in statement.splitlines() if not line.lstrip().startswith("--")).strip()
        match = re.fullmatch(r"GRANT (.+?) ON (?!SCHEMA)(.+?) TO \{\{role\}\}", body)
        if match is None:
            continue
        privileges = {p.strip() for p in match.group(1).split(",")}
        for table in match.group(2).split(","):
            parsed.setdefault(table.strip().removeprefix("{{schema}}."), set()).update(privileges)
    assert parsed == {table: set(grants) for table, grants in TABLE_GRANTS.items()}
    # the shared catalogs: predicate_schema (read-only) and the append-only method registry (E-014)
    assert set(TABLE_GRANTS) == set(USER_TABLES) | {"predicate_schema", "method"}
    for append_only in ("audit_event", "deletion_request", "turn", "session", "app_user", "memory_evidence"):
        assert not {"UPDATE", "DELETE"} & TABLE_GRANTS[append_only], append_only
