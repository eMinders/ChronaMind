"""Write the application's OpenAPI document to ``docs/api/openapi.json``.

That file is the single source of the web interface's API types: ``web/`` generates TypeScript from it. Regenerate it
whenever a route or a response model changes, and commit the result; ``tests/test_openapi_export.py`` fails while the
committed file and the application disagree.

    uv run python scripts/export_openapi.py           # write the document
    uv run python scripts/export_openapi.py --check   # exit 1 if the committed document is stale

No database, LLM key or ``.env`` file is needed: the schema is built from the routes, without running the lifespan.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "api" / "openapi.json"

# Settings are read at import time, so the environment is fixed first (the same defaults as tests/conftest.py).
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")
os.environ.setdefault("MEMORY_SLOT_CLASSIFIER", "off")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Fixed so the document does not depend on a developer's `.env` (`PROJECT_NAME`, `VERSION` and `DESCRIPTION` are
#: environment settings, and the file must be byte-identical on every machine and in CI).
INFO = {"title": "ChronaMind application API", "version": "generated"}


def document() -> dict[str, Any]:
    """The application's OpenAPI document with an environment-independent ``info`` block."""
    from app.main import app

    schema = copy.deepcopy(app.openapi())  # a copy: the application caches and serves the original
    schema["info"] = dict(INFO)
    return schema


def render() -> str:
    """The document exactly as committed: sorted keys, two-space indent, LF line endings, trailing newline."""
    return json.dumps(document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    """Write the document, or with ``--check`` report whether the committed one is current."""
    rendered = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != rendered:
            print(f"{TARGET.relative_to(ROOT)} is stale: run `uv run python scripts/export_openapi.py`")
            return 1
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
