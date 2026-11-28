"""Observability module for the application."""

import os
from typing import Optional

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from app.core.config import settings
from app.core.logging import logger

_langfuse_client: Optional[Langfuse] = None


def langfuse_init() -> Optional[Langfuse]:
    """Initialize Langfuse and set process-level environment variables."""
    global _langfuse_client
    if not settings.LANGFUSE_TRACING_ENABLED:
        logger.debug("langfuse_tracing_disabled")
        return None

    # Ensure required Langfuse environment variables are set for integrations
    os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = settings.ENVIRONMENT.value
    os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST
    os.environ["LANGFUSE_BASE_URL"] = settings.LANGFUSE_HOST
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
    os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY

    langfuse = Langfuse(
        tracing_enabled=settings.LANGFUSE_TRACING_ENABLED,
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_HOST,
        environment=settings.ENVIRONMENT.value,
        debug=settings.DEBUG,
    )

    try:
        if langfuse.auth_check():
            logger.debug("langfuse_auth_success")
            _langfuse_client = langfuse
            return langfuse
        else:
            logger.warning("langfuse_auth_failure")
    except Exception:
        logger.exception("langfuse_auth_check_failed")
    return None


def langfuse_flush() -> None:
    """Flush pending Langfuse events synchronously on shutdown or script exit."""
    global _langfuse_client
    if _langfuse_client:
        try:
            _langfuse_client.flush()
            logger.debug("langfuse_flushed")
        except Exception:
            logger.exception("langfuse_flush_failed")


def get_langfuse_callback_handler() -> CallbackHandler:
    """Create a Langfuse CallbackHandler for tracking LLM interactions.

    Returns:
        CallbackHandler: Configured Langfuse callback handler.
    """
    return CallbackHandler()


langfuse_callback_handler = get_langfuse_callback_handler()
