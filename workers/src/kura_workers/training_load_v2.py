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

_EXERCISE_MODALITY_OVERRIDES: dict[str, str] = {
    "sprint": "sprint",
    "sprint_interval": "sprint",
    "max_velocity_sprint": "sprint",
    "broad_jump_triple": "plyometric",
    "triple_broad_jump": "plyometric",
    "approach_vertical_jump": "plyometric",
    "countermovement_jump": "plyometric",
    "box_jump": "plyometric",
    "jump_squat": "plyometric",
}

_EXERCISE_MODALITY_TOKEN_HINTS: dict[str, tuple[str, ...]] = {
    "sprint": ("sprint", "maxv", "accel", "decel"),
    "plyometric": ("jump", "plyo", "bound", "hop"),
}

_MODALITY_ASSIGNMENT_SOURCES = (
    "block_type",
    "exercise_override",
    "exercise_token_hint",
    "heuristic_contacts",
    "heuristic_distance_endurance",
    "heuristic_strength",
    "heuristic_mixed",
)


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
            "Load combines external dose and internal response; do not infer false precision from missing references.",
            "Relative-intensity references (% of personal reference) fallback to sensor/subjective anchors with higher uncertainty when stale/missing.",
        ],
        "analysis_tiers": ["log_valid", "analysis_basic", "analysis_advanced"],
        "confidence_bands": ["low", "medium", "high"],
        "dual_load_policy": {
            "external_dose_dimensions": [
                "volume_kg",
                "duration_seconds",
                "distance_meters",
                "contacts",
            ],
            "internal_response_resolver_order": [
                "relative_intensity",
                "power",
                "heart_rate",
                "pace",
                "rpe",
            ],
            "fallback_policy": (
                "stale/missing relative-intensity references trigger deterministic fallback "
                "to power/heart_rate/pace/rpe with uncertainty uplift"
            ),
        },
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


def _modality_from_exercise_metadata(data: dict[str, Any]) -> tuple[str | None, str | None]:
    text_fields = (
        "exercise_id",
        "exercise",
        "workout_type",
        "sport",
        "capability_target",
    )
    normalized_terms = [
        str(data.get(field) or "").strip().lower()
        for field in text_fields
        if str(data.get(field) or "").strip()
    ]
    if not normalized_terms:
        return None, None

    for term in normalized_terms:
        mapped = _EXERCISE_MODALITY_OVERRIDES.get(term)
        if mapped is not None:
            return mapped, "exercise_override"

    combined = " ".join(normalized_terms)
    for modality, hints in _EXERCISE_MODALITY_TOKEN_HINTS.items():
        if any(hint in combined for hint in hints):
            return modality, "exercise_token_hint"
    return None, None


def infer_row_modality_with_context(data: dict[str, Any]) -> dict[str, Any]:
    block_type = str(data.get("block_type") or "").strip().lower()
    if block_type in _BLOCK_MODALITY_MAP:
        return {
            "modality": _BLOCK_MODALITY_MAP[block_type],
            "assignment_source": "block_type",
            "unknown_distance_exercise_id": None,
        }

    exercise_modality, assignment_source = _modality_from_exercise_metadata(data)
    if exercise_modality is not None:
        return {
            "modality": exercise_modality,
            "assignment_source": assignment_source or "exercise_override",
            "unknown_distance_exercise_id": None,
        }

    contacts = _to_float(data.get("contacts"))
    distance_meters = _to_float(data.get("distance_meters"))
    duration_seconds = _to_float(data.get("duration_seconds"))
    weight_kg = _to_float(data.get("weight_kg", data.get("weight")))
    reps = _to_float(data.get("reps"))

    if contacts > 0:
        return {
            "modality": "plyometric",
            "assignment_source": "heuristic_contacts",
            "unknown_distance_exercise_id": None,
        }

    unknown_distance_exercise_id: str | None = None
    if distance_meters > 0 or duration_seconds > 0:
        exercise_id = str(data.get("exercise_id") or "").strip().lower()
        if exercise_id:
            unknown_distance_exercise_id = exercise_id
        return {
            "modality": "endurance",
            "assignment_source": "heuristic_distance_endurance",
            "unknown_distance_exercise_id": unknown_distance_exercise_id,
        }

    if weight_kg > 0 or reps > 0:
        return {
            "modality": "strength",
            "assignment_source": "heuristic_strength",
            "unknown_distance_exercise_id": None,
        }
    return {
        "modality": "mixed",
        "assignment_source": "heuristic_mixed",
        "unknown_distance_exercise_id": None,
    }


