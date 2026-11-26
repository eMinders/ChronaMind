"""The engine's HTTP host (``E-046``, ``V2-D93``).

Importing this package does **not** import Starlette: the engine's core must stay installable without the ``host``
extra, so the ASGI application is reached through :func:`create_app`, resolved on first access. Everything here is
lazy for that one reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_memory_engine.host.credentials import CredentialsError, HostCredentials
from custom_memory_engine.host.limits import ConcurrencyLimiter, RateLimiter, ResourceLimitError
from custom_memory_engine.host.metrics import HostMetrics
from custom_memory_engine.host.service import HostService, HostSettings, settings_from_environment

if TYPE_CHECKING:  # pragma: no cover - typing only
    from starlette.applications import Starlette

__all__ = [
    "ConcurrencyLimiter",
    "CredentialsError",
    "HostCredentials",
    "HostMetrics",
    "HostService",
    "HostSettings",
    "RateLimiter",
    "ResourceLimitError",
    "create_app",
    "settings_from_environment",
]


def create_app(service: HostService) -> Starlette:
    """The ASGI application. Imported lazily so ``host.service`` works without the ``host`` extra installed."""
    from custom_memory_engine.host.app import create_app as build

    return build(service)


def __getattr__(name: str) -> Any:  # pragma: no cover - attribute plumbing
    if name == "STATUS_BY_CODE":
        from custom_memory_engine.host.app import STATUS_BY_CODE

        return STATUS_BY_CODE
    raise AttributeError(name)
