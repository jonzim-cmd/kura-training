"""Retrieval loop-safety + observability contract.

Architecture Decision (kura-training-5lkb):

Budgeted context retrieval must remain deterministic under large-history pressure.
The MCP runtime therefore needs explicit loop guards and observable stop reasons,
plus replayable workflow tests so regressions are detected in CI.
"""
from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes

MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")

RUST_TESTS: tuple[str, ...] = (
    "tests::retrieval_fsm_blocks_repeated_signature_loops",
    "tests::retrieval_fsm_blocks_when_max_reload_budget_is_exhausted",
    "tests::retrieval_observability_tracks_overflow_and_abort_reasons",
    "tests::retrieval_replay_contract_stops_cursor_loop_with_reason",
    "tests::retrieval_replay_contract_allows_progressive_reload_then_resets",
)


def test_retrieval_fsm_contract_is_declared_in_runtime() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "RETRIEVAL_FSM_MAX_RELOADS_PER_WINDOW" in src
    assert "RETRIEVAL_FSM_MAX_REPEAT_SIGNATURE_STREAK" in src
    assert "maybe_block_retrieval_loop" in src
    assert "retrieval_loop_guard_blocked" in src
    assert "\"reason_code\"" in src
    assert "\"max_reloads_per_window\"" in src


def test_observability_contract_exposes_required_metrics() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "RETRIEVAL_OBSERVABILITY_SCHEMA_VERSION" in src
    assert "\"overflow_rate\"" in src
    assert "\"context_hit_rate\"" in src
    assert "\"max_reload_depth\"" in src
    assert "\"abort_reasons\"" in src
    assert "attach_runtime_observability" in src


def test_runtime_replay_tests_are_pinned() -> None:
    for test_name in RUST_TESTS:
        assert_kura_api_test_passes(test_name)
