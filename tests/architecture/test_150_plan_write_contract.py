from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import (
    assert_kura_api_test_passes,
    assert_kura_mcp_runtime_test_passes,
)


MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")

API_RUNTIME_TESTS: tuple[str, ...] = (
    "routes::events::tests::test_legacy_domain_invariants_block_plan_writes_on_legacy_path",
)

MCP_RUNTIME_TESTS: tuple[str, ...] = (
    "tests::high_impact_classification_keeps_routine_plan_update_low_impact",
    "tests::high_impact_classification_escalates_large_plan_shift",
    "tests::plan_write_detection_matches_training_plan_prefix",
    "tests::write_api_error_classification_maps_preflight_blockers",
    "tests::write_api_error_classification_maps_approval_timeout",
)

def test_mcp_runtime_declares_plan_write_and_error_translation_primitives() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "contains_plan_writes" in src
    assert "plan_write_requires_write_with_proof_error" in src
    assert "classify_write_api_error" in src
    assert "write_preflight_blocked" in src
    assert "approval_timeout" in src
    assert "training_plan.* writes must use mode=write_with_proof" in src


def test_plan_write_runtime_contracts_pass() -> None:
    for test_name in API_RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
    for test_name in MCP_RUNTIME_TESTS:
        assert_kura_mcp_runtime_test_passes(test_name)
