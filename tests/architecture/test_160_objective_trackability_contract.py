from __future__ import annotations

from kura_workers.handlers.objective_advisory import _has_trackable_target
from kura_workers.objective_advisory_v1 import objective_advisory_contract_v1


def test_objective_trackability_rule_requires_metrics_or_target() -> None:
    contract = objective_advisory_contract_v1()
    rules = contract["trackability_rules"]
    assert rules["requires_success_metrics_or_target"] is True
    assert rules["staleness_days"] >= 14
    assert "objective_trackability_gap" in contract["required_reason_codes"]


def test_trackability_detection_marks_missing_target_as_untrackable() -> None:
    assert _has_trackable_target({"primary_goal": {"type": "performance"}}) is False


def test_trackability_detection_accepts_primary_goal_target_fields() -> None:
    assert (
        _has_trackable_target(
            {"primary_goal": {"type": "performance", "target_metric": "run_800m_time"}}
        )
        is True
    )
