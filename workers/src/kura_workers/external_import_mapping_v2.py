"""Import mapping v2 for external sources -> session.logged block model."""

from __future__ import annotations

from typing import Any, Literal

from .external_activity_contract import CanonicalExternalActivityV1
from .training_session_contract import (
    BLOCK_TYPES,
    CONTRACT_VERSION_V1,
    validate_session_logged_payload,
)

SupportState = Literal["supported", "partial", "not_available"]
Modality = Literal["running", "cycling", "strength", "hybrid"]

_CORE_IMPORT_FIELDS: tuple[str, ...] = (
    "session.started_at",
    "workout.workout_type",
    "dose.work",
    "provenance.source_type",
)

_MODALITY_PROFILES_V2: dict[Modality, dict[str, Any]] = {
    "running": {
        "core_fields": [
            "session.started_at",
            "workout.duration_seconds|workout.distance_meters",
            "dose.work.duration_seconds|dose.work.distance_meters",
        ],
        "optional_fields": [
            "intensity.pace",
            "metrics.heart_rate_avg",
            "metrics.power_watt",
        ],
        "provider_specific_optional": [
            "garmin.running_dynamics",
            "strava.suffer_score",
            "trainingpeaks.tss",
        ],
        "default_block_types": ["continuous_endurance", "interval_endurance", "tempo_threshold"],
    },
    "cycling": {
        "core_fields": [
            "session.started_at",
            "workout.duration_seconds|workout.distance_meters",
            "dose.work.duration_seconds|dose.work.distance_meters",
        ],
        "optional_fields": [
            "metrics.power_watt",
            "metrics.heart_rate_avg",
            "intensity.pace|intensity.speed",
        ],
        "provider_specific_optional": [
            "garmin.normalized_power",
            "strava.weighted_average_watts",
            "trainingpeaks.if",
        ],
        "default_block_types": ["continuous_endurance", "interval_endurance", "tempo_threshold"],
    },
    "strength": {
        "core_fields": [
            "session.started_at",
            "dose.work.reps",
            "metrics.weight_kg",
        ],
        "optional_fields": [
            "intensity.rpe_borg",
            "dose.recovery.duration_seconds",
            "metrics.velocity",
        ],
        "provider_specific_optional": [
            "garmin.rep_power",
            "trainingpeaks.strength_specific_fields",
        ],
        "default_block_types": ["strength_set", "explosive_power"],
    },
    "hybrid": {
        "core_fields": [
            "session.started_at",
            "dose.work",
            "provenance.source_type",
        ],
        "optional_fields": [
            "intensity.pace",
            "intensity.rpe_borg",
            "metrics.heart_rate_avg",
            "metrics.power_watt",
        ],
        "provider_specific_optional": [
            "multisport.segment_specific_metrics",
        ],
        "default_block_types": ["circuit_hybrid", "interval_endurance", "strength_set"],
    },
}

_PROVIDER_FIELD_MATRIX_V2: dict[str, dict[str, SupportState]] = {
    "garmin": {
        "session.started_at": "supported",
        "session.timezone": "supported",
        "workout.duration_seconds": "supported",
        "workout.distance_meters": "supported",
        "metrics.heart_rate_avg": "partial",
        "metrics.power_watt": "partial",
        "intensity.pace": "partial",
        "intensity.rpe_borg": "not_available",
    },
    "strava": {
        "session.started_at": "supported",
        "session.timezone": "partial",
        "workout.duration_seconds": "supported",
        "workout.distance_meters": "supported",
        "metrics.heart_rate_avg": "partial",
        "metrics.power_watt": "partial",
        "intensity.pace": "partial",
        "intensity.rpe_borg": "not_available",
    },
    "trainingpeaks": {
        "session.started_at": "supported",
        "session.timezone": "supported",
        "workout.duration_seconds": "supported",
        "workout.distance_meters": "supported",
        "metrics.heart_rate_avg": "partial",
        "metrics.power_watt": "partial",
        "intensity.pace": "partial",
        "intensity.rpe_borg": "partial",
    },
}

_FORMAT_FIELD_MATRIX_V2: dict[str, dict[str, SupportState]] = {
    "fit": {
        "session.started_at": "supported",
        "workout.duration_seconds": "supported",
        "workout.distance_meters": "supported",
        "metrics.heart_rate_avg": "partial",
        "metrics.power_watt": "partial",
        "intensity.pace": "partial",
        "intensity.rpe_borg": "not_available",
    },
    "tcx": {
        "session.started_at": "supported",
        "workout.duration_seconds": "supported",
        "workout.distance_meters": "supported",
        "metrics.heart_rate_avg": "partial",
        "metrics.power_watt": "partial",
        "intensity.pace": "partial",
        "intensity.rpe_borg": "not_available",
    },
    "gpx": {
        "session.started_at": "supported",
        "workout.duration_seconds": "supported",
        "workout.distance_meters": "partial",
        "metrics.heart_rate_avg": "not_available",
        "metrics.power_watt": "not_available",
        "intensity.pace": "partial",
        "intensity.rpe_borg": "not_available",
    },
}

