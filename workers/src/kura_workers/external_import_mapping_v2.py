"""Import mapping v2 for external sources -> session.logged block model."""

from __future__ import annotations

from typing import Any

from .external_activity_contract import CanonicalExternalActivityV1
from .external_import_mapping_profiles_v2 import (
    CORE_IMPORT_FIELDS_V2,
    FORMAT_FIELD_MATRIX_V2,
    FORMAT_MODALITY_MATRIX_V2,
    MODALITY_PROFILES_V2,
    PROVIDER_FIELD_MATRIX_V2,
    PROVIDER_MODALITY_MATRIX_V2,
    Modality,
)
from .training_session_contract import (
    BLOCK_TYPES,
    CONTRACT_VERSION_V1,
    validate_session_logged_payload,
)


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
) -> tuple[Modality, float, str]:
    text = f"{workout_type} {sport or ''}".strip().lower()
    if any(
        token in text
        for token in ("soccer", "football", "basketball", "handball", "hockey", "team")
    ):
        return "team_sport", 0.93, "token_hint_team_sport"
    if any(token in text for token in ("swim", "pool", "openwater")):
        return "swimming", 0.93, "token_hint_swimming"
    if any(token in text for token in ("row", "rowing", "erg")):
        return "rowing", 0.93, "token_hint_rowing"
    if any(token in text for token in ("brick", "multisport", "triathlon", "hybrid")):
        return "hybrid", 0.9, "token_hint_hybrid"
    if any(token in text for token in ("bike", "ride", "cycling")):
        return "cycling", 0.92, "token_hint_cycling"
    if any(token in text for token in ("strength", "gym", "weight", "lift")):
        return "strength", 0.9, "token_hint_strength"
    if any(token in text for token in ("run", "jog", "track")):
        return "running", 0.9, "token_hint_running"

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
        return "hybrid", 0.74, "set_features_hybrid"
    if has_strength:
        return "strength", 0.7, "set_features_strength"
    if has_endurance:
        if sport and str(sport).strip():
            return "unknown", 0.42, "set_features_endurance_unknown_sport"
        return "unknown", 0.36, "set_features_endurance_open_set"
    return "unknown", 0.2, "open_set_no_modality_signal"


def _block_type_for_workout(modality: Modality, workout_type: str) -> str:
    normalized = workout_type.strip().lower()
    if any(token in normalized for token in ("match", "game", "scrimmage")):
        return "speed_endurance"
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


def _metric_entry(*, value: float, unit: str) -> dict[str, Any]:
    return {
        "measurement_state": "measured",
        "unit": unit,
        "value": round(float(value), 3),
    }


def _relative_intensity_payload(raw: Any) -> dict[str, Any] | None:
    if hasattr(raw, "model_dump"):
        payload = raw.model_dump(mode="python")
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        return None
    if not isinstance(payload, dict):
        return None
    value_pct = _to_float(payload.get("value_pct"))
    reference_type = str(payload.get("reference_type") or "").strip().lower()
    if value_pct is None or value_pct <= 0 or not reference_type:
        return None
    result: dict[str, Any] = {
        "value_pct": round(value_pct, 3),
        "reference_type": reference_type,
    }
    reference_value = _to_float(payload.get("reference_value"))
    if reference_value is not None and reference_value > 0:
        result["reference_value"] = round(reference_value, 3)
    reference_measured_at = payload.get("reference_measured_at")
    if hasattr(reference_measured_at, "isoformat"):
        result["reference_measured_at"] = reference_measured_at.isoformat()
    elif isinstance(reference_measured_at, str) and reference_measured_at.strip():
        result["reference_measured_at"] = reference_measured_at.strip()
    reference_confidence = _to_float(payload.get("reference_confidence"))
    if reference_confidence is not None:
        result["reference_confidence"] = round(reference_confidence, 3)
    return result


def _ensure_workout_intensity_enrichment(
    block: dict[str, Any],
    *,
    heart_rate_avg: float | None,
    heart_rate_max: float | None,
    power_watt: float | None,
    pace_min_per_km: float | None,
    session_rpe: float | None,
    relative_intensity: dict[str, Any] | None = None,
) -> None:
    metrics = block.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
        block["metrics"] = metrics

    if heart_rate_avg is not None and heart_rate_avg > 0:
        metrics["heart_rate_avg"] = _metric_entry(value=heart_rate_avg, unit="bpm")
    if heart_rate_max is not None and heart_rate_max > 0:
        metrics["heart_rate_max"] = _metric_entry(value=heart_rate_max, unit="bpm")
    if power_watt is not None and power_watt > 0:
        metrics["power_watt"] = _metric_entry(value=power_watt, unit="watt")

    anchors = block.get("intensity_anchors")
    has_anchor = isinstance(anchors, list) and len(anchors) > 0
    if (
        isinstance(relative_intensity, dict)
        and relative_intensity.get("value_pct")
        and not isinstance(block.get("relative_intensity"), dict)
    ):
        block["relative_intensity"] = dict(relative_intensity)
    if has_anchor:
        return

    if session_rpe is not None and session_rpe > 0:
        block["intensity_anchors"] = [
            {
                "measurement_state": "measured",
                "unit": "rpe",
                "value": round(session_rpe, 2),
            }
        ]
        block.pop("intensity_anchors_status", None)
        return

    if pace_min_per_km is not None and pace_min_per_km > 0:
        block["intensity_anchors"] = [
            {
                "measurement_state": "measured",
                "unit": "min_per_km",
                "value": round(pace_min_per_km, 3),
            }
        ]
        block.pop("intensity_anchors_status", None)


