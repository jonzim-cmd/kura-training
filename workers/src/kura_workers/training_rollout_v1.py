"""Rollout guardrails and feature flags for unified training model changes."""

from __future__ import annotations

import os
from typing import Any

FEATURE_FLAG_TRAINING_LOAD_V2 = "KURA_FEATURE_TRAINING_LOAD_V2"
FEATURE_FLAG_TRAINING_LOAD_CALIBRATED = "KURA_FEATURE_TRAINING_LOAD_CALIBRATED"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def read_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    return default


def is_training_load_v2_enabled() -> bool:
    return read_flag(FEATURE_FLAG_TRAINING_LOAD_V2, default=True)


def is_training_load_calibrated_enabled() -> bool:
    return read_flag(FEATURE_FLAG_TRAINING_LOAD_CALIBRATED, default=True)


def confidence_band(value: float) -> str:
    if value >= 0.8:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"


def rollout_contract_v1() -> dict[str, Any]:
    return {
        "policy_version": "training_rollout.v1",
        "qa_matrix": {
            "strength_manual_only": {
                "description": "Legacy strength logging with set.logged and no wearable data.",
                "must_validate": [
                    "timeline_set_counts_unchanged",
                    "manual_strength_load_v2_confidence_non_zero",
                ],
            },
            "sprint_interval_manual": {
                "description": "Track interval logging (manual blocks, no mandatory HR).",
                "must_validate": [
                    "missing_anchor_prompts_block_specific",
                    "session_logged_sprint_blocks_expand_to_rows",
                ],
            },
            "endurance_sensor_rich": {
                "description": "Imported sessions with HR/power/pace where available.",
                "must_validate": [
                    "sensor_fields_raise_confidence_without_schema_change",
                    "external_provenance_preserved",
                ],
            },
            "hybrid_strength_endurance": {
                "description": "Athletes with both set.logged and session.logged events.",
                "must_validate": [
                    "coexistence_without_double_counting",
                    "global_load_rollup_uses_all_modalities",
                ],
            },
            "low_data_user": {
                "description": "Sparse users with minimal metrics and partial sessions.",
                "must_validate": [
                    "analysis_remains_log_valid",
                    "confidence_degrades_instead_of_invalidating",
                ],
            },
        },
        "feature_flags": {
            "training_load_v2": {
                "env_var": FEATURE_FLAG_TRAINING_LOAD_V2,
                "default": True,
                "rollback_behavior": (
                    "Disable load_v2 fields in training_timeline projection while keeping base timeline intact."
                ),
            },
            "training_load_calibrated": {
                "env_var": FEATURE_FLAG_TRAINING_LOAD_CALIBRATED,
                "default": True,
                "rollback_behavior": (
                    "Disable calibrated parameter profile and fall back to baseline_v1 without schema changes."
                ),
            },
        },
        "shadow_mode": {
            "comparison_window_days": 14,
            "required_checks": [
                "legacy_vs_v2_total_set_delta_within_tolerance",
                "legacy_vs_v2_session_count_delta_within_tolerance",
                "quality_health_parse_and_anchor_metrics_stable",
            ],
        },
        "monitoring": {
            "metrics": [
                "external_import_parse_fail_rate_pct",
                "session_missing_anchor_rate_pct",
                "session_confidence_distribution",
            ],
            "alerts": {
                "external_import_parse_fail_rate_pct_warn": 2.0,
                "external_import_parse_fail_rate_pct_critical": 5.0,
                "session_missing_anchor_rate_pct_warn": 5.0,
                "session_missing_anchor_rate_pct_critical": 12.5,
            },
        },
        "hardening_gate": {
            "schema_version": "training_hardening_gate.v1",
            "required_before_ramp_up": True,
            "depends_on_issues": [
                "kura-training-316.10",
                "kura-training-316.11",
                "kura-training-316.12",
                "kura-training-316.13",
                "kura-training-316.14",
                "kura-training-316.15",
            ],
        },
    }
