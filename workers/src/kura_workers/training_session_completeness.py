"""Completeness tiers for session.logged block-based training sessions."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .training_session_contract import (
    ERROR_TYPE_DOSE_WORK_DIMENSION_MISSING,
    ERROR_TYPE_INTENSITY_STATUS_NOT_APPLICABLE_WITH_ANCHOR,
    ERROR_TYPE_INTENSITY_STATUS_PROVIDED_WITHOUT_ANCHOR,
    ERROR_TYPE_INVALID_MEASUREMENT_STATE,
    ERROR_TYPE_MEASUREMENT_VALUE_OR_REFERENCE_REQUIRED,
    ERROR_TYPE_PERFORMANCE_BLOCK_MISSING_ANCHOR,
    ERROR_TYPE_SESSION_META_TEMPORAL_ORDER_INVALID,
    MeasurementValueV1,
    SessionBlockV1,
    validate_session_logged_payload,
)

_SUBJECTIVE_UNITS = {"rpe", "lifting_rpe", "borg_cr10", "borg_6_20"}
_OBSERVED_STATES = {"measured", "estimated", "inferred"}

ERROR_SCHEMA_VERSION = "session.completeness.errors.v1"
ERROR_CODE_VALIDATION_GENERIC = "session.logged.validation.invalid"
ERROR_CODE_REQUIRED_FIELD_MISSING = "session.logged.field.required_missing"
ERROR_CODE_MEASUREMENT_STATE_MISSING = "session.logged.measurement_state.missing"
ERROR_CODE_INVALID_MEASUREMENT_STATE = "session.logged.measurement_state.invalid"
ERROR_CODE_MEASUREMENT_VALUE_REQUIRED = "session.logged.measurement_state.value_or_reference_missing"
ERROR_CODE_DOSE_WORK_DIMENSION_MISSING = "session.logged.dose.work_dimension_missing"
ERROR_CODE_INTENSITY_STATUS_CONFLICT = "session.logged.anchor.status_conflict"
ERROR_CODE_MISSING_INTENSITY_ANCHOR = "session.logged.anchor.missing"
ERROR_CODE_SESSION_META_TEMPORAL_ORDER = "session.logged.session_meta.temporal_order_invalid"

MISSING_ANCHOR_ERROR_CODES: set[str] = {
    ERROR_CODE_MISSING_INTENSITY_ANCHOR,
    ERROR_CODE_INTENSITY_STATUS_CONFLICT,
}

_ERROR_TYPE_TO_CODE: dict[str, str] = {
    ERROR_TYPE_INVALID_MEASUREMENT_STATE: ERROR_CODE_INVALID_MEASUREMENT_STATE,
    ERROR_TYPE_MEASUREMENT_VALUE_OR_REFERENCE_REQUIRED: ERROR_CODE_MEASUREMENT_VALUE_REQUIRED,
    ERROR_TYPE_DOSE_WORK_DIMENSION_MISSING: ERROR_CODE_DOSE_WORK_DIMENSION_MISSING,
    ERROR_TYPE_INTENSITY_STATUS_PROVIDED_WITHOUT_ANCHOR: ERROR_CODE_INTENSITY_STATUS_CONFLICT,
    ERROR_TYPE_INTENSITY_STATUS_NOT_APPLICABLE_WITH_ANCHOR: ERROR_CODE_INTENSITY_STATUS_CONFLICT,
    ERROR_TYPE_PERFORMANCE_BLOCK_MISSING_ANCHOR: ERROR_CODE_MISSING_INTENSITY_ANCHOR,
    ERROR_TYPE_SESSION_META_TEMPORAL_ORDER_INVALID: ERROR_CODE_SESSION_META_TEMPORAL_ORDER,
}


def _is_observed(measurement: MeasurementValueV1) -> bool:
    return measurement.measurement_state in _OBSERVED_STATES


def _field_path_from_loc(loc: tuple[Any, ...]) -> str | None:
    parts: list[str] = []
    for entry in loc:
        if entry in {"__root__", "__object__"}:
            continue
        if isinstance(entry, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{entry}]"
            else:
                parts.append(f"[{entry}]")
            continue
        parts.append(str(entry))
    if not parts:
        return None
    return ".".join(parts)


def _block_scope_from_loc(
    loc: tuple[Any, ...],
    *,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    block_index: int | None = None
    for index, entry in enumerate(loc):
        if entry == "blocks" and index + 1 < len(loc) and isinstance(loc[index + 1], int):
            block_index = int(loc[index + 1])
            break
    if block_index is None:
        return None

    block_type: str | None = None
    blocks = payload.get("blocks")
    if isinstance(blocks, list) and 0 <= block_index < len(blocks):
        block = blocks[block_index]
        if isinstance(block, dict):
            raw_type = block.get("block_type")
            if isinstance(raw_type, str) and raw_type.strip():
                block_type = raw_type.strip().lower()

    return {
        "block_index": block_index,
        "block_type": block_type,
    }


def _error_code_for_validation_error(
    *,
    error_type: str,
    field_path: str | None,
) -> str:
    if error_type in _ERROR_TYPE_TO_CODE:
        return _ERROR_TYPE_TO_CODE[error_type]
    if error_type == "missing":
        if field_path and field_path.endswith("measurement_state"):
            return ERROR_CODE_MEASUREMENT_STATE_MISSING
        return ERROR_CODE_REQUIRED_FIELD_MISSING
    return ERROR_CODE_VALIDATION_GENERIC


def _normalize_validation_errors(
    exc: ValidationError,
    *,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for error in exc.errors():
        error_type = str(error.get("type") or "validation_error")
        loc = tuple(error.get("loc") or ())
        field_path = _field_path_from_loc(loc)
        normalized.append(
            {
                "schema_version": ERROR_SCHEMA_VERSION,
                "error_code": _error_code_for_validation_error(
                    error_type=error_type,
                    field_path=field_path,
                ),
                "error_type": error_type,
                "message": str(error.get("msg") or "validation_error"),
                "field_path": field_path,
                "block_scope": _block_scope_from_loc(loc, payload=payload),
            }
        )
    return normalized


def _error_summary(error_details: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for detail in error_details:
        code = str(detail.get("error_code") or ERROR_CODE_VALIDATION_GENERIC)
        summary[code] = summary.get(code, 0) + 1
    return summary


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
        error_details = _normalize_validation_errors(exc, payload=payload)
        return {
            "log_valid": False,
            "analysis_basic": False,
            "analysis_advanced": False,
            "tier": "invalid",
            "confidence": 0.0,
            "errors": [detail["message"] for detail in error_details],
            "error_schema_version": ERROR_SCHEMA_VERSION,
            "error_details": error_details,
            "error_summary": _error_summary(error_details),
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
        "error_schema_version": ERROR_SCHEMA_VERSION,
        "error_details": [],
        "error_summary": {},
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
        "error_contract": {
            "schema_version": ERROR_SCHEMA_VERSION,
            "fields": [
                "error_code",
                "error_type",
                "message",
                "field_path",
                "block_scope.block_index",
                "block_scope.block_type",
            ],
            "codes": [
                ERROR_CODE_VALIDATION_GENERIC,
                ERROR_CODE_REQUIRED_FIELD_MISSING,
                ERROR_CODE_MEASUREMENT_STATE_MISSING,
                ERROR_CODE_INVALID_MEASUREMENT_STATE,
                ERROR_CODE_MEASUREMENT_VALUE_REQUIRED,
                ERROR_CODE_DOSE_WORK_DIMENSION_MISSING,
                ERROR_CODE_INTENSITY_STATUS_CONFLICT,
                ERROR_CODE_MISSING_INTENSITY_ANCHOR,
                ERROR_CODE_SESSION_META_TEMPORAL_ORDER,
            ],
            "missing_anchor_codes": sorted(MISSING_ANCHOR_ERROR_CODES),
        },
    }
