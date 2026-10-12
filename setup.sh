#!/usr/bin/env bash
# Quick setup and start script for Linux/macOS
# Usage: ./setup.sh [--no-start] [--skip-docker]

set -e

# 1. Ensure uv is available
if ! command -v uv &>/dev/null; then
    if [ -f "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    elif [ -f "$HOME/.cargo/bin/uv" ]; then
        export PATH="$HOME/.cargo/bin:$PATH"
    else
        echo "Installing uv package manager (astral.sh)..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi

uv run python scripts/setup.py "$@"
