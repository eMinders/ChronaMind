"""Serialisation keys and the global lock order for cross-scope work (V2-D51; DELETION_ORDER.md §6.1).

A deletion (or any write) whose provenance closure touches several scopes must hold the
serialisation lock of each before its first write. Taking them in *discovery* order deadlocks
when two transactions reach each other's scopes from opposite ends; taking them in **one global
order** cannot, because no cycle of waits can form over a total order.

* **Key (`I9`, W1).** The serialisation key of a scope is its owner namespace ``/u/<user>/``.
  Every canonical scope lies inside exactly one namespace (``scope.paths`` grammar), so this is
  total, and for the conversational profile it is exactly v1's per-user advisory lock. A finer
  per-scope key is a later decision; the order rule does not change with it.
* **Order.** Keys are deduplicated and sorted by their UTF-8 bytes (equivalently, by code point),
  never by a database collation, so every process agrees on the order.
* **Closure growth.** :func:`must_retry` is the step-3 check: if the closure recomputed under the
  locks needs a key not held, the transaction rolls back and retries with the union (advisory
  transaction locks cannot be released early). Retries are bounded by the caller (tenacity).

Pure: no I/O. ``E-011``/``E-037`` turn each key into ``pg_advisory_xact_lock(hashtext(key))``.
"""

from __future__ import annotations

from collections.abc import Iterable

from custom_memory_engine.scope.paths import ScopeError, parse_scope, user_scope


def serialisation_key(scope: object) -> str:
    """The `I9` key of one canonical scope: its owner namespace ``/u/<user>/``."""
    path = parse_scope(scope)
    if len(path.segments) < 2 or path.segments[0] != "u":
        raise ScopeError("scope_outside_user_namespace")
    return user_scope(path.segments[1]).text


def lock_order(scopes: Iterable[object]) -> tuple[str, ...]:
    """Deduplicated serialisation keys of ``scopes`` in the one global acquisition order."""
    keys = {serialisation_key(scope) for scope in scopes}
    if not keys:
        raise ScopeError("lock_set_required")
    return tuple(sorted(keys, key=lambda key: key.encode("utf-8")))


def must_retry(held: Iterable[str], recomputed_scopes: Iterable[object]) -> bool:
    """True if the closure recomputed under the locks needs a key the transaction does not hold."""
    return not {serialisation_key(scope) for scope in recomputed_scopes} <= set(held)


__all__ = ["lock_order", "must_retry", "serialisation_key"]
