"""Completeness tiers for session.logged block-based training sessions."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .training_session_contract import MeasurementValueV1, SessionBlockV1, validate_session_logged_payload

_SUBJECTIVE_UNITS = {"rpe", "lifting_rpe", "borg_cr10", "borg_6_20"}
_OBSERVED_STATES = {"measured", "estimated", "inferred"}


def _is_observed(measurement: MeasurementValueV1) -> bool:
    return measurement.measurement_state in _OBSERVED_STATES


def _evaluate_block(block: SessionBlockV1) -> dict[str, Any]:
    observed_anchors = [anchor for anchor in block.intensity_anchors if _is_observed(anchor)]
    explicit_anchor_waiver = block.intensity_anchors_status == "not_applicable"

    objective_anchor_count = 0
    for anchor in observed_anchors:
        unit = (anchor.unit or "").strip().lower()
        if unit and unit not in _SUBJECTIVE_UNITS:
            objective_anchor_count += 1

    observed_metrics = sum(1 for metric in block.metrics.values() if _is_observed(metric))

    analysis_basic = bool(observed_anchors) or explicit_anchor_waiver

    # Advanced analysis needs richer signal density than minimal logging.
    analysis_advanced = (
        analysis_basic
        and not explicit_anchor_waiver
        and (
            objective_anchor_count >= 1
            and (len(observed_anchors) >= 2 or observed_metrics >= 1)
        )
    )

    if block.block_type == "recovery_session":
        analysis_basic = True
        analysis_advanced = observed_metrics >= 1

    confidence = 0.35
    if analysis_basic:
        confidence = 0.65
    if analysis_advanced:
        confidence = 0.9
    if explicit_anchor_waiver and not observed_anchors:
        confidence = min(confidence, 0.55)

    return {
        "block_type": block.block_type,
        "analysis_basic": analysis_basic,
        "analysis_advanced": analysis_advanced,
        "observed_anchor_count": len(observed_anchors),
        "objective_anchor_count": objective_anchor_count,
        "observed_metric_count": observed_metrics,
        "explicit_anchor_waiver": explicit_anchor_waiver,
        "confidence": round(confidence, 2),
    }


def evaluate_session_completeness(payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate log_valid / analysis_basic / analysis_advanced completeness tiers.

    This function intentionally does not require heart-rate data globally.
    """
    try:
        session = validate_session_logged_payload(payload)
    except ValidationError as exc:
        return {
            "log_valid": False,
            "analysis_basic": False,
            "analysis_advanced": False,
            "tier": "invalid",
            "confidence": 0.0,
            "errors": [error.get("msg", "validation_error") for error in exc.errors()],
            "blocks": [],
        }

    block_results = [_evaluate_block(block) for block in session.blocks]

    analysis_basic = all(result["analysis_basic"] for result in block_results)
    analysis_advanced = analysis_basic and all(
        result["analysis_advanced"] for result in block_results
    )

    if analysis_advanced:
        tier = "analysis_advanced"
    elif analysis_basic:
        tier = "analysis_basic"
    else:
        tier = "log_valid"

    avg_confidence = sum(result["confidence"] for result in block_results) / len(block_results)

    return {
        "log_valid": True,
        "analysis_basic": analysis_basic,
        "analysis_advanced": analysis_advanced,
        "tier": tier,
        "confidence": round(avg_confidence, 2),
        "errors": [],
        "blocks": block_results,
    }


def completeness_policy_v1() -> dict[str, Any]:
    return {
        "levels": {
            "log_valid": "Schema-valid and reconstructable session blocks",
            "analysis_basic": "Block-level dose and at least one usable intensity signal per block (or explicit not_applicable)",
            "analysis_advanced": "Richer objective signal density for higher-confidence analytics",
        },
        "global_requirements": {
            "heart_rate_required": False,
            "power_required": False,
            "gps_required": False,
        },
    }
