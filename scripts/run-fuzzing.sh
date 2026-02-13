#!/usr/bin/env bash
# Run adversarial fuzzing tests against the Kura API.
#
# Usage:
#   ./scripts/run-fuzzing.sh                    # Deterministic fixtures + skip Hypothesis
#   ./scripts/run-fuzzing.sh --full             # Full suite including Hypothesis
#   ./scripts/run-fuzzing.sh --seed 42          # Reproducible Hypothesis run
#   ./scripts/run-fuzzing.sh --live-llm         # Include LLM-generated scenarios
#
# Prerequisites:
#   - Kura API running (default: http://localhost:3000)
#   - KURA_API_KEY set (test user API key)
#   - For --live-llm: ANTHROPIC_API_KEY set
#
# CI mode:
#   KURA_API_KEY=... ./scripts/run-fuzzing.sh --seed 42

set -euo pipefail

cd "$(dirname "$0")/../workers"

PYTEST_ARGS=("-v" "--tb=short")
MODE="fixtures"

while [[ $# -gt 0 ]]; do
    case $1 in
        --full)
            MODE="full"
            shift
            ;;
        --seed)
            PYTEST_ARGS+=("--hypothesis-seed=$2")
            shift 2
            ;;
        --live-llm)
            PYTEST_ARGS+=("--live-llm")
            shift
            ;;
        *)
            PYTEST_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ -z "${KURA_API_KEY:-}" ]]; then
    echo "⚠ KURA_API_KEY not set — fuzzing tests will be skipped"
    echo "  Set it: export KURA_API_KEY=kura_sk_..."
fi

case "$MODE" in
    fixtures)
        echo "Running fixture regression tests..."
        uv run pytest tests/fuzzing/test_fixtures.py tests/fuzzing/test_scenarios.py "${PYTEST_ARGS[@]}"
        ;;
    full)
        echo "Running full fuzzing suite (Hypothesis + scenarios + fixtures)..."
        uv run pytest tests/fuzzing/ "${PYTEST_ARGS[@]}"
        ;;
esac
