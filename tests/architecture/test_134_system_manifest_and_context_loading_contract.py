"""System manifest + lean context contract.

Architecture Decision (kura-training-9g5):

The agent context brief must remain compact and complete as the system grows.
Deployment-static system config should be fetchable by section, and context
reads must support omitting the heavy system payload while preserving pointers
for deterministic reload.
"""
from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes

AGENT_ROUTE = Path("api/src/routes/agent.rs")
SYSTEM_ROUTE = Path("api/src/routes/system.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")
WORKER_SYSTEM_CONFIG = Path("workers/src/kura_workers/system_config.py")

RUST_TESTS: tuple[str, ...] = (
    "routes::system::tests::resolve_system_config_section_value_reads_root_and_nested_entries",
    "routes::system::tests::system_manifest_sections_include_nested_event_and_convention_entries",
    "routes::system::tests::system_manifest_prefers_section_metadata_when_present",
    "routes::system::tests::system_manifest_resource_uri_points_to_resolvable_section_uri",
    "routes::system::tests::system_manifest_sections_cache_reuses_sections_for_same_version",
    "routes::system::tests::if_none_match_matches_supports_weak_and_strong_forms",
    "routes::system::tests::system_cache_response_returns_not_modified_on_matching_etag",
    "routes::system::tests::system_cache_response_returns_ok_on_etag_miss",
    "routes::agent::tests::agent_context_brief_contract_exposes_required_fields",
)


def test_system_routes_expose_manifest_and_section_contract() -> None:
    src = SYSTEM_ROUTE.read_text(encoding="utf-8")
    assert "/v1/system/config/manifest" in src
    assert "/v1/system/config/section" in src
    assert "build_system_config_manifest_sections" in src
    assert "build_system_config_manifest_sections_cached" in src
    assert "resolve_system_config_section_value" in src
    assert "extract_system_section_metadata" in src
    assert "section_resource_uri" in src
    assert "status = 304" in src
    assert "IF_NONE_MATCH" in src
    assert "ETAG" in src
    assert "if_none_match_matches" in src


def test_agent_context_declares_include_system_and_manifest_driven_sections() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "pub include_system: Option<bool>" in src
    assert "let include_system = params.include_system.unwrap_or(true);" in src
    assert "build_system_config_manifest_sections_cached(system.version, &system.data)" in src
    assert "Erstkontakt: Kura kurz erklaeren" not in src


def test_mcp_runtime_declares_targeted_system_reload_tools() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "kura_system_manifest" in src
    assert "kura_system_section_get" in src
    assert "\"include_system\": { \"type\": \"boolean\", \"default\": false }" in src
    assert "kura://system/config/manifest" in src
    assert "kura_system_manifest to list sections" in src


def test_worker_system_config_declares_section_metadata_source_of_truth() -> None:
    src = WORKER_SYSTEM_CONFIG.read_text(encoding="utf-8")
    assert "SECTION_METADATA_SCHEMA_VERSION" in src
    assert "_build_section_metadata" in src
    assert "section_metadata" in src
    assert "system_config_section_metadata.v1" in src


def test_system_manifest_runtime_contracts_pass() -> None:
    for test_name in RUST_TESTS:
        assert_kura_api_test_passes(test_name)
