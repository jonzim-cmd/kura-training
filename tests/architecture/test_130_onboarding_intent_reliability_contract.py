from __future__ import annotations

from kura_workers.handlers.user_profile import _should_suggest_onboarding


def test_onboarding_prompt_is_not_disabled_by_event_count_alone() -> None:
    coverage = [
        {"area": "training_background", "status": "uncovered"},
        {"area": "baseline_profile", "status": "covered"},
        {"area": "unit_preferences", "status": "covered"},
    ]
    assert _should_suggest_onboarding(500, coverage) is True


def test_onboarding_prompt_turns_off_after_workflow_close() -> None:
    coverage = [
        {"area": "training_background", "status": "uncovered"},
        {"area": "baseline_profile", "status": "uncovered"},
        {"area": "unit_preferences", "status": "uncovered"},
    ]
    assert _should_suggest_onboarding(0, coverage, onboarding_closed=True) is False


def test_onboarding_contract_requires_dual_path_intent_tokens() -> None:
    from pathlib import Path

    src = Path("api/src/routes/agent.rs").read_text(encoding="utf-8")
    assert "offer_onboarding" in src
    assert "allow_skip_and_log_now" in src
