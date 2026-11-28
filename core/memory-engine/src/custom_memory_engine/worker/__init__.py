"""The slow-lane worker loop, as engine code (``E-047``, ``V2-D103``, ``V2-D106``).

It used to live in ``scripts/run_worker.py``, where nothing could test it: ``tests/`` may not import ``scripts/``
(a boundary rule with a test behind it). So the loop that claimed jobs, called the models and wrote the claims was the
one piece of the slow lane no test reached — and three defects lived there undetected:

* it read ``CME_RUNTIME_ROLE`` and **never used it**, so every slow-lane write ran as the connecting owner and
  row-level security was a promise rather than a boundary;
* it called the embedder and the relation adjudicator **inside** the write transaction, holding the tenant's clock
  for the length of each model call (``semantic.judgements`` explains the fix);
* it recovered expired leases **only at start-up**, so a job leased by a worker that died stayed ``running`` until some
  worker happened to restart.

What remains in ``scripts/run_worker.py`` is the process: signal handlers, a connection, and :func:`serve`.
"""

from custom_memory_engine.worker.loop import (
    KINDS,
    WorkerSettings,
    run_once,
    serve,
    settings_from_environment,
)

__all__ = ["KINDS", "WorkerSettings", "run_once", "serve", "settings_from_environment"]
