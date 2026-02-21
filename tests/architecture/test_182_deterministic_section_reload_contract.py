"""Deterministic section-reload contract for startup and overflow recovery.

Architecture Decision (kura-training-79mg / kura-training-kqub / kura-training-chuq):

Startup-critical data must be recoverable without monolithic context payloads.
The platform therefore exposes a stable section index + section fetch contract,
mirrored in MCP tools and CLI diagnostics with machine-readable reason codes.
If startup sections are missing, recovery must stay deterministic and auditable.
"""
from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import (
    assert_kura_api_test_passes,
    assert_kura_mcp_runtime_test_passes,
)


AGENT_ROUTE = Path("api/src/routes/agent.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")
CLI_MAIN = Path("cli/src/main.rs")

API_TESTS: tuple[str, ...] = (
    "routes::agent::tests::agent_context_section_index_contract_includes_startup_capsule_and_critical_sections",
    "routes::agent::tests::agent_context_section_fetch_field_projection_contract_projects_top_level_fields",
    "routes::agent::tests::agent_context_section_cursor_contract_roundtrips_base64_key",
)

MCP_RUNTIME_TESTS: tuple[str, ...] = (
    "tests::agent_section_tools_are_exposed_with_contract_inputs",
    "tests::startup_tool_surface_contract_reports_consistency",
    "tests::mcp_status_exposes_tool_surface_contract_fields",
    "tests::startup_context_missing_sections_tracks_capsule_and_critical_overflow_entries",
)


def test_agent_route_declares_section_index_and_section_fetch_contract() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "/v1/agent/context/section-index" in src
    assert "/v1/agent/context/section-fetch" in src
    assert "AGENT_CONTEXT_SECTION_INDEX_SCHEMA_VERSION" in src
    assert "AGENT_CONTEXT_SECTION_FETCH_SCHEMA_VERSION" in src
    assert "STARTUP_CAPSULE_SCHEMA_VERSION" in src
    assert "build_agent_context_section_index" in src
    assert "fetch_projection_page_by_key" in src


def test_mcp_runtime_declares_section_tools_and_startup_diagnostics() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "kura_agent_section_index" in src
    assert "kura_agent_section_fetch" in src
    assert "run_startup_diagnostics" in src
    assert "mcp_startup_diagnostic.v1" in src
    assert "tool_surface" in src
    assert "startup_critical_sections_missing" in src


def test_cli_exposes_mcp_diagnose_entrypoint() -> None:
    src = CLI_MAIN.read_text(encoding="utf-8")
    assert "Commands::Mcp" in src
    assert "commands::mcp::McpCommands" in src


def test_runtime_and_api_section_reload_contract_tests_pass() -> None:
    for test_name in API_TESTS:
        assert_kura_api_test_passes(test_name)
    for test_name in MCP_RUNTIME_TESTS:
        assert_kura_mcp_runtime_test_passes(test_name)
