from __future__ import annotations

from pathlib import Path

from kura_workers.handlers.objective_state import _default_objective_from_profile
from kura_workers.interview_guide import get_interview_guide


def _goals_coverage_area() -> dict:
    guide = get_interview_guide()
    for area in guide["coverage_areas"]:
        if area.get("area") == "goals":
            return area
    raise AssertionError("goals coverage area missing")


def test_onboarding_goals_area_produces_objective_and_legacy_goal_paths() -> None:
    goals = _goals_coverage_area()
    produces = goals["produces"]
    assert "objective.set" in produces
    assert "goal.set" in produces


def test_objective_default_is_explicit_when_user_goal_is_unclear() -> None:
    inferred = _default_objective_from_profile({"training_modality": "running"})
    assert inferred["source"] == "default_inferred"
    assert inferred["mode"] == "journal"
    assert inferred["primary_goal"]["type"] == "endurance_base"
    assert inferred["confidence"] < 0.6


def test_onboarding_skip_and_log_path_remains_available() -> None:
    src = Path("api/src/routes/agent.rs").read_text(encoding="utf-8")
    assert "allow_skip_and_log_now" in src
