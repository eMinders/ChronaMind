"""Tenant isolation at the service boundary: restricted role provisioning and scoping guard (RL-071)."""

from custom_memory_engine.security.provisioning import (
    USER_TABLES,
    RlsProvisioningError,
    provision_runtime_role,
    verify_forced_rls,
    verify_runtime_role,
)
from custom_memory_engine.security.scoping import TenantScopeError, tenant_scope, validate_user_id

__all__ = [
    "USER_TABLES",
    "RlsProvisioningError",
    "TenantScopeError",
    "provision_runtime_role",
    "tenant_scope",
    "validate_user_id",
    "verify_forced_rls",
    "verify_runtime_role",
]
