#!/usr/bin/env bash
# Launch the MapToPoster Flask GUI
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Try uv first, then local venv, then system python
if command -v uv &> /dev/null; then
    echo "Starting MapToPoster GUI with uv..."
    uv run python gui.py "$@"
elif [ -f ".venv/bin/python" ]; then
    echo "Starting MapToPoster GUI (.venv)..."
    .venv/bin/python gui.py "$@"
elif command -v python3 &> /dev/null; then
    echo "Starting MapToPoster GUI..."
    python3 gui.py "$@"
else
    echo "Error: python not found."
    echo ""
    echo "Setup options:"
    echo "  1. uv sync                              (if uv is installed)"
    echo "  2. python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi
