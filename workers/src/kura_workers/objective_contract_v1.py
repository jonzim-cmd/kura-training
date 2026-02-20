"""Objective contract for Evidence-OS objective modeling."""

from __future__ import annotations

from typing import Any


def objective_goal_pack_registry_v1() -> dict[str, dict[str, Any]]:
    """Return canonical goal-pack templates.

    Goal packs are advisory templates, not hard constraints.
    """
    return {
        "performance": {
            "advisory_template": True,
            "default_mode": "coach",
            "primary_goal_examples": [
                "run_800m_time",
                "sprint_30m_time",
                "jump_height_cm",
                "swim_400m_time",
            ],
            "recommended_success_metrics": [
                "target_event_time",
                "time_trial_delta_pct",
                "objective_trackability_score",
            ],
        },
        "physique": {
            "advisory_template": True,
            "default_mode": "coach",
            "primary_goal_examples": [
                "hypertrophy_focus",
                "body_fat_reduction",
            ],
            "recommended_success_metrics": [
                "bodyweight_trend",
                "circumference_delta",
                "consistency_score",
            ],
        },
        "health": {
            "advisory_template": True,
            "default_mode": "collaborate",
            "primary_goal_examples": [
                "general_fitness",
                "cardiorespiratory_health",
                "injury_risk_reduction",
            ],
            "recommended_success_metrics": [
                "weekly_activity_consistency",
                "readiness_stability",
                "symptom_burden_trend",
            ],
        },
        "explore": {
            "advisory_template": True,
            "default_mode": "journal",
            "primary_goal_examples": [
                "multi_sport_exploration",
                "training_discovery",
            ],
            "recommended_success_metrics": [
                "coverage_diversity_index",
                "adherence_rate",
                "subjective_enjoyment_trend",
            ],
        },
    }


def objective_contract_v1() -> dict[str, Any]:
    """Return objective contract for objective-first planning context."""
    return {
        "schema_version": "objective_contract.v1",
        "modes": ["journal", "collaborate", "coach"],
        "required_objective_fields": [
            "objective_id",
            "mode",
            "primary_goal",
            "secondary_goals",
            "anti_goals",
            "success_metrics",
            "constraint_markers",
            "source",
            "confidence",
        ],
        "event_surface": {
            "set": "objective.set",
            "update": "objective.updated",
            "archive": "objective.archived",
            "override_rationale": "advisory.override.recorded",
        },
        "legacy_compatibility": {
            "goal_set_supported": True,
            "mapping_policy": "latest_goal_set_maps_to_primary_goal_when_objective_missing",
            "replay_safe": True,
            "non_destructive": True,
        },
        "goal_pack_registry": objective_goal_pack_registry_v1(),
        "non_goals": [
            "No hard auto-blocking from objective warnings.",
            "No mandatory user objective before logging starts.",
        ],
    }

