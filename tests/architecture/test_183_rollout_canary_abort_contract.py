"""Rollout/Canary abort contract for deterministic retrieval runtime.

Architecture Decision (kura-training-g6wk):

Deterministic retrieval changes must be deployable with a safe canary envelope.
The MCP runtime therefore exposes explicit rollout feature flags, cohorting, and
metric-based abort criteria (overflow/critical-missing/blocked/speculative) with
a rollback switch. Diagnostics must surface abort-required decisions so rollout
automation can fail fast without relying on model freestyle.
"""
from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import assert_kura_mcp_runtime_test_passes


MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")

RUNTIME_TESTS: tuple[str, ...] = (
    "tests::determinism_rollout_policy_parser_uses_defaults_when_unset",
    "tests::determinism_rollout_policy_parser_accepts_env_overrides",
    "tests::determinism_rollout_policy_parser_normalizes_user_and_email_allowlists",
    "tests::determinism_rollout_canary_bucket_is_stable",
    "tests::speculative_answer_rate_tracks_guard_signals",
    "tests::rollout_guard_recommends_abort_on_metric_breach",
    "tests::rollout_abort_guard_blocks_only_when_enforced_and_active",
    "tests::rollout_guard_matches_user_or_email_allowlists_before_bucket",
)


def test_mcp_runtime_declares_rollout_guard_flags_and_abort_thresholds() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "KURA_MCP_DETERMINISM_ROLLOUT_MODE" in src
    assert "KURA_MCP_DETERMINISM_CANARY_PERCENT" in src
    assert "KURA_MCP_DETERMINISM_CANARY_ALLOWLIST" in src
    assert "KURA_MCP_DETERMINISM_CANARY_USER_ALLOWLIST" in src
    assert "KURA_MCP_DETERMINISM_CANARY_EMAIL_ALLOWLIST" in src
    assert "KURA_MCP_DETERMINISM_ABORT_ON_BREACH" in src
    assert "KURA_MCP_ROLLOUT_ABORT_OVERFLOW_RATE_MAX" in src
    assert "KURA_MCP_ROLLOUT_ABORT_CRITICAL_MISSING_RATE_MAX" in src
    assert "KURA_MCP_ROLLOUT_ABORT_BLOCKED_RATE_MAX" in src
    assert "KURA_MCP_ROLLOUT_ABORT_SPECULATIVE_RATE_MAX" in src
    assert "rollout_guard" in src
    assert "rollout_abort_guard_blocked" in src
    assert "rollout_decision" in src


def test_rollout_guard_runtime_contract_tests_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_mcp_runtime_test_passes(test_name)