def infer_row_modality(data: dict[str, Any]) -> str:
    return str(infer_row_modality_with_context(data).get("modality") or "mixed")


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
                "rows_with_relative_intensity": 0,
                "rows_with_relative_intensity_fallback": 0,
            },
            "modality_assignment": {
                source: 0 for source in _MODALITY_ASSIGNMENT_SOURCES
            },
            "unknown_distance_exercise": {
                "rows": 0,
                "exercise_ids": {},
            },
            "relative_intensity": {
                "rows_used": 0,
                "rows_fallback": 0,
                "reference_types": {},
                "sources": {},
                "reference_confidence_sum": 0.0,
                "reference_confidence_count": 0,
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
) -> dict[str, Any]:
    components = compute_row_load_components_v2(data=data, profile=profile)
    return {
        "volume_kg": float(components.get("volume_kg", 0.0) or 0.0),
        "reps": float(components.get("reps", 0.0) or 0.0),
        "duration_seconds": float(components.get("duration_seconds", 0.0) or 0.0),
        "distance_meters": float(components.get("distance_meters", 0.0) or 0.0),
        "contacts": float(components.get("contacts", 0.0) or 0.0),
        "load_score": float(components.get("load_score", 0.0) or 0.0),
        "internal_response_source": str(
            components.get("internal_response_source") or "modality_prior"
        ),
        "uncertainty": float(components.get("uncertainty", 0.0) or 0.0),
        "relative_intensity_status": str(
            components.get("relative_intensity_status") or "not_present"
        ),
        "relative_intensity_source": components.get("relative_intensity_source"),
        "relative_intensity_reference_type": components.get(
            "relative_intensity_reference_type"
        ),
        "relative_intensity_reference_confidence": components.get(
            "relative_intensity_reference_confidence"
        ),
    }


