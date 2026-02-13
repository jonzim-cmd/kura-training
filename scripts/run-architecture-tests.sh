#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PYTHONPATH=workers/src uv run --project workers python -m pytest tests/architecture -q "$@"
