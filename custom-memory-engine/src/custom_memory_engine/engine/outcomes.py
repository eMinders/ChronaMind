"""Procedure outcome persistence: the counters ``record_outcome`` maintains (E-027, ``V2-D75``).

``record_outcome`` is **retrospective**: the agent executed the procedure outside the engine, and reports what
happened (``API_V2.md`` 4.5, ``ARCHITECTURE_V2.md`` section 6). There is no execution, no planning and no precondition
matching here -- those are ``E-030``.

The storage this module writes is migration 20's ``outcome_stats`` table: a side table keyed to the procedure's
``memory_version`` row, in the shape ``abstraction_stats`` already established. It is the **minimal substrate E-030
owns**; see the migration header for why it is a side table and what ``E-030`` will add to it.

**The counters are monotone by construction.** An outcome is a recorded event, so :func:`record_outcome` only ever adds
to a counter: the database's ``guard_outcome_stats`` trigger refuses a decrement, which makes that a property of the
schema rather than of this module's care. Replaying the same call therefore records a second outcome, which is correct
and is what ``API_V2.md`` 4.5 specifies -- the operation takes no ``idempotency_key`` because an increment has no
identity to deduplicate on (``E-027`` section 5 Q3).

**Cost is stored as sum and count.** Storing a running mean would make the persisted value depend on the order the
outcomes arrived in, so two identical histories could disagree; ``mean_cost`` is a generated column computed from
``cost_sum / cost_count`` instead.
"""

from collections.abc import Sequence
from datetime import datetime

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.engine.procedure_ranking import OutcomeStats

#: The two recorded outcomes (``API_V2.md`` 4.5). A third would be a schema decision, not a validation tweak.
OUTCOMES: tuple[str, str] = ("success", "failure")


class OutcomeError(ValueError):
    """A static code; never echoes content, an identifier or a scope."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _statement(schema_name: str) -> sql.Composed:
    """The upsert of one outcome. ``+1`` on the matching counter, and the cost folded into sum and count."""
    return sql.SQL(
        "INSERT INTO {t} AS s (version_id, user_id, successes, failures, last_used_at, cost_count, cost_sum) "
        "VALUES (%(id)s, %(user)s, %(success)s, %(failure)s, clock_now(), %(cost_count)s, %(cost_sum)s) "
        "ON CONFLICT (version_id) DO UPDATE SET "
        "  successes = s.successes + EXCLUDED.successes, "
        "  failures = s.failures + EXCLUDED.failures, "
        "  cost_count = s.cost_count + EXCLUDED.cost_count, "
        "  cost_sum = s.cost_sum + EXCLUDED.cost_sum, "
        "  last_used_at = EXCLUDED.last_used_at, "
        "  updated_at = clock_now() "
        "RETURNING s.successes, s.failures, s.last_used_at, s.mean_cost"
    ).format(t=sql.Identifier(schema_name, "outcome_stats"))


async def record_outcome(
    connection: AsyncConnection,
    *,
    user_id: str,
    procedure_id: str,
    outcome: str,
    cost: float | None = None,
    schema_name: str = "cme_engine",
) -> OutcomeStats:
    """Record one retroactive outcome for ``procedure_id`` and return the counters after it.

    The caller (``api.outcome_handlers``) has already established that the target is readable -- so an absent
    procedure and another principal's procedure are both ``forbidden`` before this runs -- and that it is a procedure.
    This function still lets the schema enforce the second: the ``guard_outcome_stats`` trigger raises
    ``P0001`` for a non-procedure, which the handler maps to ``precondition_failed``.

    ``cost`` is an opaque non-negative real (``API_V2.md`` 4.5 declares no unit): ``None`` records an outcome with no
    cost, which is not the same as a cost of ``0.0`` and must not move ``mean_cost``.
    """
    if outcome not in OUTCOMES:
        raise OutcomeError("outcome_invalid")  # static code; the value itself is never echoed
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, int | float) or cost < 0):
        raise OutcomeError("cost_invalid")
    parameters = {
        "id": procedure_id,
        "user": user_id,
        "success": 1 if outcome == "success" else 0,
        "failure": 1 if outcome == "failure" else 0,
        # a cost of 0 is a recorded cost (count 1, sum 0); an absent cost records neither
        "cost_count": 0 if cost is None else 1,
        "cost_sum": 0.0 if cost is None else float(cost),
    }
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(_statement(schema_name), parameters)
        row = await cursor.fetchone()
    if row is None:  # the upsert always returns the stored row; a miss would be a schema fault
        raise OutcomeError("outcome_not_recorded")
    return OutcomeStats(
        procedure_id=procedure_id,
        successes=int(row[0]),
        failures=int(row[1]),
        last_used_at=row[2] if isinstance(row[2], datetime) else None,
        mean_cost=None if row[3] is None else float(row[3]),
    )


async def stats_for(
    connection: AsyncConnection, *, user_id: str, procedure_ids: Sequence[str], schema_name: str = "cme_engine"
) -> dict[str, OutcomeStats]:
    """The recorded counters of ``procedure_ids``, for the ranking term. Missing ids simply have no entry.

    One statement for the whole batch, so ordering a page of procedures costs one round trip rather than one per row.
    RLS restricts the read to the caller's own rows, and the ``user_id`` predicate is belt-and-braces on top of it.
    """
    if not procedure_ids:
        return {}
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT version_id, successes, failures, last_used_at, mean_cost FROM {t} "
                "WHERE user_id = %s AND version_id = ANY(%s)"
            ).format(t=sql.Identifier(schema_name, "outcome_stats")),
            (user_id, list(procedure_ids)),
        )
        return {
            str(row[0]): OutcomeStats(
                procedure_id=str(row[0]),
                successes=int(row[1]),
                failures=int(row[2]),
                last_used_at=row[3] if isinstance(row[3], datetime) else None,
                mean_cost=None if row[4] is None else float(row[4]),
            )
            for row in await cursor.fetchall()
        }


__all__ = ["OUTCOMES", "OutcomeError", "record_outcome", "stats_for"]
