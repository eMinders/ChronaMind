"""Explicit environment configuration for disposable compatibility tests, not deployment."""

import os
import re
from collections.abc import Mapping

from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

#: The disposable test database, one pytest-xdist worker's copy of it (``conftest.py``, issue #167), and that
#: database's migrated template (``_tpl``) and per-test clones (``_c<n>``, ``tests/integration/schema.py``). Nothing
#: else: the guard exists so a misconfigured URL can never point destructive test code at a product database.
TEST_DATABASE_NAME = re.compile(r"cme_compatibility(?:_gw[0-9]{1,3})?(?:_tpl|_c[0-9]{1,7})?")


class TestDatabaseConfig(BaseModel):
    """Redact connection credentials and refuse accidental use of product databases."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def validate_disposable_target(cls, value: SecretStr) -> SecretStr:
        """Limit destructive probe access to the explicitly named local test database."""
        try:
            params = conninfo_to_dict(value.get_secret_value())
        except ProgrammingError:
            raise ValueError("invalid_test_database_connection") from None
        if (
            TEST_DATABASE_NAME.fullmatch(str(params.get("dbname", ""))) is None
            or params.get("user") != "cme_test"
            or params.get("host") not in {"127.0.0.1", "localhost", "::1"}
            or not params.get("password")
            or "hostaddr" in params
            or "service" in params
        ):
            raise ValueError("local_disposable_test_database_required")
        return value

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "TestDatabaseConfig":
        """Read exported environment only; never implicitly load product .env files."""
        values = os.environ if environ is None else environ
        return cls(database_url=SecretStr(values.get("CME_TEST_DATABASE_URL", "")))
