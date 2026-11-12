"""The ``record_outcome`` handler (E-027; ``API_V2.md`` 4.5, ``V2-D75``).

Same contract as :mod:`api.handlers`: reached only through :meth:`api.entry.Engine.call`, run as the caller under the
restricted role, static errors only. ``targets_readable`` has already required the procedure to be in the caller's read
set, and answered an unreadable procedure exactly as an absent one (``T-09``, ``T-11``).

The three error cases of ``API_V2.md`` 4.5, and where each is enforced:

* **not a procedure** -> ``precondition_failed``. Established here by reading the target's ``kind`` under the caller's
  RLS; the ``outcome_stats`` foreign key and the ``guard_outcome_stats`` trigger would also refuse it, so the rule does
  not depend on this read alone.
* **unreadable target** -> ``forbidden``, indistinguishable from absent. Established by ``targets_readable`` before
  this handler runs, and re-checked here by the read itself: a row the caller may not see simply is not returned.
* **tier / read-set** -> ``forbidden``. ``authorize`` refuses a tier without the operation; ``targets_readable``
  refuses a target outside the read set.

Retrospective only: nothing is executed, and no episode store is touched beyond the optional ``episode_ref`` recorded
in the content-free audit row (``E-030`` owns linking an outcome to the episode that demonstrated it).
"""

from collections.abc import Mapping

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Runtime, new_id
from custom_memory_engine.engine import outcomes
from custom_memory_engine.engine.procedure_ranking import OutcomeStats
from custom_memory_engine.security.audit_guard import content_free_details


class OutcomeHandlers:
    """The ``record_outcome`` handler, bound to one :class:`Runtime`."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def _kind_of(self, connection: AsyncConnection, user: str, procedure_id: str) -> str | None:
        """The target's ``kind``, or ``None`` when no row is visible to this caller.

        A row that exists but has a NULL ``kind`` (a v1 row before migration 5 set one) is reported as the empty
        string, not as ``None``: it is present and readable, so the right answer is "not a procedure" rather than the
        ``forbidden`` that means "absent or unreadable" (``T-11``).
        """
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL("SELECT kind FROM {t} WHERE id = %s AND user_id = %s").format(
                    t=self.runtime.table("memory_version")
                ),
                (procedure_id, user),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return "" if row[0] is None else str(row[0])

    async def record_outcome(self, call: Call) -> OutcomeStats:
        procedure_id = call.arguments.get("procedure_id")
        if not isinstance(procedure_id, str) or not procedure_id:
            raise EntryError("validation_error", "target_required")
        outcome = call.arguments.get("outcome")
        if outcome not in outcomes.OUTCOMES:
            raise EntryError("validation_error", "outcome_invalid")
        cost = call.arguments.get("cost")
        if cost is not None and (isinstance(cost, bool) or not isinstance(cost, int | float) or cost < 0):
            raise EntryError("validation_error", "cost_invalid")
        episode_ref = call.arguments.get("episode_ref")
        if episode_ref is not None and (not isinstance(episode_ref, str) or not episode_ref):
            raise EntryError("validation_error", "episode_ref_invalid")
        user = call.ctx.principal
        async with self.runtime.session(user) as connection:
            kind = await self._kind_of(connection, user, procedure_id)
            if kind is None:
                raise EntryError("forbidden")  # absent or another principal's: the same answer (T-11)
            if kind != "procedure":
                raise EntryError("precondition_failed", "target_is_not_a_procedure")
            stats = await outcomes.record_outcome(
                connection,
                user_id=user,
                procedure_id=procedure_id,
                outcome=str(outcome),
                cost=None if cost is None else float(cost),
                schema_name=self.runtime.schema_name,
            )
            await self._audit(connection, user, procedure_id, str(outcome), episode_ref)
            return stats

    async def _audit(
        self, connection: AsyncConnection, user: str, procedure_id: str, outcome: str, episode_ref: object
    ) -> None:
        """A content-free outcome event. The counters live in the table; the event records that feedback happened.

        ``episode_ref`` becomes a *boolean flag*, never the ref itself: the audit guard would admit an
        identifier-shaped string, but an episode id is a locator and ``RL-072`` keeps locators out of audit details.
        """
        details: dict[str, object] = {"reason_code": "outcome_recorded", "kind": outcome}
        if episode_ref is not None:
            details["episode_referenced"] = True
        async with connection.cursor() as cursor:
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                    "VALUES (%s, %s, %s, %s, pg_catalog.transaction_timestamp(), %s)"
                ).format(a=self.runtime.table("audit_event")),
                (
                    # a random id: deriving it from the target would correlate two audit rows and leak the id's shape
                    new_id("audit"),
                    user,
                    procedure_id,
                    "outcome_recorded",
                    Jsonb(content_free_details(details)),
                ),
            )


def register_outcome_handlers(engine: Engine, runtime: Runtime) -> OutcomeHandlers:
    """Wire ``record_outcome`` onto ``engine``."""
    handlers = OutcomeHandlers(runtime)
    table: Mapping[str, Handler] = {"record_outcome": handlers.record_outcome}
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["OutcomeHandlers", "register_outcome_handlers"]
