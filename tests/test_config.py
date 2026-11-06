"""One SQLAlchemy URL for the app and Alembic: psycopg 3, credentials quoted."""

import logging

from app.core import logging as app_logging
from app.core.config import Settings


def test_the_database_url_uses_psycopg_3_and_quotes_credentials(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("POSTGRES_USER", "app user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:w/rd%")
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_DB", "chronamind")
    url = Settings().sqlalchemy_database_url
    assert url == "postgresql+psycopg://app+user:p%40ss%3Aw%2Frd%25@db.internal:6543/chronamind"


def test_the_configured_log_level_is_the_one_applied(monkeypatch) -> None:  # noqa: ANN001
    """Production's WARNING must reach the handlers; it used to be replaced by INFO (derived from DEBUG alone)."""
    monkeypatch.setattr(app_logging.settings, "LOG_LEVEL", "WARNING")
    monkeypatch.setattr(app_logging.settings, "DEBUG", False)
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        root.handlers.clear()
        app_logging.setup_logging()
        assert root.level == logging.WARNING
        assert all(handler.level == logging.WARNING for handler in root.handlers)
    finally:
        root.handlers[:], root.level = saved[0], saved[1]