def accumulate_row_load_v2(
    session_load: dict[str, Any],
    *,
    data: dict[str, Any],
    source_type: str,
    session_confidence_hint: float | None = None,
) -> None:
    modality_info = infer_row_modality_with_context(data)
    modality = str(modality_info.get("modality") or "mixed")
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

    assignment_source = str(modality_info.get("assignment_source") or "")
    if assignment_source in global_part["modality_assignment"]:
        global_part["modality_assignment"][assignment_source] += 1

    unknown_distance_exercise_id = modality_info.get("unknown_distance_exercise_id")
    if isinstance(unknown_distance_exercise_id, str) and unknown_distance_exercise_id:
        unknown_bucket = global_part["unknown_distance_exercise"]
        unknown_bucket["rows"] += 1
        exercise_ids = unknown_bucket.get("exercise_ids")
        if isinstance(exercise_ids, dict):
            exercise_ids[unknown_distance_exercise_id] = (
                int(exercise_ids.get(unknown_distance_exercise_id, 0) or 0) + 1
            )

    relative_status = str(components.get("relative_intensity_status") or "not_present")
    relative_bucket = global_part["relative_intensity"]
    if relative_status == "used":
        global_part["signal_density"]["rows_with_relative_intensity"] += 1
        relative_bucket["rows_used"] += 1
    elif relative_status.startswith("fallback_"):
        global_part["signal_density"]["rows_with_relative_intensity_fallback"] += 1
        relative_bucket["rows_fallback"] += 1

    relative_source = str(components.get("relative_intensity_source") or "").strip()
    if relative_source:
        sources = relative_bucket.get("sources")
        if isinstance(sources, dict):
            sources[relative_source] = int(sources.get(relative_source, 0) or 0) + 1
    reference_type = str(components.get("relative_intensity_reference_type") or "").strip()
    if reference_type:
        reference_types = relative_bucket.get("reference_types")
        if isinstance(reference_types, dict):
            reference_types[reference_type] = int(reference_types.get(reference_type, 0) or 0) + 1
    reference_conf = components.get("relative_intensity_reference_confidence")
    if isinstance(reference_conf, (int, float)):
        relative_bucket["reference_confidence_sum"] += float(reference_conf)
        relative_bucket["reference_confidence_count"] += 1


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
    unknown_bucket = global_part.get("unknown_distance_exercise")
    if isinstance(unknown_bucket, dict):
        unknown_bucket["rows"] = int(unknown_bucket.get("rows", 0) or 0)
        exercise_ids = unknown_bucket.get("exercise_ids")
        if isinstance(exercise_ids, dict):
            unknown_bucket["exercise_ids"] = dict(
                sorted(
                    (
                        str(exercise_id),
                        int(count or 0),
                    )
                    for exercise_id, count in exercise_ids.items()
                    if str(exercise_id).strip()
                )
            )
    relative_bucket = global_part.get("relative_intensity")
    if isinstance(relative_bucket, dict):
        count = int(relative_bucket.get("reference_confidence_count", 0) or 0)
        confidence_sum = float(relative_bucket.get("reference_confidence_sum", 0.0) or 0.0)
        relative_bucket["reference_confidence_avg"] = (
            round(confidence_sum / count, 3) if count > 0 else None
        )
        relative_bucket["rows_used"] = int(relative_bucket.get("rows_used", 0) or 0)
        relative_bucket["rows_fallback"] = int(relative_bucket.get("rows_fallback", 0) or 0)
        for field in ("sources", "reference_types"):
            value = relative_bucket.get(field)
            if isinstance(value, dict):
                relative_bucket[field] = dict(
                    sorted(
                        (str(key), int(count or 0))
                        for key, count in value.items()
                        if str(key).strip()
                    )
                )
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
        summary["global"]["signal_density"]["rows_with_relative_intensity"] += int(
            (global_bucket.get("signal_density") or {}).get("rows_with_relative_intensity", 0) or 0
        )
        summary["global"]["signal_density"]["rows_with_relative_intensity_fallback"] += int(
            (
                (global_bucket.get("signal_density") or {}).get(
                    "rows_with_relative_intensity_fallback",
                    0,
                )
                or 0
            )
        )

        target_assignment = summary["global"].get("modality_assignment")
        source_assignment = global_bucket.get("modality_assignment")
        if isinstance(target_assignment, dict) and isinstance(source_assignment, dict):
            for source in _MODALITY_ASSIGNMENT_SOURCES:
                target_assignment[source] = int(target_assignment.get(source, 0) or 0) + int(
                    source_assignment.get(source, 0) or 0
                )

        target_unknown = summary["global"].get("unknown_distance_exercise")
        source_unknown = global_bucket.get("unknown_distance_exercise")
        if isinstance(target_unknown, dict) and isinstance(source_unknown, dict):
            target_unknown["rows"] = int(target_unknown.get("rows", 0) or 0) + int(
                source_unknown.get("rows", 0) or 0
            )
            target_ids = target_unknown.get("exercise_ids")
            source_ids = source_unknown.get("exercise_ids")
            if isinstance(target_ids, dict) and isinstance(source_ids, dict):
                for exercise_id, count in source_ids.items():
                    key = str(exercise_id).strip()
                    if not key:
                        continue
                    target_ids[key] = int(target_ids.get(key, 0) or 0) + int(count or 0)

        target_relative = summary["global"].get("relative_intensity")
        source_relative = global_bucket.get("relative_intensity")
        if isinstance(target_relative, dict) and isinstance(source_relative, dict):
            target_relative["rows_used"] = int(target_relative.get("rows_used", 0) or 0) + int(
                source_relative.get("rows_used", 0) or 0
            )
            target_relative["rows_fallback"] = int(
                target_relative.get("rows_fallback", 0) or 0
            ) + int(source_relative.get("rows_fallback", 0) or 0)
            target_relative["reference_confidence_sum"] = float(
                target_relative.get("reference_confidence_sum", 0.0) or 0.0
            ) + float(source_relative.get("reference_confidence_sum", 0.0) or 0.0)
            target_relative["reference_confidence_count"] = int(
                target_relative.get("reference_confidence_count", 0) or 0
            ) + int(source_relative.get("reference_confidence_count", 0) or 0)
            for field in ("sources", "reference_types"):
                target_map = target_relative.get(field)
                source_map = source_relative.get(field)
                if not isinstance(target_map, dict) or not isinstance(source_map, dict):
                    continue
                for key, count in source_map.items():
                    normalized = str(key).strip()
                    if not normalized:
                        continue
                    target_map[normalized] = int(target_map.get(normalized, 0) or 0) + int(
                        count or 0
                    )

    finalize_session_load_v2(summary)
    summary["sessions_total"] = sessions_total
    summary["parameter_versions"] = parameter_versions
    return summary
