from __future__ import annotations

from pathlib import Path

from kura_workers.system_config import _get_agent_behavior
from tests.architecture.conftest import assert_kura_api_test_passes


AGENT_ROUTE = Path("api/src/routes/agent.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")

RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::write_with_proof_preflight_contract_schema_version_is_pinned",
    "routes::agent::tests::write_with_proof_preflight_contract_exposes_blockers",
    "routes::agent::tests::write_with_proof_preflight_contract_dedupes_blocker_codes",
    "routes::agent::tests::formal_event_type_policy_contract_schema_version_is_pinned",
    "routes::agent::tests::formal_event_type_policy_contract_rejects_unknown_event_type",
    "routes::agent::tests::formal_event_type_policy_contract_accepts_registered_event_type",
)


def _operational() -> dict:
    return _get_agent_behavior()["operational"]


def test_system_config_declares_formal_event_type_policy() -> None:
    policy = _operational()["formal_event_type_policy_v1"]
    assert policy["schema_version"] == "formal_event_type_policy.v1"
    assert policy["requires_dotted_lowercase_shape"] is True
    assert policy["requires_registry_membership"] is True
    assert policy["unknown_event_type_action"] == "block_with_reason"
    assert (
        "POST /v1/agent/write-with-proof" in policy["enforcement_surfaces"]
    )


def test_system_config_declares_write_preflight_contract() -> None:
    contract = _operational()["write_preflight_v1"]
    assert contract["schema_version"] == "write_preflight.v1"
    required_domains = set(contract["required_blocker_domains"])
    assert {
        "event_type_policy",
        "consent_gate",
        "workflow_gate",
        "intent_handshake",
        "high_impact_confirmation",
        "autonomy_gate",
        "verification",
    }.issubset(required_domains)


def test_mcp_runtime_declares_simulate_first_default_and_blocked_fallback() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert '"mode": {' in src
    assert '"enum": ["commit", "simulate", "write_with_proof"]' in src
    assert '"default": "simulate"' in src
    assert "allow_legacy_write_with_proof_fallback" in src
    assert "write_with_proof_fallback_blocked" in src
    assert "context_required_before_write" in src


def test_agent_route_declares_preflight_and_formal_event_type_policy_constants() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "AGENT_WRITE_PREFLIGHT_SCHEMA_VERSION" in src
    assert "AGENT_FORMAL_EVENT_TYPE_POLICY_SCHEMA_VERSION" in src
    assert "write_with_proof blocked by preflight checks" in src
    assert "formal_event_type_unknown" in src


def test_write_preflight_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