def _set_slice_to_block(
    set_slice: Any,
    *,
    modality: Modality,
    modality_confidence: float,
    modality_source: str,
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
    relative_intensity = _relative_intensity_payload(set_data.get("relative_intensity"))

    has_strength = reps is not None or weight_kg is not None
    has_endurance = duration_seconds is not None or distance_meters is not None
    set_modality: Modality = modality
    if has_strength and has_endurance:
        set_modality = "hybrid"
    elif has_strength:
        set_modality = "strength"
    elif has_endurance and modality in {"strength", "unknown"}:
        set_modality = "hybrid"

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
            "modality_assignment": {
                "value": set_modality,
                "confidence": round(float(modality_confidence), 3),
                "source": modality_source,
            },
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
    if relative_intensity is not None:
        block["relative_intensity"] = relative_intensity
    return block


def map_external_activity_to_session_logged_v2(
    canonical: CanonicalExternalActivityV1,
) -> dict[str, Any]:
    workout_type = canonical.workout.workout_type or "workout"
    duration_seconds = _to_float(canonical.workout.duration_seconds)
    distance_meters = _to_float(canonical.workout.distance_meters)
    heart_rate_avg = _to_float(canonical.workout.heart_rate_avg)
    heart_rate_max = _to_float(canonical.workout.heart_rate_max)
    power_watt = _to_float(canonical.workout.power_watt)
    pace_min_per_km = _to_float(canonical.workout.pace_min_per_km)
    session_rpe = _to_float(canonical.workout.session_rpe)
    workout_relative_intensity = _relative_intensity_payload(
        canonical.workout.relative_intensity
    )
    if (
        pace_min_per_km is None
        and duration_seconds is not None
        and distance_meters is not None
        and duration_seconds > 0
        and distance_meters > 0
    ):
        pace_min_per_km = (duration_seconds / 60.0) / (distance_meters / 1000.0)
    set_rows = [set_slice.model_dump(mode="python") for set_slice in canonical.sets]
    modality, modality_confidence, modality_source = _infer_modality(
        workout_type=workout_type,
        sport=canonical.workout.sport,
        sets_data=set_rows,
    )

    blocks: list[dict[str, Any]] = []
    for set_slice in canonical.sets:
        mapped = _set_slice_to_block(
            set_slice,
            modality=modality,
            modality_confidence=modality_confidence,
            modality_source=modality_source,
        )
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
                "modality_assignment": {
                    "value": modality,
                    "confidence": round(float(modality_confidence), 3),
                    "source": modality_source,
                },
            },
        }
        if pace_anchor is not None:
            block["intensity_anchors"] = [pace_anchor]
        else:
            block["intensity_anchors_status"] = "not_applicable"
        blocks.append(block)

    for block in blocks:
        _ensure_workout_intensity_enrichment(
            block,
            heart_rate_avg=heart_rate_avg,
            heart_rate_max=heart_rate_max,
            power_watt=power_watt,
            pace_min_per_km=pace_min_per_km,
            session_rpe=session_rpe,
            relative_intensity=workout_relative_intensity,
        )

    payload = {
        "contract_version": CONTRACT_VERSION_V1,
        "session_meta": {
            "sport": canonical.workout.sport or modality,
            "modality": modality,
            "modality_confidence": round(float(modality_confidence), 3),
            "modality_source": modality_source,
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
        "modalities": list(MODALITY_PROFILES_V2.keys()),
        "modality_profiles": MODALITY_PROFILES_V2,
        "required_core_fields": list(CORE_IMPORT_FIELDS_V2),
        "provider_field_matrix": PROVIDER_FIELD_MATRIX_V2,
        "format_field_matrix": FORMAT_FIELD_MATRIX_V2,
        "provider_modality_matrix": PROVIDER_MODALITY_MATRIX_V2,
        "format_modality_matrix": FORMAT_MODALITY_MATRIX_V2,
        "rules": [
            "Provider-specific fields must remain optional and may not become global core requirements.",
            "Mapped imports must produce session.logged-compatible block payloads.",
            "Missing external sensors map to explicit not_measured/not_applicable semantics.",
            "Relative-intensity references are optional and must carry reference metadata when present.",
            "Running/Cycling/Strength/Hybrid/Swimming/Rowing/Team modalities must resolve to supported block types.",
            "Open-set routing keeps uncertain modality as unknown instead of forcing hidden remaps to running.",
        ],
    }
