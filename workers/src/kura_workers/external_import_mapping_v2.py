"""Import mapping v2 spec for external sources -> session.logged block model."""

from __future__ import annotations

from typing import Any, Literal

from .external_activity_contract import CanonicalExternalActivityV1
from .training_session_contract import (
    BLOCK_TYPES,
    CONTRACT_VERSION_V1,
    validate_session_logged_payload,
)

SupportState = Literal["supported", "partial", "not_available"]

_CORE_IMPORT_FIELDS: tuple[str, ...] = (
    "session.started_at",
    "workout.workout_type",
    "dose.work",
    "provenance.source_type",
)

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


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_block_type(workout_type: str) -> str:
    normalized = workout_type.strip().lower()
    if any(token in normalized for token in ("sprint", "maxv", "accel")):
        return "sprint_accel_maxv"
    if any(token in normalized for token in ("interval", "fartlek", "repeat")):
        return "interval_endurance"
    if any(token in normalized for token in ("tempo", "threshold")):
        return "tempo_threshold"
    if any(token in normalized for token in ("strength", "gym", "weight")):
        return "strength_set"
    if any(token in normalized for token in ("plyo", "jump")):
        return "plyometric_reactive"
    if any(token in normalized for token in ("technique", "drill")):
        return "technique_coordination"
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


def _set_slice_to_block(set_slice: Any) -> dict[str, Any] | None:
    if not hasattr(set_slice, "model_dump"):
        return None
    set_data = set_slice.model_dump(mode="python")
    reps = set_data.get("reps")
    weight_kg = set_data.get("weight_kg")
    duration_seconds = set_data.get("duration_seconds")
    distance_meters = set_data.get("distance_meters")
    rest_seconds = set_data.get("rest_seconds")
    rpe = set_data.get("rpe")

    work: dict[str, Any] = {}
    if reps is not None:
        work["reps"] = int(reps)
    if duration_seconds is not None:
        work["duration_seconds"] = float(duration_seconds)
    if distance_meters is not None:
        work["distance_meters"] = float(distance_meters)
    if not work:
        work["reps"] = 1

    block_type = "strength_set" if (reps is not None or weight_kg is not None) else "interval_endurance"
    block: dict[str, Any] = {
        "block_type": block_type,
        "dose": {"work": work, "repeats": 1},
        "metrics": {},
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
        block["intensity_anchors_status"] = "not_applicable"
    return block


def map_external_activity_to_session_logged_v2(
    canonical: CanonicalExternalActivityV1,
) -> dict[str, Any]:
    workout_type = canonical.workout.workout_type or "workout"
    block_type = _to_block_type(workout_type)
    duration_seconds = _to_float(canonical.workout.duration_seconds)
    distance_meters = _to_float(canonical.workout.distance_meters)

    blocks: list[dict[str, Any]] = []
    for set_slice in canonical.sets:
        mapped = _set_slice_to_block(set_slice)
        if mapped is not None:
            blocks.append(mapped)

    if not blocks:
        pace_anchor = _pace_anchor(
            duration_seconds=duration_seconds,
            distance_meters=distance_meters,
        )
        block: dict[str, Any] = {
            "block_type": block_type,
            "dose": _dose_from_workout(
                duration_seconds=duration_seconds,
                distance_meters=distance_meters,
            ),
            "metrics": {},
        }
        if pace_anchor is not None:
            block["intensity_anchors"] = [pace_anchor]
        else:
            block["intensity_anchors_status"] = "not_applicable"
        blocks.append(block)

    payload = {
        "contract_version": CONTRACT_VERSION_V1,
        "session_meta": {
            "sport": canonical.workout.sport or workout_type,
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
        "required_core_fields": list(_CORE_IMPORT_FIELDS),
        "provider_field_matrix": _PROVIDER_FIELD_MATRIX_V2,
        "format_field_matrix": _FORMAT_FIELD_MATRIX_V2,
        "rules": [
            "Provider-specific fields must remain optional and may not become global core requirements.",
            "Mapped imports must produce session.logged-compatible block payloads.",
            "Missing external sensors map to explicit not_measured/not_applicable semantics.",
        ],
    }
