"""Objective-aware statistical method contract."""

from __future__ import annotations

from typing import Any


def objective_statistical_method_contract_v1() -> dict[str, Any]:
    """Return the statistical contract for objective/modality stratification."""
    return {
        "schema_version": "objective_statistical_method.v1",
        "stratification_axes": [
            "objective_mode",
            "modality",
            "quality_band",
        ],
        "estimand_policy": {
            "identity_surface": [
                "intervention",
                "outcome",
                "objective_mode",
                "modality",
            ],
            "required_diagnostics": [
                "overlap_floor",
                "positivity_alerts",
                "sample_size",
                "confidence_interval",
            ],
            "fallback_behavior": "emit_caveat_and_keep_advisory",
        },
        "aggregation_policy": {
            "default": "weighted_mean_with_shrinkage",
            "weights": ["sample_size", "confidence"],
            "report_global_and_segmented": True,
            "simpson_paradox_check_required": True,
        },
        "sample_size_policy": {
            "min_samples_per_stratum": 12,
            "min_unique_users_per_stratum": 6,
            "small_sample_strategy": "hierarchical_shrinkage",
            "fallback_order": ["stratum", "cohort", "global"],
            "must_emit_small_n_caveat": True,
        },
        "calibration_policy": {
            "required_metrics": ["brier_score", "ece", "coverage_ci95"],
            "drift_guard_required": True,
            "drift_guard_axes": ["objective_mode", "modality"],
        },
        "rollout_policy": {
            "grouped_gates_required": True,
            "block_on_critical_segment_regression": True,
        },
    }

