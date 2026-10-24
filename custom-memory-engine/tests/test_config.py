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
    ],
)
def test_unsafe_or_invalid_targets_are_rejected_without_secret_leak(url: str) -> None:
    with pytest.raises(ValidationError) as error:
        DatabaseConfig.from_environment({"CME_TEST_DATABASE_URL": url})
    assert "test-only-secret" not in str(error.value)


def test_missing_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DatabaseConfig.from_environment({})
