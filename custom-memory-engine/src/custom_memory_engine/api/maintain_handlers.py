"""The ``purge_working`` handler (E-026; ``API_V2.md`` 4.7, ``V2-D47``, ``V2-D10``).

Same contract as :mod:`api.handlers`: reached only through :meth:`api.entry.Engine.call`, run as the caller under the
restricted role, static errors only. Before this runs the entry point has already established that the caller holds
the admin role within Tier C (a Tier B or non-admin Tier C credential is ``forbidden``) and that ``scope`` passes
``scope_writable`` (it lies in the caller's own ``/u/<caller>/`` namespace).

The handler must not break ``V2-D10``: a purge never removes answerability. What it does to an artifact whose evidence
is a working episode is rule PW-1 of :mod:`engine.purge`, the only place that decides it; this module adds nothing to
the rule. The answer is a :class:`~custom_memory_engine.engine.purge.PurgeReceipt`: counts only, no id, no path, no
content (``API_V2.md`` 1.1). A purge with nothing to purge, including a repeated one, answers zero counts.
"""

from collections.abc import Mapping

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.engine import purge
from custom_memory_engine.scope.paths import ScopeError, parse_scope


class MaintainHandlers:
    """The Maintain handlers landed so far (``purge_working``), bound to one :class:`Runtime`."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def purge_working(self, call: Call) -> purge.PurgeReceipt:
        try:
            scope = parse_scope(call.arguments.get("scope")).text
        except ScopeError:
            raise EntryError("validation_error", "scope_invalid") from None
        user = call.ctx.principal
        async with self.runtime.session(user) as connection:
            return await purge.purge_working(connection, user, scope, schema_name=self.runtime.schema_name)


def register_maintain_handlers(engine: Engine, runtime: Runtime) -> MaintainHandlers:
    """Wire ``purge_working`` onto ``engine``. ``expire`` and ``rebuild_index`` stay with ``A-014``."""
    handlers = MaintainHandlers(runtime)
    table: Mapping[str, Handler] = {"purge_working": handlers.purge_working}
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["MaintainHandlers", "register_maintain_handlers"]