_PROVIDER_MODALITY_MATRIX_V2: dict[str, dict[Modality, SupportState]] = {
    "garmin": {
        "running": "supported",
        "cycling": "supported",
        "strength": "partial",
        "hybrid": "partial",
    },
    "strava": {
        "running": "supported",
        "cycling": "supported",
        "strength": "not_available",
        "hybrid": "partial",
    },
    "trainingpeaks": {
        "running": "supported",
        "cycling": "supported",
        "strength": "partial",
        "hybrid": "supported",
    },
}

_FORMAT_MODALITY_MATRIX_V2: dict[str, dict[Modality, SupportState]] = {
    "fit": {
        "running": "supported",
        "cycling": "supported",
        "strength": "partial",
        "hybrid": "partial",
    },
    "tcx": {
        "running": "supported",
        "cycling": "supported",
        "strength": "not_available",
        "hybrid": "partial",
    },
    "gpx": {
        "running": "supported",
        "cycling": "partial",
        "strength": "not_available",
        "hybrid": "not_available",
    },
}


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_sensor_metrics(modality: Modality) -> dict[str, dict[str, str]]:
    if modality == "strength":
        return {
            "heart_rate_avg": {"measurement_state": "not_measured"},
            "power_watt": {"measurement_state": "not_measured"},
        }
    return {
        "heart_rate_avg": {"measurement_state": "not_measured"},
        "power_watt": {"measurement_state": "not_measured"},
        "cadence": {"measurement_state": "not_measured"},
    }


def _infer_modality(
    *,
    workout_type: str,
    sport: str | None,
    sets_data: list[dict[str, Any]],
) -> Modality:
    text = f"{workout_type} {sport or ''}".strip().lower()
    if any(token in text for token in ("brick", "multisport", "triathlon", "hybrid")):
        return "hybrid"
    if any(token in text for token in ("bike", "ride", "cycling")):
        return "cycling"
    if any(token in text for token in ("strength", "gym", "weight", "lift")):
        return "strength"
    if any(token in text for token in ("run", "jog", "track")):
        return "running"

    has_strength = any(
        set_data.get("reps") is not None or set_data.get("weight_kg") is not None
        for set_data in sets_data
    )
    has_endurance = any(
        set_data.get("duration_seconds") is not None
        or set_data.get("distance_meters") is not None
        for set_data in sets_data
    )
    if has_strength and has_endurance:
        return "hybrid"
    if has_strength:
        return "strength"
    if has_endurance:
        return "running"
    return "running"


def _block_type_for_workout(modality: Modality, workout_type: str) -> str:
    normalized = workout_type.strip().lower()
    if any(token in normalized for token in ("sprint", "maxv", "accel")):
        return "sprint_accel_maxv"
    if any(token in normalized for token in ("tempo", "threshold")):
        return "tempo_threshold"
    if any(token in normalized for token in ("interval", "repeat", "fartlek")):
        return "interval_endurance"
    if modality == "strength":
        return "strength_set"
    if modality == "hybrid":
        return "circuit_hybrid"
    return "continuous_endurance"


def _dose_from_workout(
    *,
    duration_seconds: float | None,
    distance_meters: float | None,
) -> dict[str, Any]:
    work: dict[str, Any] = {}
    if duration_seconds is not None and duration_seconds >= 0:
        work["duration_seconds"] = duration_seconds
    if distance_meters is not None and distance_meters >= 0:
        work["distance_meters"] = distance_meters
    if not work:
        work["duration_seconds"] = 1.0
    return {"work": work, "repeats": 1}


def _pace_anchor(
    *,
    duration_seconds: float | None,
    distance_meters: float | None,
) -> dict[str, Any] | None:
    if duration_seconds is None or distance_meters is None or distance_meters <= 0:
        return None
    min_per_km = (duration_seconds / 60.0) / (distance_meters / 1000.0)
    if min_per_km <= 0:
        return None
    return {
        "measurement_state": "estimated",
        "unit": "min_per_km",
        "value": round(min_per_km, 3),
    }


