"""CLI + MCP retrieval DX contract.

Architecture Decision (kura-training-21cw.2):

Deterministic retrieval must stay usable after context compaction. The CLI needs
first-class section index/fetch commands (no generic request escape hatch), and
MCP section tools must return explicit recovery metadata (ordered fetch plan and
pagination next-call hints). Contracts are enforced with executable tests.
"""
from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import (
    assert_kura_mcp_runtime_test_passes,
    assert_rust_package_test_passes,
)

CLI_AGENT = Path("cli/src/commands/agent.rs")
CLI_MAIN = Path("cli/src/main.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")

CLI_TESTS: tuple[str, ...] = (
    "commands::agent::tests::build_context_query_includes_budget_tokens_when_present",
    "commands::agent::tests::build_context_query_supports_section_index_parity_params",
    "commands::agent::tests::build_section_fetch_query_serializes_optional_params",
)

MCP_TESTS: tuple[str, ...] = (
    "tests::section_index_recovery_contract_prefers_startup_order_and_expands_unknown_sections",
    "tests::section_fetch_pagination_contract_exposes_next_call_when_cursor_exists",
)


def test_cli_declares_first_class_section_tools_and_budget_hint() -> None:
    src = CLI_AGENT.read_text(encoding="utf-8")
    assert "SectionIndex {" in src
    assert "SectionFetch {" in src
    assert "budget_tokens: Option<u32>" in src
    assert "\"/v1/agent/context/section-index\"" in src
    assert "\"/v1/agent/context/section-fetch\"" in src


def test_cli_legacy_context_alias_supports_budget_tokens() -> None:
    src = CLI_MAIN.read_text(encoding="utf-8")
    assert "budget_tokens: Option<u32>" in src
    assert "budget_tokens," in src


def test_mcp_declares_recovery_metadata_for_section_tools() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "\"deterministic_recovery\"" in src
    assert "\"critical_fetch_order\"" in src
    assert "\"critical_fetch_calls\"" in src
    assert "\"section_contract\"" in src
    assert "\"pagination\"" in src
    assert "\"next_call\"" in src
    assert "\"reason_code\"" in src
    assert "\"next_action\"" in src


def test_cli_runtime_contracts_pass() -> None:
    for test_name in CLI_TESTS:
        assert_rust_package_test_passes("kura-cli", test_name)


def test_mcp_runtime_contracts_pass() -> None:
    for test_name in MCP_TESTS:
        assert_kura_mcp_runtime_test_passes(test_name)
