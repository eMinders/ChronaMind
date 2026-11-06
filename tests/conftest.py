"""Settings are read at import time, so the test environment is fixed before any ``app`` module is imported.

These tests need no services: no database, no LLM provider, no Langfuse. ``setdefault`` keeps an explicitly configured
environment (for example a developer's own ``APP_ENV``) out of reach only where it is unset.
"""

import os
import secrets

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")
