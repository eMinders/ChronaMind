#!/usr/bin/env python3
"""Project onboarding and runner script for ChronaMind / FastAPI LangGraph template.

Automates setup for new and existing contributors:
- Detects / installs Astral `uv` package manager
- Creates and configures `.env.development` with secure keys and ports
- Ensures PostgreSQL + pgvector container is running
- Synchronizes the Python 3.14 virtual environment dependencies
- Installs git pre-commit hooks
- Applies Alembic database migrations
- Launches the application server with hot-reload
"""

import argparse
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# Paths
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT_DIR / ".env.example"
ENV_DEV = ROOT_DIR / ".env.development"


def print_step(title: str) -> None:
    """Print a formatted step banner."""
    print(f"\n\033[1;36m===> {title}\033[0m")


def print_success(message: str) -> None:
    """Print a success message."""
    print(f"\033[1;32m[+] {message}\033[0m")


def print_warning(message: str) -> None:
    """Print a warning message."""
    print(f"\033[1;33m[!] {message}\033[0m")


def print_error(message: str) -> None:
    """Print an error message."""
    print(f"\033[1;31m[x] {message}\033[0m")


def run_cmd(
    cmd: list[str],
    cwd: Path = ROOT_DIR,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Execute a system command."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a network port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def find_free_postgres_port(preferred_port: int = 5432) -> int:
    """Find a free port starting from the preferred port."""
    if not is_port_in_use(preferred_port):
        return preferred_port
    for port in range(preferred_port + 1, preferred_port + 20):
        if not is_port_in_use(port):
            return port
    return preferred_port


def ensure_uv() -> str:
    """Verify uv is installed or install it automatically."""
    print_step("Checking uv package manager")
    uv_path = shutil.which("uv")
    if uv_path:
        print_success(f"uv is already installed at: {uv_path}")
        return uv_path

    print_warning("uv not found on PATH. Attempting automatic installation...")
    try:
        run_cmd([sys.executable, "-m", "pip", "install", "--upgrade", "uv"])
        uv_path = shutil.which("uv")
        if uv_path:
            print_success(f"uv installed successfully at: {uv_path}")
            return uv_path
    except Exception as e:
        print_warning(f"pip install uv failed: {e}")

    # Fallback to user home .local/bin or cargo/bin
    if sys.platform == "win32":
        local_uv = Path.home() / ".local" / "bin" / "uv.exe"
        cargo_uv = Path.home() / ".cargo" / "bin" / "uv.exe"
    else:
        local_uv = Path.home() / ".local" / "bin" / "uv"
        cargo_uv = Path.home() / ".cargo" / "bin" / "uv"

    for candidate in (local_uv, cargo_uv):
        if candidate.exists():
            print_success(f"Found uv at {candidate}")
            return str(candidate)

    print_error("Failed to find or install uv. Please install uv from https://docs.astral.sh/uv/.")
    sys.exit(1)


def setup_env_file() -> None:
    """Create .env.development if missing, generating secure secrets and ports."""
    print_step("Checking environment configuration (.env.development)")
    if ENV_DEV.exists():
        print_success(".env.development already exists.")
        return

    if not ENV_EXAMPLE.exists():
        print_error(".env.example template not found. Cannot generate .env.development.")
        sys.exit(1)

    print("Generating .env.development from .env.example...")
    content = ENV_EXAMPLE.read_text(encoding="utf-8")

    # Generate cryptographically secure JWT secret (64 characters)
    jwt_secret = secrets.token_hex(32)
    content = content.replace("CHANGE_ME_TO_A_RANDOM_32_PLUS_CHAR_STRING", jwt_secret)
    content = content.replace("your-jwt-secret-key", jwt_secret)

    # Set host localhost so local Python can connect
    content = content.replace("POSTGRES_HOST=db", "POSTGRES_HOST=localhost")

    # Pick an available PostgreSQL port to prevent conflicts
    pg_port = find_free_postgres_port(5432)
    if pg_port != 5432:
        print_warning(f"Port 5432 is in use. Assigning POSTGRES_PORT={pg_port} for this project.")
        content = content.replace("POSTGRES_PORT=5432", f"POSTGRES_PORT={pg_port}")

    # Ensure Langfuse tracing is disabled by default for zero-friction local start
    content = content.replace("LANGFUSE_TRACING_ENABLED=true", "LANGFUSE_TRACING_ENABLED=false")

    ENV_DEV.write_text(content, encoding="utf-8")
    print_success(".env.development created with secure JWT secret and database settings.")


def check_docker() -> bool:
    """Check if Docker daemon is available and running."""
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False
    try:
        res = run_cmd([docker_bin, "info"], check=False, capture_output=True)
        return res.returncode == 0
    except Exception:
        return False


def setup_database(skip_docker: bool = False) -> None:
    """Start PostgreSQL + pgvector container via Docker Compose if enabled."""
    print_step("Checking Database Service")
    if skip_docker:
        print("Skipping Docker database setup as requested.")
        return

    if not check_docker():
        print_warning(
            "Docker Desktop is not currently running or not installed.\n"
            "If you already have an external PostgreSQL instance running, configure .env.development.\n"
            "Otherwise, please launch Docker Desktop to start the required pgvector database."
        )
        return

    print("Starting PostgreSQL (with pgvector) via Docker Compose...")
    try:
        run_cmd(
            [
                "docker",
                "compose",
                "--env-file",
                str(ENV_DEV),
                "up",
                "-d",
                "db",
            ]
        )
        print_success("Database container is starting.")

        # Wait up to 15 seconds for Postgres to become healthy
        print("Waiting for database health check...")
        for _ in range(15):
            res = run_cmd(
                ["docker", "compose", "ps", "db", "--format", "json"],
                check=False,
                capture_output=True,
            )
            if "healthy" in res.stdout.lower() or "running" in res.stdout.lower():
                print_success("Database is healthy and ready.")
                return
            time.sleep(1)
    except Exception as e:
        print_warning(f"Failed to start Docker container automatically: {e}")


def sync_python_dependencies(uv_bin: str) -> None:
    """Synchronize Python dependencies using uv sync."""
    print_step("Synchronizing Python dependencies via uv sync")
    run_cmd([uv_bin, "sync"])
    print_success("Python environment synced successfully.")


def setup_git_hooks(uv_bin: str) -> None:
    """Install pre-commit hooks."""
    print_step("Installing git pre-commit hooks")
    git_dir = ROOT_DIR / ".git"
    if not git_dir.exists():
        print("Not a git repository, skipping pre-commit hooks.")
        return
    try:
        run_cmd([uv_bin, "run", "pre-commit", "install"], check=False)
        print_success("Pre-commit hooks installed.")
    except Exception as e:
        print_warning(f"Could not install pre-commit hooks: {e}")


def run_database_migrations(uv_bin: str) -> None:
    """Run Alembic database migrations."""
    print_step("Running Alembic database migrations")
    try:
        run_cmd([uv_bin, "run", "alembic", "upgrade", "head"])
        print_success("Alembic database migrations applied successfully.")
    except subprocess.CalledProcessError:
        print_error(
            "Failed to run database migrations. Ensure PostgreSQL is running and credentials match .env.development."
        )


def start_server(uv_bin: str) -> None:
    """Start the application server with hot-reload."""
    print_step("Starting FastAPI LangGraph Application")
    print_success("Server will be available at http://127.0.0.1:8000")
    print_success("Interactive API Docs: http://127.0.0.1:8000/docs")
    print("Press CTRL+C to stop the server.\n")
    try:
        run_cmd([uv_bin, "run", "python", "run.py"])
    except KeyboardInterrupt:
        print("\nServer stopped.")


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Bootstrap and run the ChronaMind FastAPI LangGraph template.")
    parser.add_argument(
        "--start",
        action="store_true",
        default=True,
        help="Start the application server after setup (default: True)",
    )
    parser.add_argument(
        "--no-start",
        dest="start",
        action="store_false",
        help="Run setup without launching the server",
    )
    parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Skip starting the Docker database container",
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  ChronaMind / FastAPI LangGraph Agent Setup & Runner")
    print("=" * 60)

    uv_bin = ensure_uv()
    setup_env_file()
    setup_database(skip_docker=args.skip_docker)
    sync_python_dependencies(uv_bin)
    setup_git_hooks(uv_bin)
    run_database_migrations(uv_bin)

    print("\n" + "=" * 60)
    print_success("Project setup completed successfully!")
    print("=" * 60)

    if args.start:
        start_server(uv_bin)
    else:
        print("\nTo start the server manually anytime, run:")
        print(f"  {uv_bin} run python run.py")


if __name__ == "__main__":
    main()
