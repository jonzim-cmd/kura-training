from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes


AGENT_ROUTE = Path("api/src/routes/agent.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")

RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::agent_context_brief_contract_exposes_required_fields",
    "routes::agent::tests::agent_context_brief_contract_clears_onboarding_gaps_when_closed",
)


def test_agent_context_declares_brief_first_contract_surface() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "pub struct AgentBrief" in src
    assert "pub struct AgentBriefWorkflowState" in src
    assert "pub struct AgentBriefSectionRef" in src
    assert "pub struct AgentBriefSystemConfigRef" in src
    assert "pub agent_brief: AgentBrief" in src
    assert "must_cover_intents" in src
    assert "coverage_gaps" in src
    assert "available_sections" in src
    assert "build_agent_brief" in src


def test_mcp_runtime_declares_structured_agent_context_overflow() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "AGENT_CONTEXT_OVERFLOW_SCHEMA_VERSION" in src
    assert "if tool == \"kura_agent_context\"" in src
    assert "enforce_agent_context_payload_limit" in src
    assert "omitted_sections" in src
    assert "agent_context_section_reload_hint" in src


def test_agent_context_brief_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
