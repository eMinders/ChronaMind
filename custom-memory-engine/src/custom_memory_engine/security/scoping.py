"""Service-boundary tenant guard: transaction-local role and tenant context (RL-071, Invariant 7).

``tenant_scope`` is the single entry point a service layer uses to run engine calls for one
authenticated user. It validates the identity before any statement runs, opens the transaction
itself, then sets the restricted role and ``app.user_id`` with ``SET LOCAL`` / ``set_config(..., true)``
so a pooled connection cannot carry a tenant into its next checkout.

It is consistent with, not a competitor to, the per-call checks in ``engine/deletion.py::_verify``
and ``engine/transactions.py``: those still verify that ``app.user_id`` matches the call's user.
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue
from psycopg.pq import TransactionStatus

ROLE_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")
MAX_USER_ID_LENGTH = 256


class TenantScopeError(InvalidParameterValue):
    """Static scoping contract error (SQLSTATE 22023) that never echoes the offending value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_user_id(user_id: object) -> str:
    """Return ``user_id`` unchanged when it is a usable tenant identity, else raise before any I/O."""
    if not isinstance(user_id, str):
        raise TenantScopeError("user_id_must_be_a_string")
    if not user_id or not user_id.strip():
        raise TenantScopeError("nonblank_user_id_required")
    if user_id != user_id.strip():
        raise TenantScopeError("user_id_must_not_have_surrounding_whitespace")
    if len(user_id) > MAX_USER_ID_LENGTH:
        raise TenantScopeError("user_id_too_long")
    if "\x00" in user_id:
        raise TenantScopeError("user_id_must_not_contain_nul")
    return user_id


def _validate_role(role: object) -> str:
    if not isinstance(role, str) or ROLE_PATTERN.fullmatch(role) is None:
        raise TenantScopeError("valid_runtime_role_required")
    return role


@asynccontextmanager
async def tenant_scope(
    connection: AsyncConnection,
    authenticated_user_id: str,
    *,
    role: str,
    requested_user_id: str | None = None,
) -> AsyncIterator[None]:
    """Run the enclosed block as ``role`` with ``app.user_id`` bound to the authenticated user.

    * Missing, blank, over-long or non-string identities raise ``TenantScopeError`` (``22023``).
    * A ``requested_user_id`` (for example one taken from a request payload) that differs from the
      authenticated identity raises ``InsufficientPrivilege`` (``42501``) before any statement runs.
    * The connection must be idle: the context manager owns the transaction so that the transaction-local
      role and tenant cannot outlive the block. A caller already inside a transaction is rejected rather
      than silently widening or narrowing its scope.
    * Exiting the block, normally or by exception, ends the transaction and with it the role and tenant.
    """
    user_id = validate_user_id(authenticated_user_id)
    runtime_role = _validate_role(role)
    if requested_user_id is not None and validate_user_id(requested_user_id) != user_id:
        raise InsufficientPrivilege("tenant_scope_mismatch")
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise TenantScopeError("idle_connection_required")

    async with connection.transaction():
        await connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(runtime_role)))
        await connection.execute("SELECT pg_catalog.set_config('app.user_id', %s, true)", (user_id,))
        yield


__all__ = ["MAX_USER_ID_LENGTH", "TenantScopeError", "tenant_scope", "validate_user_id"]
