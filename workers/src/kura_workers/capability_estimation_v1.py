"""Cross-capability estimation contract for strength/sprint/jump/endurance."""

from __future__ import annotations

from typing import Any


def capability_estimation_contract_v1() -> dict[str, Any]:
    """Return the architecture contract for capability_estimation.v1.

    The contract defines a shared estimation pattern across all performance
    capabilities so uncertainty, comparability, and data sufficiency are
    handled consistently for agent-only backend consumers.
    """
    return {
        "schema_version": "capability_estimation.v1",
        "rules": [
            "Use one shared estimation architecture across capabilities to avoid metric-specific drift.",
            "Separate observation model from latent state model; never collapse both into one proxy score.",
            "Output must always include uncertainty and data sufficiency, not just a point estimate.",
            "Protocol/equipment context defines comparability boundaries and must be explicit.",
            "When data is insufficient, emit machine-readable insufficiency diagnostics instead of guessing.",
        ],
        "pipeline": {
            "observation_model": {
                "required_outputs": [
                    "normalized_observation",
                    "observation_variance",
                    "protocol_signature",
                    "comparability_group",
                ],
                "goal": (
                    "Convert raw events into standardized observations with explicit measurement "
                    "error and comparability metadata."
                ),
            },
            "state_model": {
                "required_outputs": [
                    "latent_state_mean",
                    "latent_state_interval",
                    "state_velocity",
                    "state_diagnostics",
                ],
                "goal": (
                    "Estimate latent capability over time via probabilistic dynamics "
                    "(online approximation + optional heavy offline refit)."
                ),
            },
            "output_contract": {
                "agent_surface": "machine_readable_only",
                "required_fields": [
                    "estimate.mean",
                    "estimate.interval",
                    "status",
                    "confidence",
                    "data_sufficiency",
                    "caveats",
                    "model_version",
                ],
                "goal": "Support agent decisions without UI-specific assumptions.",
            },
        },
        "minimum_data_policy": {
            "status_values": ["ok", "insufficient_data", "degraded_comparability"],
            "required_output_fields_when_insufficient": [
                "status",
                "required_observations",
                "observed_observations",
                "uncertainty_reason_codes",
                "recommended_next_observations",
            ],
        },
        "capability_registry": {
            "strength_1rm": {
                "observation_fields": [
                    "weight_kg",
                    "reps",
                    "rpe",
                    "rir",
                    "set_type",
                    "tempo",
                    "rest_seconds",
                ],
                "protocol_required": [
                    "load_context.implements_type",
                    "load_context.equipment_profile",
                    "load_context.comparability_group",
                ],
                "estimator_tiers": ["baseline_proxy", "effort_adjusted", "latent_state"],
            },
            "sprint_max_speed": {
                "observation_fields": [
                    "distance_meters",
                    "duration_seconds",
                    "split_times",
                    "surface",
                    "timing_method",
                ],
                "protocol_required": [
                    "surface",
                    "timing_method",
                    "wind_state",
                ],
                "estimator_tiers": ["speed_proxy", "split_profile", "latent_state"],
            },
            "jump_height": {
                "observation_fields": [
                    "jump_height_cm",
                    "contact_time_ms",
                    "device_type",
                    "attempt_index",
                ],
                "protocol_required": [
                    "device_type",
                    "attempt_protocol",
                    "surface",
                ],
                "estimator_tiers": ["trial_best", "trial_distribution", "latent_state"],
            },
            "endurance_threshold": {
                "observation_fields": [
                    "distance_meters",
                    "duration_seconds",
                    "power_watt",
                    "heart_rate_avg",
                    "relative_intensity",
                ],
                "protocol_required": [
                    "reference_type",
                    "reference_measured_at",
                    "reference_confidence",
                ],
                "estimator_tiers": [
                    "single_session_proxy",
                    "threshold_fit",
                    "latent_state",
                ],
            },
        },
        "migration_order": [
            {
                "id": "phase_1_foundation",
                "goal": "shared observation/state/output contract and insufficiency protocol",
                "depends_on": [],
            },
            {
                "id": "phase_2_strength",
                "goal": "effort-adjusted strength estimation and uncertainty surfaces",
                "depends_on": ["phase_1_foundation"],
            },
            {
                "id": "phase_3_sprint",
                "goal": "split-aware sprint capability model with protocol comparability",
                "depends_on": ["phase_2_strength"],
            },
            {
                "id": "phase_4_jump",
                "goal": "trial-distribution jump estimation with device/surface correction",
                "depends_on": ["phase_3_sprint"],
            },
            {
                "id": "phase_5_endurance",
                "goal": "threshold-based endurance estimation with reference freshness handling",
                "depends_on": ["phase_4_jump"],
            },
            {
                "id": "phase_6_cross_capability_eval",
                "goal": "shared calibration gates and rollout checks across all capabilities",
                "depends_on": ["phase_5_endurance"],
            },
        ],
    }

