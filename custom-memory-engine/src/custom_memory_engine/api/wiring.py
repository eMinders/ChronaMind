"""Wire one :class:`~custom_memory_engine.api.entry.Engine` the way a deployment wires it (``E-039``, ``E-011``).

The entry point takes injected collaborators so tests can drive every branch. That also means a deployment could wire
it *incompletely* - most importantly without a ``ScopeReadRecorder``, which would silently disable the cross-scope
audit of ``ARCHITECTURE_V2.md`` section 5 rule 6. This function is the one place that builds a complete engine, and
:func:`tests/test_api_wiring.py` asserts it wires every collaborator a read path needs.

Handlers are registered by their owning tasks (``api.handlers``, ``api.ingest_handlers``); an operation whose task has
not landed stays unwired and answers ``forbidden``, which is the fail-closed behaviour ``E-039`` specifies.
"""

from custom_memory_engine.api.entry import Engine, Handler
from custom_memory_engine.api.runtime import DbMethods, DbTargets, ForeignKeyPrincipals, Runtime
from custom_memory_engine.scope.audit import ConnectionScopeReads


def build_engine(runtime: Runtime, *, handlers: dict[str, Handler] | None = None) -> Engine:
    """A fully wired engine: caller-scoped resolver, method registry, principal check and the scope read recorder."""
    return Engine(
        resolver=DbTargets(runtime),
        methods=DbMethods(runtime),
        principals=ForeignKeyPrincipals(),
        handlers=handlers,
        scope_reads=ConnectionScopeReads(runtime.session, schema_name=runtime.schema_name),
    )


__all__ = ["build_engine"]
