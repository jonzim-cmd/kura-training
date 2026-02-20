"""Startup brief gate + discovery hygiene contract.

Architecture Decision (kura-training-2of5):

First-contact behavior must be deterministic even with payload truncation and
agent-context drift. MCP therefore enforces a startup brief gate:
`kura_agent_brief` must run before broad tool orchestration, and onboarding
signals are sourced from this minimal brief surface. Import/provider tools are
hidden by default in the runtime profile to avoid onboarding noise until those
flows are explicitly enabled.
"""
from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import assert_kura_mcp_runtime_test_passes


MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")

RUNTIME_TESTS: tuple[str, ...] = (
    "tests::initialize_instructions_prioritize_startup_brief_and_first_contact_onboarding",
    "tests::agent_brief_tool_schema_defaults_to_startup_minimal_bundle",
    "tests::startup_brief_gate_blocks_non_exempt_tools_until_loaded",
    "tests::startup_brief_gate_unlocks_after_brief_load",
    "tests::discover_defaults_only_include_capabilities_section",
    "tests::import_and_provider_tools_hidden_by_default_runtime_profile",
)


def test_mcp_runtime_declares_startup_brief_gate_and_minimal_tool() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "kura_agent_brief" in src
    assert "startup_brief_required" in src
    assert "required_first_tool\": \"kura_agent_brief\"" in src
    assert "should_block_for_startup_brief" in src
    assert "mark_brief_loaded" in src
    assert "is_brief_loaded" in src


def test_mcp_runtime_declares_discovery_hygiene_flag_for_import_provider_tools() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "KURA_MCP_ENABLE_IMPORT_PROVIDER_TOOLS" in src
    assert "import_provider_tools_exposed" in src
    assert "if !import_device_tools_enabled()" in src
    assert "kura_import_job_create" in src
    assert "kura_provider_connections_list" in src


def test_startup_brief_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_mcp_runtime_test_passes(test_name)
