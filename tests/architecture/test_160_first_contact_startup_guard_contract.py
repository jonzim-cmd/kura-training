from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import (
    assert_kura_api_test_passes,
    assert_kura_mcp_runtime_test_passes,
)


AGENT_ROUTE = Path("api/src/routes/agent.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")

API_RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::agent_context_brief_contract_exposes_required_fields",
    "routes::agent::tests::first_contact_contract_does_not_reactivate_after_bootstrap_progress",
)

MCP_RUNTIME_TESTS: tuple[str, ...] = (
    "tests::initialize_instructions_prioritize_startup_context_and_first_contact_onboarding",
    "tests::startup_tool_surface_contract_reports_consistency",
)


def test_agent_brief_includes_first_contact_contract_and_response_guard() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")

    assert "pub struct AgentBriefFirstContactOpening" in src
    assert "pub struct AgentBriefResponseGuard" in src
    assert "first_contact_onboarding_active" in src
    assert "user_profile_bootstrap_pending" in src
    assert "first_assistant_turn_after_brief" in src
    assert "first_contact_response_guard.v1" in src
    assert "workflow.onboarding.aborted" in src


def test_mcp_startup_gate_exposes_context_fallback_and_anti_hallucination_hint() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")

    assert 'const STARTUP_REQUIRED_FIRST_TOOL: &str = "kura_agent_context"' in src
    assert 'const STARTUP_PREFERRED_FIRST_TOOL: &str = "kura_agent_brief"' in src
    assert 'const STARTUP_FALLBACK_FIRST_TOOL: &str = "kura_agent_context"' in src
    assert "STARTUP_ONBOARDING_NEXT_ACTION" in src
    assert "context_required_brief_preferred" in src
    assert "Avoid dashboard/booking claims unless explicitly present in loaded brief/context payloads." in src
    assert "should_block_for_startup_context(" in src


def test_first_contact_startup_guard_runtime_contracts_pass() -> None:
    for test_name in API_RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
    for test_name in MCP_RUNTIME_TESTS:
        assert_kura_mcp_runtime_test_passes(test_name)
