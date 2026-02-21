from __future__ import annotations

from pathlib import Path

from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import (
    assert_kura_api_test_passes,
    assert_kura_mcp_runtime_test_passes,
)


AGENT_ROUTE = Path("api/src/routes/agent.rs")
EVENTS_ROUTE = Path("api/src/routes/events.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")
USER_PROFILE_HANDLER = Path("workers/src/kura_workers/handlers/user_profile.py")

API_RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::agent_context_brief_contract_exposes_required_fields",
    "routes::agent::tests::workflow_gate_blocks_planning_after_restart_until_reclosed_or_overridden",
    "routes::agent::tests::effective_workflow_state_applies_marker_deltas_after_projection_timestamp",
    "routes::events::tests::test_legacy_domain_invariants_block_planning_after_restart_without_reclose_or_override",
    "routes::events::tests::test_legacy_domain_invariants_allow_planning_after_restart_with_override",
)

MCP_RUNTIME_TESTS: tuple[str, ...] = (
    "tests::initialize_instructions_prioritize_startup_context_and_first_contact_onboarding",
)


def test_first_contact_contract_offers_quick_deep_with_deep_default() -> None:
    interview_offer = _get_conventions()["first_contact_opening_v1"]["interview_offer"]
    assert interview_offer["format"] == "offer_onboarding_fork_quick_or_deep"
    assert interview_offer["default_path"] == "deep"
    assert interview_offer["recommended_path"] == "deep"
    options = interview_offer["options"]
    assert {option["path"] for option in options} >= {"quick", "deep"}


def test_onboarding_contract_sources_include_restart_lifecycle() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    events_src = EVENTS_ROUTE.read_text(encoding="utf-8")
    worker_src = USER_PROFILE_HANDLER.read_text(encoding="utf-8")
    assert "pub onboarding_aborted: bool" in src
    assert "\"workflow.onboarding.aborted\".to_string()" in src
    assert "WORKFLOW_ONBOARDING_RESTARTED_EVENT_TYPE" in src
    assert "WORKFLOW_ONBOARDING_RESTARTED_EVENT_TYPE" in events_src
    assert "\"workflow.onboarding.restarted\"" in worker_src


def test_mcp_runtime_first_contact_copy_mentions_quick_deep_fork() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "offer onboarding path fork (Quick or Deep, Deep recommended/default)" in src


def test_onboarding_fork_abort_restart_runtime_contracts_pass() -> None:
    for test_name in API_RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
    for test_name in MCP_RUNTIME_TESTS:
        assert_kura_mcp_runtime_test_passes(test_name)