def _set_slice_to_block(
    set_slice: Any,
    *,
    modality: Modality,
) -> dict[str, Any] | None:
    if not hasattr(set_slice, "model_dump"):
        return None
    set_data = set_slice.model_dump(mode="python")
    reps = set_data.get("reps")
    weight_kg = set_data.get("weight_kg")
    duration_seconds = set_data.get("duration_seconds")
    distance_meters = set_data.get("distance_meters")
    rest_seconds = set_data.get("rest_seconds")
    rpe = set_data.get("rpe")

    has_strength = reps is not None or weight_kg is not None
    has_endurance = duration_seconds is not None or distance_meters is not None
    set_modality: Modality = modality
    if has_strength and has_endurance:
        set_modality = "hybrid"
    elif has_strength:
        set_modality = "strength"
    elif has_endurance and modality == "strength":
        set_modality = "running"

    work: dict[str, Any] = {}
    if reps is not None:
        work["reps"] = int(reps)
    if duration_seconds is not None:
        work["duration_seconds"] = float(duration_seconds)
    if distance_meters is not None:
        work["distance_meters"] = float(distance_meters)
    if not work:
        work["reps"] = 1

    if set_modality == "strength":
        block_type = "strength_set"
    elif set_modality == "hybrid":
        block_type = "circuit_hybrid"
    elif rest_seconds is not None and rest_seconds > 0:
        block_type = "interval_endurance"
    else:
        block_type = "continuous_endurance"

    block: dict[str, Any] = {
        "block_type": block_type,
        "dose": {"work": work, "repeats": 1},
        "metrics": _default_sensor_metrics(set_modality),
        "provenance": {
            "source_type": "imported",
            "source_ref": "external_import_mapping.v2",
        },
    }
    if rest_seconds is not None:
        block["dose"]["recovery"] = {"duration_seconds": float(rest_seconds)}
    if weight_kg is not None:
        block["metrics"]["weight_kg"] = {
            "measurement_state": "measured",
            "unit": "kg",
            "value": float(weight_kg),
        }

    if rpe is not None:
        block["intensity_anchors"] = [
            {
                "measurement_state": "measured",
                "unit": "rpe",
                "value": float(rpe),
            }
        ]
    else:
        pace_anchor = _pace_anchor(
            duration_seconds=_to_float(duration_seconds),
            distance_meters=_to_float(distance_meters),
        )
        if pace_anchor is not None:
            block["intensity_anchors"] = [pace_anchor]
        else:
            block["intensity_anchors_status"] = "not_applicable"
    return block


def map_external_activity_to_session_logged_v2(
    canonical: CanonicalExternalActivityV1,
) -> dict[str, Any]:
    workout_type = canonical.workout.workout_type or "workout"
    duration_seconds = _to_float(canonical.workout.duration_seconds)
    distance_meters = _to_float(canonical.workout.distance_meters)
    set_rows = [set_slice.model_dump(mode="python") for set_slice in canonical.sets]
    modality = _infer_modality(
        workout_type=workout_type,
        sport=canonical.workout.sport,
        sets_data=set_rows,
    )

    blocks: list[dict[str, Any]] = []
    for set_slice in canonical.sets:
        mapped = _set_slice_to_block(set_slice, modality=modality)
        if mapped is not None:
            blocks.append(mapped)

    if not blocks:
        pace_anchor = _pace_anchor(
            duration_seconds=duration_seconds,
            distance_meters=distance_meters,
        )
        block: dict[str, Any] = {
            "block_type": _block_type_for_workout(modality, workout_type),
            "dose": _dose_from_workout(
                duration_seconds=duration_seconds,
                distance_meters=distance_meters,
            ),
            "metrics": _default_sensor_metrics(modality),
            "provenance": {
                "source_type": "imported",
                "source_ref": "external_import_mapping.v2",
            },
        }
        if pace_anchor is not None:
            block["intensity_anchors"] = [pace_anchor]
        else:
            block["intensity_anchors_status"] = "not_applicable"
        blocks.append(block)

    payload = {
        "contract_version": CONTRACT_VERSION_V1,
        "session_meta": {
            "sport": canonical.workout.sport or modality,
            "started_at": canonical.session.started_at.isoformat(),
            "ended_at": canonical.session.ended_at.isoformat()
            if canonical.session.ended_at is not None
            else None,
            "timezone": canonical.session.timezone,
            "session_id": (
                canonical.session.session_id
                or f"{canonical.source.provider}:{canonical.source.external_activity_id}"
            ),
        },
        "blocks": blocks,
        "provenance": {
            "source_type": "imported",
            "source_ref": (
                f"{canonical.source.provider}:{canonical.source.external_activity_id}"
            ),
            "confidence": canonical.provenance.source_confidence,
        },
    }
    validate_session_logged_payload(payload)
    return payload


def import_mapping_contract_v2() -> dict[str, Any]:
    return {
        "schema_version": "external_import_mapping.v2",
        "target_contract": CONTRACT_VERSION_V1,
        "supported_block_types": list(BLOCK_TYPES),
        "modalities": list(_MODALITY_PROFILES_V2.keys()),
        "modality_profiles": _MODALITY_PROFILES_V2,
        "required_core_fields": list(_CORE_IMPORT_FIELDS),
        "provider_field_matrix": _PROVIDER_FIELD_MATRIX_V2,
        "format_field_matrix": _FORMAT_FIELD_MATRIX_V2,
        "provider_modality_matrix": _PROVIDER_MODALITY_MATRIX_V2,
        "format_modality_matrix": _FORMAT_MODALITY_MATRIX_V2,
        "rules": [
            "Provider-specific fields must remain optional and may not become global core requirements.",
            "Mapped imports must produce session.logged-compatible block payloads.",
            "Missing external sensors map to explicit not_measured/not_applicable semantics.",
            "Running/Cycling/Strength/Hybrid modalities must resolve to supported block types.",
        ],
    }
