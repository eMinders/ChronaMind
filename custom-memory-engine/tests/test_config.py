"""Configuration validation and credential-redaction regressions."""

import pytest
from pydantic import ValidationError

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig

URL = "postgresql://cme_test:test-only-secret@127.0.0.1:5433/cme_compatibility"


def test_explicit_environment_and_redacted_representation() -> None:
    config = DatabaseConfig.from_environment({"CME_TEST_DATABASE_URL": URL})
    assert config.database_url.get_secret_value() == URL
    assert "test-only-secret" not in repr(config)
    assert "test-only-secret" not in config.model_dump_json()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a connection",
        URL.replace("cme_compatibility", "production"),
        URL.replace("127.0.0.1", "db.example.com"),
        URL.replace("cme_test", "postgres"),
        "postgresql://cme_test@localhost/cme_compatibility",
        URL + "?hostaddr=203.0.113.1",
        URL + "?service=production",
        # the xdist worker form (#167) is exact: nothing that merely resembles it
        URL.replace("cme_compatibility", "cme_compatibility_production"),
        URL.replace("cme_compatibility", "cme_compatibility_gw"),
        URL.replace("cme_compatibility", "cme_compatibility_gw1234"),
        URL.replace("cme_compatibility", "cme_compatibility_gw0x"),
        URL.replace("cme_compatibility", "xcme_compatibility_gw0"),
    ],
)
def test_unsafe_or_invalid_targets_are_rejected_without_secret_leak(url: str) -> None:
    with pytest.raises(ValidationError) as error:
        DatabaseConfig.from_environment({"CME_TEST_DATABASE_URL": url})
    assert "test-only-secret" not in str(error.value)


def test_missing_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DatabaseConfig.from_environment({})


@pytest.mark.parametrize("name", ["cme_compatibility", "cme_compatibility_gw0", "cme_compatibility_gw15"])
def test_the_disposable_database_and_its_xdist_worker_copies_are_accepted(name: str) -> None:
    """#167: each pytest-xdist worker uses its own copy of the disposable database (``conftest.py``)."""
    config = DatabaseConfig.from_environment({"CME_TEST_DATABASE_URL": URL.replace("cme_compatibility", name)})
    assert config.database_url.get_secret_value().endswith("/" + name)
