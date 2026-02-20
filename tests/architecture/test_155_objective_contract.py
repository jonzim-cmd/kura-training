from __future__ import annotations

from kura_workers.objective_contract_v1 import objective_contract_v1
from kura_workers.system_config import _get_conventions


def test_objective_contract_is_declared_in_system_conventions() -> None:
    conventions = _get_conventions()
    block = conventions["objective_contract_v1"]
    assert "contract" in block
    assert block["contract"]["schema_version"] == "objective_contract.v1"


def test_objective_contract_pins_modes_and_required_fields() -> None:
    contract = objective_contract_v1()
    assert set(contract["modes"]) == {"journal", "collaborate", "coach"}
    required = set(contract["required_objective_fields"])
    assert {
        "objective_id",
        "mode",
        "primary_goal",
        "secondary_goals",
        "anti_goals",
        "success_metrics",
        "constraint_markers",
        "source",
        "confidence",
    } <= required


def test_objective_contract_keeps_legacy_goal_set_compatibility() -> None:
    contract = objective_contract_v1()
    legacy = contract["legacy_compatibility"]
    assert legacy["goal_set_supported"] is True
    assert legacy["replay_safe"] is True
    assert legacy["non_destructive"] is True


def test_objective_event_surface_is_pinned() -> None:
    contract = objective_contract_v1()
    surface = contract["event_surface"]
    assert surface == {
        "set": "objective.set",
        "update": "objective.updated",
        "archive": "objective.archived",
        "override_rationale": "advisory.override.recorded",
    }

