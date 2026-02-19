"""Declarative modality/provider/format profiles for external import mapping v2."""

from __future__ import annotations

from typing import Any, Literal

SupportState = Literal["supported", "partial", "not_available"]
Modality = Literal[
    "running",
    "cycling",
    "strength",
    "hybrid",
    "swimming",
    "rowing",
    "team_sport",
]

CORE_IMPORT_FIELDS_V2: tuple[str, ...] = (
    "session.started_at",
    "workout.workout_type",
    "dose.work",
    "provenance.source_type",
)

MODALITY_PROFILES_V2: dict[Modality, dict[str, Any]] = {
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
    "swimming": {
        "core_fields": [
            "session.started_at",
            "workout.duration_seconds|workout.distance_meters",
            "dose.work.duration_seconds|dose.work.distance_meters",
        ],
        "optional_fields": [
            "intensity.pace",
            "metrics.heart_rate_avg",
            "intensity.rpe_borg",
        ],
        "provider_specific_optional": [
            "garmin.swolf",
            "trainingpeaks.swim_stroke_metrics",
        ],
        "default_block_types": ["continuous_endurance", "interval_endurance"],
    },
    "rowing": {
        "core_fields": [
            "session.started_at",
            "workout.duration_seconds|workout.distance_meters",
            "dose.work.duration_seconds|dose.work.distance_meters",
        ],
        "optional_fields": [
            "metrics.power_watt",
            "metrics.heart_rate_avg",
            "intensity.pace",
            "intensity.rpe_borg",
        ],
        "provider_specific_optional": [
            "garmin.stroke_rate",
            "trainingpeaks.rowing_power_curve",
        ],
        "default_block_types": ["continuous_endurance", "interval_endurance"],
    },
    "team_sport": {
        "core_fields": [
            "session.started_at",
            "dose.work.duration_seconds|dose.work.distance_meters|dose.work.contacts",
        ],
        "optional_fields": [
            "dose.work.contacts",
            "metrics.heart_rate_avg",
            "intensity.rpe_borg",
            "intensity.pace",
        ],
        "provider_specific_optional": [
            "catapult.accel_decel",
            "strava.suffer_score",
        ],
        "default_block_types": ["speed_endurance", "sprint_accel_maxv", "interval_endurance"],
    },
}

PROVIDER_FIELD_MATRIX_V2: dict[str, dict[str, SupportState]] = {
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

FORMAT_FIELD_MATRIX_V2: dict[str, dict[str, SupportState]] = {
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

PROVIDER_MODALITY_MATRIX_V2: dict[str, dict[Modality, SupportState]] = {
    "garmin": {
        "running": "supported",
        "cycling": "supported",
        "strength": "partial",
        "hybrid": "partial",
        "swimming": "supported",
        "rowing": "partial",
        "team_sport": "partial",
    },
    "strava": {
        "running": "supported",
        "cycling": "supported",
        "strength": "not_available",
        "hybrid": "partial",
        "swimming": "partial",
        "rowing": "partial",
        "team_sport": "partial",
    },
    "trainingpeaks": {
        "running": "supported",
        "cycling": "supported",
        "strength": "partial",
        "hybrid": "supported",
        "swimming": "supported",
        "rowing": "supported",
        "team_sport": "partial",
    },
}

FORMAT_MODALITY_MATRIX_V2: dict[str, dict[Modality, SupportState]] = {
    "fit": {
        "running": "supported",
        "cycling": "supported",
        "strength": "partial",
        "hybrid": "partial",
        "swimming": "supported",
        "rowing": "partial",
        "team_sport": "partial",
    },
    "tcx": {
        "running": "supported",
        "cycling": "supported",
        "strength": "not_available",
        "hybrid": "partial",
        "swimming": "partial",
        "rowing": "partial",
        "team_sport": "partial",
    },
    "gpx": {
        "running": "supported",
        "cycling": "partial",
        "strength": "not_available",
        "hybrid": "not_available",
        "swimming": "partial",
        "rowing": "partial",
        "team_sport": "not_available",
    },
}
