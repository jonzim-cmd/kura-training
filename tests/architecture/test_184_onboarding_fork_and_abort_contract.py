from __future__ import annotations

from pathlib import Path

from kura_workers.system_config import _get_conventions


AGENT_ROUTE = Path("api/src/routes/agent.rs")
EVENTS_ROUTE = Path("api/src/routes/events.rs")
MCP_RUNTIME = Path("mcp-runtime/src/lib.rs")
USER_PROFILE_HANDLER = Path("workers/src/kura_workers/handlers/user_profile.py")


def test_first_contact_contract_offers_quick_deep_with_deep_default() -> None:
    interview_offer = _get_conventions()["first_contact_opening_v1"]["interview_offer"]
    assert interview_offer["format"] == "offer_onboarding_fork_quick_or_deep"
    assert interview_offer["default_path"] == "deep"
    assert interview_offer["recommended_path"] == "deep"
    options = interview_offer["options"]
    assert {option["path"] for option in options} >= {"quick", "deep"}


def test_agent_brief_contract_tracks_onboarding_aborted_state() -> None:
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "pub onboarding_aborted: bool" in src
    assert "workflow_state.onboarding_closed || workflow_state.onboarding_aborted" in src
    assert "\"workflow.onboarding.aborted\".to_string()" in src


def test_worker_and_api_routes_persist_onboarding_aborted_marker() -> None:
    worker_src = USER_PROFILE_HANDLER.read_text(encoding="utf-8")
    events_src = EVENTS_ROUTE.read_text(encoding="utf-8")
    assert "\"workflow.onboarding.aborted\"" in worker_src
    assert "onboarding_aborted" in worker_src
    assert "WORKFLOW_ONBOARDING_ABORTED_EVENT_TYPE" in events_src


def test_mcp_runtime_first_contact_copy_mentions_quick_deep_fork() -> None:
    src = MCP_RUNTIME.read_text(encoding="utf-8")
    assert "offer onboarding path fork (Quick or Deep, Deep recommended/default)" in src
