"""Start the engine's HTTP host (``E-046``).

Configuration is environment-only, and every variable names a capability rather than a provider:

``CME_DATABASE_URL``        owner-level DSN the pool connects with; each call ``SET LOCAL ROLE``s to
                            ``CME_RUNTIME_ROLE``, so the login role needs ``SET ROLE`` and nothing else
``CME_RUNTIME_ROLE``        the restricted, ``NOSUPERUSER NOBYPASSRLS`` role provisioned by ``security.provisioning``
``CME_SCHEMA``              the managed schema (default ``cme_engine``)
``CME_HOST_TOKENS``         JSON: ``{"<token>": {"principal": "...", "tier": "B"|"C", "admin": false,
                            "delegate": false}}``. ``delegate: true`` is a service token that names the end user per
                            request in the envelope's ``principal`` (``V2-D120``); it cannot also be ``admin``
``CME_PROVENANCE_KEY``      >=32 chars; signs evidence. Must be the same value across restarts
``CME_DELETION_RECEIPT_KEY``>=32 chars; keys the deletion receipt digests. Same rule
``CME_HOST_HOST``/``PORT``  bind address (default ``127.0.0.1:8100``)

Nothing here reads a provider SDK, a model name or a vendor: the host is the engine's own contract over HTTP.
"""

from __future__ import annotations

import os
import sys

import structlog
import uvicorn

from custom_memory_engine.host import HostCredentials, HostService, create_app, settings_from_environment


def main() -> int:
    structlog.configure(processors=[structlog.processors.add_log_level, structlog.processors.JSONRenderer()])
    settings = settings_from_environment()
    credentials = HostCredentials.from_environment(os.environ.get("CME_HOST_TOKENS"))
    # The application owns the pool lifecycle (its lifespan opens and closes it), so nothing here is stateful.
    app = create_app(HostService(settings, credentials))
    uvicorn.run(
        app,
        host=os.environ.get("CME_HOST_HOST", "127.0.0.1"),
        port=int(os.environ.get("CME_HOST_PORT", "8100")),
        log_config=None,  # structlog already owns the output
        access_log=False,  # uvicorn's access log writes paths and clients; the host's own events are content-free
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
