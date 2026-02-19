"""Budgeted context + paged projection reload contract.

Architecture Decision (kura-training-oybz):

Large user histories must not force unbounded context payloads. The agent
needs deterministic reload mechanics (cursor pagination + explicit overflow
metadata) instead of ad-hoc retries.
"""
from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes

AGENT_ROUTE = Path("api/src/routes/agent.rs")
PROJECTIONS_ROUTE = Path("api/src/routes/projections.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")

RUST_TESTS: tuple[str, ...] = (
    "routes::projections::tests::projection_cursor_roundtrip_preserves_key",
    "routes::projections::tests::projection_cursor_rejects_invalid_payload",
    "routes::agent::tests::clamp_budget_tokens_validates_and_clamps",
)


def test_projection_routes_expose_paged_reload_contract() -> None:
    src = PROJECTIONS_ROUTE.read_text(encoding="utf-8")
    assert "/v1/projections/{projection_type}/paged" in src
    assert "pub struct ListProjectionPageParams" in src
    assert "pub struct PaginatedProjectionResponse" in src
    assert "next_cursor" in src
    assert "has_more" in src
    assert "encode_projection_cursor" in src
    assert "decode_projection_cursor" in src


def test_agent_context_declares_budget_and_overflow_fields() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "pub budget_tokens: Option<i64>" in src
    assert "pub included_tokens_estimate: usize" in src
    assert "pub overflow: Option<AgentContextOverflow>" in src
    assert "pub struct AgentContextOverflow" in src
    assert "pub struct AgentContextOverflowSection" in src
    assert "apply_agent_context_budget(&mut response)" in src
    assert "clamp_budget_tokens(params.budget_tokens)?" in src


def test_mcp_runtime_declares_budget_and_cursor_reload_inputs() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "\"budget_tokens\"" in src
    assert "\"cursor\"" in src
    assert "\"limit\"" in src
    assert "/v1/projections/{projection_type}/paged" in src
    assert "projection_type is required when limit/cursor is provided" in src


def test_runtime_contracts_pass() -> None:
    for test_name in RUST_TESTS:
        assert_kura_api_test_passes(test_name)
