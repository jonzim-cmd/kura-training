"""Provider mapping matrix v1 for Garmin/Strava/TrainingPeaks (tm5.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from .external_activity_contract import CONTRACT_VERSION_V1

_KJ_TO_KCAL = 0.239005736

TransformName = Literal[
    "identity",
    "minutes_to_seconds",
    "km_to_meters",
    "miles_to_meters",
    "kj_to_kcal",
]


@dataclass(frozen=True)
class MappingRule:
    target_field: str
    source_path: str
    transform: TransformName = "identity"
    confidence: float = 1.0
    source_unit: str | None = None
    target_unit: str | None = None


@dataclass(frozen=True)
class UnsupportedField:
    source_path: str
    reason: str


@dataclass(frozen=True)
class ProviderMatrix:
    provider: str
    version: str
    rules: tuple[MappingRule, ...]
    unsupported_fields: tuple[UnsupportedField, ...]


@dataclass(frozen=True)
class MappingResult:
    canonical_draft: dict[str, Any]
    mapped_fields: list[str]
    unit_conversions: list[dict[str, str]]
    unsupported_fields: list[str]
    warnings: list[str]


_PROVIDER_MATRICES_V1: dict[str, ProviderMatrix] = {
    "garmin": ProviderMatrix(
        provider="garmin",
        version="garmin-v1",
        rules=(
            MappingRule("workout.workout_type", "activity.type"),
            MappingRule(
                "workout.duration_seconds",
                "summary.duration_s",
                source_unit="s",
                target_unit="s",
            ),
            MappingRule(
                "workout.distance_meters",
                "summary.distance_m",
                source_unit="m",
                target_unit="m",
            ),
            MappingRule(
                "workout.calories_kcal",
                "summary.energy_kj",
                transform="kj_to_kcal",
                source_unit="kJ",
                target_unit="kcal",
                confidence=0.95,
            ),
            MappingRule("workout.heart_rate_avg", "summary.heart_rate_avg"),
            MappingRule("workout.heart_rate_max", "summary.heart_rate_max"),
            MappingRule("workout.power_watt", "summary.power_watt"),
            MappingRule("workout.pace_min_per_km", "summary.pace_min_per_km"),
            MappingRule("workout.session_rpe", "summary.session_rpe"),
            MappingRule("session.started_at", "activity.start_time"),
            MappingRule("session.ended_at", "activity.end_time"),
            MappingRule("session.timezone", "activity.timezone"),
        ),
        unsupported_fields=(
            UnsupportedField(
                source_path="summary.ground_contact_balance",
                reason="No canonical target field in contract v1.",
            ),
        ),
    ),
    "strava": ProviderMatrix(
        provider="strava",
        version="strava-v1",
        rules=(
            MappingRule("workout.workout_type", "type"),
            MappingRule(
                "workout.duration_seconds",
                "moving_time",
                source_unit="s",
                target_unit="s",
            ),
            MappingRule(
                "workout.distance_meters",
                "distance",
                source_unit="m",
                target_unit="m",
            ),
            MappingRule(
                "workout.calories_kcal",
                "kilojoules",
                transform="kj_to_kcal",
                source_unit="kJ",
                target_unit="kcal",
                confidence=0.92,
            ),
            MappingRule("workout.heart_rate_avg", "average_heartrate"),
            MappingRule("workout.heart_rate_max", "max_heartrate"),
            MappingRule("workout.power_watt", "average_watts"),
            MappingRule("workout.pace_min_per_km", "pace_min_per_km"),
            MappingRule("workout.session_rpe", "perceived_exertion"),
            MappingRule("session.started_at", "start_date"),
            MappingRule("session.timezone", "timezone"),
        ),
        unsupported_fields=(
            UnsupportedField(
                source_path="suffer_score",
                reason="No canonical target field in contract v1.",
            ),
        ),
    ),
    "trainingpeaks": ProviderMatrix(
        provider="trainingpeaks",
        version="trainingpeaks-v1",
        rules=(
            MappingRule("workout.workout_type", "workout.type"),
            MappingRule(
                "workout.duration_seconds",
                "workout.totalTimeMinutes",
                transform="minutes_to_seconds",
                source_unit="min",
                target_unit="s",
            ),
            MappingRule(
                "workout.distance_meters",
                "workout.distanceKm",
                transform="km_to_meters",
                source_unit="km",
                target_unit="m",
            ),
            MappingRule(
                "workout.calories_kcal",
                "workout.energyKj",
                transform="kj_to_kcal",
                source_unit="kJ",
                target_unit="kcal",
                confidence=0.9,
            ),
            MappingRule("workout.heart_rate_avg", "workout.avgHr"),
            MappingRule("workout.heart_rate_max", "workout.maxHr"),
            MappingRule("workout.power_watt", "workout.avgPower"),
            MappingRule("workout.pace_min_per_km", "workout.paceMinPerKm"),
            MappingRule("workout.session_rpe", "workout.sessionRpe"),
            MappingRule("session.started_at", "workout.startTime"),
            MappingRule("session.ended_at", "workout.endTime"),
            MappingRule("session.timezone", "workout.timezone"),
        ),
        unsupported_fields=(
            UnsupportedField(
                source_path="workout.normalizedPower",
                reason="No canonical target field in contract v1.",
            ),
        ),
    ),
}


def provider_mapping_matrices_v1() -> dict[str, ProviderMatrix]:
    return dict(_PROVIDER_MATRICES_V1)


def _extract_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _apply_transform(value: Any, transform: TransformName) -> Any:
    if value is None:
        return None
    if transform == "identity":
        return value
    numeric = float(value)
    if transform == "minutes_to_seconds":
        return numeric * 60.0
    if transform == "km_to_meters":
        return numeric * 1000.0
    if transform == "miles_to_meters":
        return numeric * 1609.344
    if transform == "kj_to_kcal":
        return numeric * _KJ_TO_KCAL
    return value


def _set_nested(mapping: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = mapping
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _default_source(
    *,
    provider: str,
    provider_user_id: str,
    external_activity_id: str,
    external_event_version: str | None,
    ingestion_method: str,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "provider_user_id": provider_user_id,
        "external_activity_id": external_activity_id,
        "external_event_version": external_event_version,
        "ingestion_method": ingestion_method,
    }


def map_external_payload_v1(
    *,
    provider: str,
    provider_user_id: str,
    external_activity_id: str,
    raw_payload: dict[str, Any],
    external_event_version: str | None = None,
    ingestion_method: str = "file_import",
) -> MappingResult:
    matrix = _PROVIDER_MATRICES_V1.get(provider.lower())
    if matrix is None:
        raise ValueError(f"Unsupported provider for mapping v1: {provider}")

    canonical_draft: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION_V1,
        "source": _default_source(
            provider=matrix.provider,
            provider_user_id=provider_user_id,
            external_activity_id=external_activity_id,
            external_event_version=external_event_version,
            ingestion_method=ingestion_method,
        ),
        "workout": {},
        "session": {},
        "sets": [],
        "provenance": {
            "mapping_version": matrix.version,
            "mapped_at": datetime.now(tz=UTC).isoformat(),
            "source_confidence": 1.0,
            "field_provenance": {},
            "unsupported_fields": [entry.source_path for entry in matrix.unsupported_fields],
            "warnings": [],
        },
    }

    mapped_fields: list[str] = []
    unit_conversions: list[dict[str, str]] = []
    warnings: list[str] = []

    for rule in matrix.rules:
        raw_value = _extract_path(raw_payload, rule.source_path)
        if raw_value is None:
            continue
        try:
            mapped_value = _apply_transform(raw_value, rule.transform)
        except (TypeError, ValueError):
            warnings.append(
                f"Could not map {rule.source_path} -> {rule.target_field} ({rule.transform})"
            )
            continue

        _set_nested(canonical_draft, rule.target_field, mapped_value)
        mapped_fields.append(rule.target_field)
        canonical_draft["provenance"]["field_provenance"][rule.target_field] = {
            "source_path": rule.source_path,
            "confidence": rule.confidence,
            "status": "mapped",
            "transform": rule.transform,
            "unit_original": rule.source_unit,
            "unit_normalized": rule.target_unit,
        }

        if rule.source_unit and rule.target_unit and rule.source_unit != rule.target_unit:
            unit_conversions.append(
                {
                    "field": rule.target_field,
                    "source_unit": rule.source_unit,
                    "target_unit": rule.target_unit,
                    "formula": rule.transform,
                }
            )

    canonical_draft["provenance"]["warnings"] = warnings

    return MappingResult(
        canonical_draft=canonical_draft,
        mapped_fields=mapped_fields,
        unit_conversions=unit_conversions,
        unsupported_fields=[entry.source_path for entry in matrix.unsupported_fields],
        warnings=warnings,
    )
