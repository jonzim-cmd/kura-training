from __future__ import annotations

from pathlib import Path


WORKFLOW_VISUALIZATION = Path("api/src/routes/agent/workflow_visualization.rs")
POLICY = Path("api/src/routes/agent/policy.rs")


def test_challenge_mode_hint_is_scoped_to_open_onboarding() -> None:
    src = WORKFLOW_VISUALIZATION.read_text(encoding="utf-8")

    assert "fn onboarding_open(" in src
    assert "let onboarding_hint_required = !intro_seen && onboarding_open(user_profile);" in src
    assert "onboarding_hint: if onboarding_hint_required" in src


def test_challenge_mode_onboarding_hint_uses_language_neutral_contract_copy() -> None:
    src = POLICY.read_text(encoding="utf-8")
    assert "Challenge Mode defaults to auto." in src
    assert "challenge_mode" in src
