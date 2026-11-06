@AGENTS.md

# First-Time Setup & Installation

To bootstrap the repository for the first time (installs `uv` if needed, creates `.env.development` with secure keys, starts PostgreSQL + pgvector via Docker, syncs dependencies, installs pre-commit hooks, applies migrations, and launches the app):

- **Windows (PowerShell)**: `.\setup.ps1`
- **macOS / Linux**: `chmod +x setup.sh && ./setup.sh`
- **Cross-Platform / CLI**: `uv run python scripts/setup.py` (or `make setup`)

## Common Development Commands

- **Run dev server**: `uv run python run.py` (or `make dev`)
- **Run database migrations**: `uv run alembic upgrade head` (or `make migrate`)
- **Check code quality**: `make check` (ruff, format check, pyright, and the root tests; the same gates as CI `checks`)
