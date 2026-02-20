from __future__ import annotations

from kura_workers.event_conventions import get_event_conventions
from kura_workers.handlers.objective_state import _objective_from_legacy_goal
from kura_workers.objective_contract_v1 import objective_contract_v1


def test_goal_set_remains_supported_in_event_conventions() -> None:
    conventions = get_event_conventions()
    goal_set = conventions["goal.set"]
    assert goal_set["legacy_compatibility"]["supported"] is True
    assert goal_set["legacy_compatibility"]["maps_to_objective_state"] is True


def test_objective_contract_declares_legacy_goal_mapping_policy() -> None:
    contract = objective_contract_v1()
    legacy = contract["legacy_compatibility"]
    assert legacy["goal_set_supported"] is True
    assert "maps_to_primary_goal" in legacy["mapping_policy"]


def test_legacy_goal_mapping_keeps_optional_fields() -> None:
    objective = _objective_from_legacy_goal(
        {
            "goal_type": "endurance",
            "description": "Sub 40 over 10k",
            "timeframe_weeks": 16,
        }
    )
    primary_goal = objective["primary_goal"]
    assert primary_goal["type"] == "endurance"
    assert primary_goal["description"] == "Sub 40 over 10k"
    assert primary_goal["timeframe_weeks"] == 16

