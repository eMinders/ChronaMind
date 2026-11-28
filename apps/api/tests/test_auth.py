"""Tokens (PyJWT) and password hashes (bcrypt): the security primitives the auth routes rest on."""

from datetime import timedelta

import jwt

from app.core.config import settings
from app.models.user import User
from app.utils.auth import create_access_token, verify_token


def test_a_token_round_trips_to_its_thread_id() -> None:
    token = create_access_token("thread-123")
    assert verify_token(token.access_token) == "thread-123"


def test_a_tampered_token_is_refused() -> None:
    token = create_access_token("thread-123").access_token
    header, payload, signature = token.split(".")
    forged = jwt.encode({"sub": "thread-999"}, "not-the-secret", algorithm=settings.JWT_ALGORITHM).split(".")[1]
    assert verify_token(f"{header}.{forged}.{signature}") is None
    assert verify_token(f"{header}.{payload}.{signature[:-2]}AA") is None


def test_an_expired_token_is_refused() -> None:
    token = create_access_token("thread-123", expires_delta=timedelta(seconds=-1))
    assert verify_token(token.access_token) is None


def test_a_token_signed_with_another_algorithm_is_refused() -> None:
    unsigned = jwt.encode({"sub": "thread-123"}, key=None, algorithm="none")  # pyright: ignore[reportArgumentType]
    assert verify_token(unsigned + "x") is None


def test_passwords_are_salted_hashes_that_verify_only_the_original() -> None:
    first, second = User.hash_password("correct horse"), User.hash_password("correct horse")
    assert first != second and first.startswith("$2")
    user = User(email="a@example.com", hashed_password=first)
    assert user.verify_password("correct horse")
    assert not user.verify_password("Correct horse")
