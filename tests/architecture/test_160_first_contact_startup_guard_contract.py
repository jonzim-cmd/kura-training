from __future__ import annotations

from pathlib import Path


AGENT_ROUTE = Path("api/src/routes/agent.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")


def test_agent_brief_includes_first_contact_contract_and_response_guard() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")

    assert "pub struct AgentBriefFirstContactOpening" in src
    assert "pub struct AgentBriefResponseGuard" in src
    assert "first_contact_onboarding_active" in src
    assert "user_profile_bootstrap_pending" in src
    assert "first_assistant_turn_after_brief" in src
    assert "first_contact_response_guard.v1" in src
    assert "onboarding_skipped_by_user" in src


def test_mcp_startup_gate_exposes_context_fallback_and_anti_hallucination_hint() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")

    assert 'const STARTUP_REQUIRED_FIRST_TOOL: &str = "kura_agent_context"' in src
    assert 'const STARTUP_PREFERRED_FIRST_TOOL: &str = "kura_agent_brief"' in src
    assert 'const STARTUP_FALLBACK_FIRST_TOOL: &str = "kura_agent_context"' in src
    assert "context_required_brief_preferred" in src
    assert "Avoid dashboard/booking claims unless explicitly present in loaded brief/context payloads." in src
    assert "should_block_for_startup_context(" in src
