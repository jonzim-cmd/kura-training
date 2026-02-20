from __future__ import annotations

from pathlib import Path

from kura_workers.handlers.objective_state import _objective_from_legacy_goal

HANDLER_INIT = Path("workers/src/kura_workers/handlers/__init__.py")
OBJECTIVE_STATE_HANDLER = Path("workers/src/kura_workers/handlers/objective_state.py")


def test_objective_state_handler_is_registered_in_bootstrap() -> None:
    src = HANDLER_INIT.read_text(encoding="utf-8")
    assert "from . import objective_state" in src


def test_objective_state_projection_contract_surface_is_pinned() -> None:
    src = OBJECTIVE_STATE_HANDLER.read_text(encoding="utf-8")
    assert "'objective_state'" in src
    assert "objective_state.v1" in src
    assert "active_objective" in src
    assert "objective_history" in src
    assert "active_constraints" in src
    assert "unresolved_fields" in src
    assert "inferred_confidence" in src


def test_objective_state_accepts_legacy_goal_mapping() -> None:
    objective = _objective_from_legacy_goal(
        {
            "goal_type": "strength",
            "target_exercise": "barbell_back_squat",
            "target_1rm_kg": 160,
            "timeframe_weeks": 12,
        }
    )
    assert objective["source"] == "legacy_goal_set"
    assert objective["primary_goal"]["type"] == "strength"
    assert objective["primary_goal"]["target_exercise"] == "barbell_back_squat"
    assert objective["primary_goal"]["target_1rm_kg"] == 160

