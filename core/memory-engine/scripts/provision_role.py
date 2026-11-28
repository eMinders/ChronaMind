"""Create (or re-grant) the restricted runtime role for a migrated schema, and verify it.

The operator-side step between ``python -m custom_memory_engine.schema.migrate apply`` and starting the host: the host
connects with an owner-level DSN and ``SET LOCAL ROLE``s to this role on every call, so the role must exist, be
``NOSUPERUSER NOBYPASSRLS NOLOGIN``, hold least privilege, and see forced RLS on every table.
:func:`~custom_memory_engine.security.provisioning.provision_runtime_role` does all of that and verifies it; this
script is that function with a command line, so a deployment (``deploy/README.md`` step 4) and the app-to-engine
boundary test run the same code instead of an inline snippet.

``RL_DATABASE_URL`` names the owner connection, exactly as the migration runner reads it; nothing reads a dotenv file.

Usage::

    RL_DATABASE_URL=postgresql://owner@host/db python scripts/provision_role.py --schema cme_engine \
        --role cme_runtime [--login-role cme_host]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from psycopg import AsyncConnection

from custom_memory_engine.security.provisioning import RlsProvisioningError, provision_runtime_role


async def provision(dsn: str, schema: str, role: str, login_role: str | None) -> None:
    """Provision ``role`` on ``schema`` in one transaction; a failed verification rolls the grants back."""
    async with await AsyncConnection.connect(dsn, connect_timeout=5) as connection:
        await provision_runtime_role(connection, schema, role, login_role=login_role)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision the restricted runtime role for a migrated schema")
    parser.add_argument("--schema", default="cme_engine")
    parser.add_argument("--role", required=True)
    parser.add_argument("--login-role")
    args = parser.parse_args(argv)
    dsn = os.environ.get("RL_DATABASE_URL", "")
    if not dsn:
        print('{"error": "rl_database_url_required"}', file=sys.stderr)
        return 1
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            runner.run(provision(dsn, args.schema, args.role, args.login_role))
    except RlsProvisioningError as error:
        # a static code; never the DSN, which carries a password
        print(f'{{"error": "{error.code}"}}', file=sys.stderr)
        return 1
    print('{"status": "provisioned"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
