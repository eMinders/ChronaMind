"""One SQLAlchemy URL for the app and Alembic: psycopg 3, credentials quoted."""

from app.core.config import Settings


def test_the_database_url_uses_psycopg_3_and_quotes_credentials(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("POSTGRES_USER", "app user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:w/rd%")
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_DB", "chronamind")
    url = Settings().sqlalchemy_database_url
    assert url == "postgresql+psycopg://app+user:p%40ss%3Aw%2Frd%25@db.internal:6543/chronamind"
