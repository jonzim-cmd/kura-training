"""Modality-specific and global training load aggregation (Projection v2)."""

from __future__ import annotations

from typing import Any

from .training_load_calibration_v1 import (
    CALIBRATION_VERSION_ENV,
    FEATURE_FLAG_TRAINING_LOAD_CALIBRATED,
    active_calibration_version,
    calibration_profile_for_version,
    calibration_protocol_v1,
    compute_row_confidence_v1,
    compute_row_load_components_v2,
)

_ROW_MODALITIES = ("strength", "sprint", "endurance", "plyometric", "mixed")

_BLOCK_MODALITY_MAP: dict[str, str] = {
    "strength_set": "strength",
    "explosive_power": "strength",
    "circuit_hybrid": "mixed",
    "sprint_accel_maxv": "sprint",
    "speed_endurance": "sprint",
    "interval_endurance": "endurance",
    "continuous_endurance": "endurance",
    "tempo_threshold": "endurance",
    "plyometric_reactive": "plyometric",
    "technique_coordination": "mixed",
    "recovery_session": "mixed",
}


def load_projection_contract_v2() -> dict[str, Any]:
    calibration_contract = calibration_protocol_v1()
    return {
        "schema_version": "training_load.v2",
        "modalities": list(_ROW_MODALITIES),
        "rules": [
            "Manual-only logging must remain analyzable (no global HR requirement).",
            "Missing sensors reduce confidence but do not invalidate sessions.",
            "Additional sensors increase confidence without schema changes.",
            "Global load is aggregated from modality-specific load buckets.",
            "Calibration parameter profiles are versioned and shadow-gated before rollout.",
        ],
        "analysis_tiers": ["log_valid", "analysis_basic", "analysis_advanced"],
        "confidence_bands": ["low", "medium", "high"],
        "calibration": {
            "protocol_version": calibration_contract["schema_version"],
            "active_parameter_version": active_calibration_version(),
            "feature_flag": FEATURE_FLAG_TRAINING_LOAD_CALIBRATED,
            "version_env": CALIBRATION_VERSION_ENV,
            "available_parameter_versions": calibration_contract["parameter_registry"][
                "available_versions"
            ],
        },
    }


def _to_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed < 0:
        return 0.0
    return parsed


def _confidence_band(confidence: float) -> str:
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.6:
        return "medium"
    return "low"


def _analysis_tier(confidence: float) -> str:
    if confidence >= 0.8:
        return "analysis_advanced"
    if confidence >= 0.6:
        return "analysis_basic"
    return "log_valid"


def infer_row_modality(data: dict[str, Any]) -> str:
    block_type = str(data.get("block_type") or "").strip().lower()
    if block_type in _BLOCK_MODALITY_MAP:
        return _BLOCK_MODALITY_MAP[block_type]

    contacts = _to_float(data.get("contacts"))
    distance_meters = _to_float(data.get("distance_meters"))
    duration_seconds = _to_float(data.get("duration_seconds"))
    weight_kg = _to_float(data.get("weight_kg", data.get("weight")))
    reps = _to_float(data.get("reps"))

    if contacts > 0:
        return "plyometric"
    if distance_meters > 0 or duration_seconds > 0:
        return "endurance"
    if weight_kg > 0 or reps > 0:
        return "strength"
    return "mixed"


def _init_modality_bucket() -> dict[str, Any]:
    return {
        "rows": 0,
        "load_score": 0.0,
        "volume_kg": 0.0,
        "reps": 0,
        "duration_seconds": 0.0,
        "distance_meters": 0.0,
        "contacts": 0,
        "confidence_sum": 0.0,
    }


def init_session_load_v2(parameter_version: str | None = None) -> dict[str, Any]:
    resolved_parameter_version = parameter_version or active_calibration_version()
    return {
        "schema_version": "training_load.v2",
        "parameter_version": resolved_parameter_version,
        "modalities": {modality: _init_modality_bucket() for modality in _ROW_MODALITIES},
        "global": {
            "load_score": 0.0,
            "confidence": 0.0,
            "confidence_band": "low",
            "analysis_tier": "log_valid",
            "missing_sensor_policy": (
                "Missing sensor data lowers confidence but does not invalidate logging."
            ),
            "signal_density": {
                "rows_total": 0,
                "objective_rows": 0,
                "rows_with_hr": 0,
                "rows_with_power": 0,
                "rows_with_pace": 0,
            },
        },
    }


def _row_confidence(
    *,
    data: dict[str, Any],
    source_type: str,
    session_confidence_hint: float | None,
    profile: dict[str, Any],
) -> float:
    return compute_row_confidence_v1(
        data=data,
        source_type=source_type,
        session_confidence_hint=session_confidence_hint,
        profile=profile,
    )


def _row_load_components(
    data: dict[str, Any],
    *,
    profile: dict[str, Any],
) -> dict[str, float]:
    components = compute_row_load_components_v2(data=data, profile=profile)
    return {
        "volume_kg": float(components.get("volume_kg", 0.0) or 0.0),
        "reps": float(components.get("reps", 0.0) or 0.0),
        "duration_seconds": float(components.get("duration_seconds", 0.0) or 0.0),
        "distance_meters": float(components.get("distance_meters", 0.0) or 0.0),
        "contacts": float(components.get("contacts", 0.0) or 0.0),
        "load_score": float(components.get("load_score", 0.0) or 0.0),
    }


