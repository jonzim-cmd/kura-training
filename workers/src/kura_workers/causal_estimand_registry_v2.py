"""Causal estimand registry v2 (objective/modality aware)."""

from __future__ import annotations

from typing import Any

CAUSAL_ESTIMAND_REGISTRY_SCHEMA_VERSION = "causal_estimand_registry.v2"

_DEFAULT_DIAGNOSTICS = [
    "sample_size",
    "effective_sample_size",
    "overlap_floor",
    "positivity_alerts",
    "confidence_interval",
]

_DEFAULT_CONFOUNDERS = [
    "baseline_readiness",
    "baseline_sleep_hours",
    "baseline_load_volume",
    "baseline_protein_g",
    "current_readiness",
    "current_sleep_hours",
    "current_load_volume",
    "current_protein_g",
    "current_calories",
]

_STRENGTH_CONFOUNDERS = _DEFAULT_CONFOUNDERS + [
    "baseline_strength_aggregate",
    "current_strength_aggregate",
]

_INTERVENTION_REGISTRY: dict[str, dict[str, dict[str, Any]]] = {
    "program_change": {
        "readiness_score_t_plus_1": {
            "estimand_type": "average_treatment_effect",
            "confounders": list(_DEFAULT_CONFOUNDERS),
            "notes": "Plan/program transitions may proxy deload, taper, or overload shifts.",
        },
        "strength_aggregate_delta_t_plus_1": {
            "estimand_type": "average_treatment_effect",
            "confounders": list(_STRENGTH_CONFOUNDERS),
            "notes": "Strength aggregate is sensitive to exercise mix and comparability context.",
        },
        "strength_delta_by_exercise_t_plus_1": {
            "estimand_type": "average_treatment_effect",
            "confounders": list(_STRENGTH_CONFOUNDERS),
            "notes": "Per-exercise estimands require exercise-level overlap checks.",
        },
    },
    "nutrition_shift": {
        "readiness_score_t_plus_1": {
            "estimand_type": "average_treatment_effect",
            "confounders": list(_DEFAULT_CONFOUNDERS),
            "notes": "Nutrition shifts may have delayed response; interpret short horizons cautiously.",
        },
        "strength_aggregate_delta_t_plus_1": {
            "estimand_type": "average_treatment_effect",
            "confounders": list(_STRENGTH_CONFOUNDERS),
            "notes": "Protein/calorie changes can interact with load and recovery context.",
        },
        "strength_delta_by_exercise_t_plus_1": {
            "estimand_type": "average_treatment_effect",
            "confounders": list(_STRENGTH_CONFOUNDERS),
            "notes": "Exercise-level outcome requires sufficient repeated exposure.",
        },
    },
    "sleep_intervention": {
        "readiness_score_t_plus_1": {
            "estimand_type": "average_treatment_effect",
            "confounders": list(_DEFAULT_CONFOUNDERS),
            "notes": "Sleep interventions are vulnerable to residual confounding from fatigue cycles.",
        },
        "strength_aggregate_delta_t_plus_1": {
            "estimand_type": "average_treatment_effect",
            "confounders": list(_STRENGTH_CONFOUNDERS),
            "notes": "Strength response to sleep may lag; watch interval width and overlap.",
        },
        "strength_delta_by_exercise_t_plus_1": {
            "estimand_type": "average_treatment_effect",
            "confounders": list(_STRENGTH_CONFOUNDERS),
            "notes": "Exercise-level sleep response can be sparse for low-frequency lifts.",
        },
    },
}


def causal_estimand_registry_v2() -> dict[str, Any]:
    """Return canonical intervention x outcome estimand registry."""
    return {
        "schema_version": CAUSAL_ESTIMAND_REGISTRY_SCHEMA_VERSION,
        "policy_role": "advisory_only",
        "identity_dimensions": [
            "intervention",
            "outcome",
            "objective_mode",
            "modality",
            "exercise_id",
        ],
        "required_diagnostics": list(_DEFAULT_DIAGNOSTICS),
        "interventions": _INTERVENTION_REGISTRY,
        "fallback_policy": {
            "unknown_intervention_or_outcome": "use_default_estimand_template",
            "unknown_context_dimensions": "set_to_unknown_and_emit_caveat",
        },
    }


def build_estimand_identity_v2(
    *,
    intervention: str,
    outcome: str,
    objective_mode: str,
    modality: str,
    exercise_id: str | None = None,
) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "intervention": str(intervention or "unknown").strip().lower() or "unknown",
        "outcome": str(outcome or "unknown").strip().lower() or "unknown",
        "objective_mode": str(objective_mode or "unknown").strip().lower() or "unknown",
        "modality": str(modality or "unknown").strip().lower() or "unknown",
    }
    if exercise_id:
        identity["exercise_id"] = str(exercise_id).strip().lower()
    return identity


def resolve_estimand_spec_v2(intervention: str, outcome: str) -> dict[str, Any]:
    """Resolve estimand metadata for intervention/outcome with stable fallback."""
    normalized_intervention = str(intervention or "").strip().lower()
    normalized_outcome = str(outcome or "").strip().lower()

    by_outcome = _INTERVENTION_REGISTRY.get(normalized_intervention) or {}
    resolved = by_outcome.get(normalized_outcome)
    if not isinstance(resolved, dict):
        resolved = {
            "estimand_type": "average_treatment_effect",
            "confounders": list(_DEFAULT_CONFOUNDERS),
            "notes": "Fallback estimand spec applied for unknown intervention/outcome.",
        }

    return {
        "estimand_type": str(resolved.get("estimand_type") or "average_treatment_effect"),
        "confounders": [str(item) for item in (resolved.get("confounders") or []) if str(item)],
        "required_diagnostics": list(_DEFAULT_DIAGNOSTICS),
        "notes": str(resolved.get("notes") or ""),
    }
