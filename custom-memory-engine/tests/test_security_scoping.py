"""Unit tests for the tenant-scoping guard and provisioning inputs (RL-071); no database needed."""

import pytest
from psycopg.errors import InsufficientPrivilege

from custom_memory_engine.security import (
    USER_TABLES,
    RlsProvisioningError,
    TenantScopeError,
    provision_runtime_role,
    tenant_scope,
    validate_user_id,
    verify_forced_rls,
)
from custom_memory_engine.security.provisioning import RLS_SQL, _sections

SCHEMA_SQL_TABLES: tuple[str, ...] = (
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
)


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
    assert set(USER_TABLES) == set(SCHEMA_SQL_TABLES) - {"predicate_schema"}
    assert len(USER_TABLES) == 11


def test_rls_sql_grants_cover_every_user_table_and_only_reads_the_catalog() -> None:
    grants = _sections()["grants"]
    for table in USER_TABLES:
        assert f"{{{{schema}}}}.{table}" in grants, table
    assert "GRANT SELECT ON {{schema}}.predicate_schema" in grants
    assert "BYPASSRLS" not in RLS_SQL.read_text(encoding="utf-8").replace("NOBYPASSRLS", "")
    assert "NOBYPASSRLS" in _sections()["create-role"]
    assert "NOSUPERUSER" in _sections()["create-role"]
    assert "NOLOGIN" in _sections()["create-role"]