def accumulate_row_load_v2(
    session_load: dict[str, Any],
    *,
    data: dict[str, Any],
    source_type: str,
    session_confidence_hint: float | None = None,
) -> None:
    modality = infer_row_modality(data)
    bucket = session_load["modalities"].setdefault(modality, _init_modality_bucket())
    parameter_version = str(
        session_load.get("parameter_version") or active_calibration_version()
    ).strip()
    profile = calibration_profile_for_version(parameter_version)
    components = _row_load_components(data, profile=profile)
    confidence = _row_confidence(
        data=data,
        source_type=source_type,
        session_confidence_hint=session_confidence_hint,
        profile=profile,
    )

    bucket["rows"] += 1
    bucket["load_score"] += components["load_score"]
    bucket["volume_kg"] += components["volume_kg"]
    bucket["reps"] += int(round(components["reps"]))
    bucket["duration_seconds"] += components["duration_seconds"]
    bucket["distance_meters"] += components["distance_meters"]
    bucket["contacts"] += int(round(components["contacts"]))
    bucket["confidence_sum"] += confidence

    global_part = session_load["global"]
    global_part["load_score"] += components["load_score"]
    global_part["signal_density"]["rows_total"] += 1
    if (
        components["volume_kg"] > 0
        or components["duration_seconds"] > 0
        or components["distance_meters"] > 0
        or components["contacts"] > 0
    ):
        global_part["signal_density"]["objective_rows"] += 1

    data_keys = {str(key).strip().lower() for key in data.keys()}
    if {"heart_rate_avg", "heart_rate_max", "hr_avg", "hr_bpm"} & data_keys:
        global_part["signal_density"]["rows_with_hr"] += 1
    if {"power", "power_watt", "watts"} & data_keys:
        global_part["signal_density"]["rows_with_power"] += 1
    if {"pace", "pace_min_per_km", "min_per_km"} & data_keys:
        global_part["signal_density"]["rows_with_pace"] += 1


def finalize_session_load_v2(session_load: dict[str, Any]) -> dict[str, Any]:
    rows_total = 0
    confidence_sum = 0.0
    for bucket in session_load["modalities"].values():
        rows = int(bucket.get("rows", 0) or 0)
        rows_total += rows
        bucket_confidence_sum = float(bucket.get("confidence_sum", 0.0) or 0.0)
        confidence_sum += bucket_confidence_sum
        bucket["load_score"] = round(float(bucket.get("load_score", 0.0) or 0.0), 2)
        bucket["volume_kg"] = round(float(bucket.get("volume_kg", 0.0) or 0.0), 1)
        bucket["duration_seconds"] = round(float(bucket.get("duration_seconds", 0.0) or 0.0), 1)
        bucket["distance_meters"] = round(float(bucket.get("distance_meters", 0.0) or 0.0), 1)
        if rows > 0:
            bucket["confidence"] = round(bucket_confidence_sum / rows, 2)
        bucket.pop("confidence_sum", None)

    confidence = round(confidence_sum / rows_total, 2) if rows_total else 0.0
    global_part = session_load["global"]
    global_part["load_score"] = round(float(global_part.get("load_score", 0.0) or 0.0), 2)
    global_part["confidence"] = confidence
    global_part["confidence_band"] = _confidence_band(confidence)
    global_part["analysis_tier"] = _analysis_tier(confidence)
    return session_load


def summarize_timeline_load_v2(session_data: dict[str, dict[str, Any]]) -> dict[str, Any]:
    summary = init_session_load_v2()
    sessions_total = 0
    parameter_versions: dict[str, int] = {}

    for session in session_data.values():
        load_v2 = session.get("load_v2")
        if not isinstance(load_v2, dict):
            continue
        sessions_total += 1
        parameter_version = str(
            load_v2.get("parameter_version") or active_calibration_version()
        ).strip()
        if parameter_version:
            parameter_versions[parameter_version] = (
                parameter_versions.get(parameter_version, 0) + 1
            )
        for modality, bucket in load_v2.get("modalities", {}).items():
            if modality not in summary["modalities"]:
                continue
            target = summary["modalities"][modality]
            rows = int(bucket.get("rows", 0) or 0)
            target["rows"] += rows
            target["load_score"] += float(bucket.get("load_score", 0.0) or 0.0)
            target["volume_kg"] += float(bucket.get("volume_kg", 0.0) or 0.0)
            target["reps"] += int(bucket.get("reps", 0) or 0)
            target["duration_seconds"] += float(bucket.get("duration_seconds", 0.0) or 0.0)
            target["distance_meters"] += float(bucket.get("distance_meters", 0.0) or 0.0)
            target["contacts"] += int(bucket.get("contacts", 0) or 0)
            # Session buckets may already be finalized (confidence, no confidence_sum).
            confidence_sum = bucket.get("confidence_sum")
            if confidence_sum is None:
                confidence_sum = float(bucket.get("confidence", 0.0) or 0.0) * rows
            target["confidence_sum"] += float(confidence_sum or 0.0)

        global_bucket = load_v2.get("global", {})
        summary["global"]["load_score"] += float(global_bucket.get("load_score", 0.0) or 0.0)
        summary["global"]["signal_density"]["rows_total"] += int(
            (global_bucket.get("signal_density") or {}).get("rows_total", 0) or 0
        )
        summary["global"]["signal_density"]["objective_rows"] += int(
            (global_bucket.get("signal_density") or {}).get("objective_rows", 0) or 0
        )
        summary["global"]["signal_density"]["rows_with_hr"] += int(
            (global_bucket.get("signal_density") or {}).get("rows_with_hr", 0) or 0
        )
        summary["global"]["signal_density"]["rows_with_power"] += int(
            (global_bucket.get("signal_density") or {}).get("rows_with_power", 0) or 0
        )
        summary["global"]["signal_density"]["rows_with_pace"] += int(
            (global_bucket.get("signal_density") or {}).get("rows_with_pace", 0) or 0
        )

    finalize_session_load_v2(summary)
    summary["sessions_total"] = sessions_total
    summary["parameter_versions"] = parameter_versions
    return summary
