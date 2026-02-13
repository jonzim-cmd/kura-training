#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

set -a && source .env && set +a
PYTHONPATH=workers/src uv run --project workers python scripts/performance_regression_gate.py "$@"
