from __future__ import annotations

from kura_workers.objective_contract_v1 import objective_goal_pack_registry_v1


def test_goal_pack_registry_covers_required_templates() -> None:
    registry = objective_goal_pack_registry_v1()
    expected = {"performance", "physique", "health", "explore"}
    assert set(registry.keys()) == expected


def test_goal_pack_templates_remain_advisory() -> None:
    registry = objective_goal_pack_registry_v1()
    for pack in registry.values():
        assert pack["advisory_template"] is True
        assert pack["default_mode"] in {"journal", "collaborate", "coach"}
        assert pack["primary_goal_examples"]
        assert pack["recommended_success_metrics"]

