"""Calibration protocol and parameter registry for training_load_v2."""

from __future__ import annotations

import os
from typing import Any, Iterable

FEATURE_FLAG_TRAINING_LOAD_CALIBRATED = "KURA_FEATURE_TRAINING_LOAD_CALIBRATED"
CALIBRATION_VERSION_ENV = "KURA_TRAINING_LOAD_CALIBRATION_VERSION"

BASELINE_PARAMETER_VERSION = "baseline_v1"
CALIBRATED_PARAMETER_VERSION = "calibrated_v1"
CALIBRATION_PROTOCOL_VERSION = "training_load_calibration.v1"
CALIBRATION_SHADOW_VERSION = "training_load_calibration_shadow.v1"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

_PARAMETER_REGISTRY_V1: dict[str, dict[str, Any]] = {
    BASELINE_PARAMETER_VERSION: {
        "base_confidence_by_source": {
            "manual": 0.62,
            "session_logged": 0.68,
            "external_import": 0.72,
        },
        "objective_dim_bonus_first": 0.12,
        "objective_dim_bonus_second": 0.08,
        "sensor_bonus": {
            "hr": 0.01,
            "power": 0.02,
            "pace": 0.01,
        },
        "session_hint_weights": {
            "base_weight": 0.6,
            "hint_weight": 0.4,
        },
        "confidence_bounds": {
            "min": 0.2,
            "max": 0.98,
        },
        "load_weights": {
            "volume_divisor": 100.0,
            "duration_divisor": 300.0,
            "distance_divisor": 1000.0,
            "contacts_divisor": 20.0,
        },
    },
    CALIBRATED_PARAMETER_VERSION: {
        "base_confidence_by_source": {
            "manual": 0.6,
            "session_logged": 0.69,
            "external_import": 0.74,
        },
        "objective_dim_bonus_first": 0.11,
        "objective_dim_bonus_second": 0.09,
        "sensor_bonus": {
            "hr": 0.03,
            "power": 0.04,
            "pace": 0.03,
        },
        "session_hint_weights": {
            "base_weight": 0.55,
            "hint_weight": 0.45,
        },
        "confidence_bounds": {
            "min": 0.2,
            "max": 0.98,
        },
        "load_weights": {
            "volume_divisor": 96.0,
            "duration_divisor": 320.0,
            "distance_divisor": 980.0,
            "contacts_divisor": 18.0,
        },
    },
}


def _read_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    return default


def calibration_parameter_registry_v1() -> dict[str, dict[str, Any]]:
    return {
        version: {
            **profile,
            "base_confidence_by_source": dict(profile["base_confidence_by_source"]),
            "sensor_bonus": dict(profile["sensor_bonus"]),
            "session_hint_weights": dict(profile["session_hint_weights"]),
            "confidence_bounds": dict(profile["confidence_bounds"]),
            "load_weights": dict(profile["load_weights"]),
        }
        for version, profile in _PARAMETER_REGISTRY_V1.items()
    }


def calibration_profile_for_version(version: str) -> dict[str, Any]:
    registry = calibration_parameter_registry_v1()
    profile = registry.get(version)
    if profile is None:
        profile = registry[BASELINE_PARAMETER_VERSION]
        version = BASELINE_PARAMETER_VERSION
    return {
        **profile,
        "version": version,
    }


def calibration_parameter_versions() -> list[str]:
    return sorted(_PARAMETER_REGISTRY_V1.keys())


def active_calibration_version() -> str:
    if not _read_flag(FEATURE_FLAG_TRAINING_LOAD_CALIBRATED, default=True):
        return BASELINE_PARAMETER_VERSION

    requested = os.environ.get(CALIBRATION_VERSION_ENV, "").strip().lower()
    if requested and requested in _PARAMETER_REGISTRY_V1:
        return requested
    return CALIBRATED_PARAMETER_VERSION


def active_calibration_profile() -> dict[str, Any]:
    return calibration_profile_for_version(active_calibration_version())


def _to_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed < 0:
        return 0.0
    return parsed


def _sensor_presence(data: dict[str, Any]) -> dict[str, bool]:
    keys = {str(key).strip().lower() for key in data.keys()}
    return {
        "hr": bool({"heart_rate_avg", "heart_rate_max", "hr_avg", "hr_bpm"} & keys),
        "power": bool({"power", "power_watt", "watts"} & keys),
        "pace": bool({"pace", "pace_min_per_km", "min_per_km"} & keys),
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _resolve_modality_prior(data: dict[str, Any]) -> float:
    text = " ".join(
        str(data.get(key) or "").strip().lower()
        for key in ("block_type", "workout_type", "sport", "exercise", "exercise_id")
    )
    if any(token in text for token in ("strength", "squat", "bench", "deadlift", "lift")):
        return 0.68
    if any(token in text for token in ("sprint", "accel", "speed", "maxv")):
        return 0.8
    if any(token in text for token in ("jump", "plyo", "reactive")):
        return 0.75
    if any(token in text for token in ("swim", "pool", "rowing", "row", "bike", "cycle", "run")):
        return 0.62
    return 0.6


def _power_internal_response(data: dict[str, Any]) -> tuple[float, str, float] | None:
    power = _to_float(data.get("power_watt", data.get("power", data.get("watts"))))
    if power <= 0.0:
        return None
    ftp = _to_float(
        data.get("ftp_watt", data.get("ftp", data.get("critical_power_watt")))
    )
    if ftp > 0.0:
        ratio = power / ftp
        normalized = _clamp((ratio - 0.4) / 0.8, 0.0, 1.0)
        return normalized, "power_ratio", 0.12
    normalized = _clamp(power / 420.0, 0.0, 1.0)
    return normalized, "power_absolute", 0.2


def _heart_rate_internal_response(data: dict[str, Any]) -> tuple[float, str, float] | None:
    hr_avg = _to_float(data.get("heart_rate_avg", data.get("hr_avg", data.get("hr_bpm"))))
    if hr_avg <= 0.0:
        return None
    hr_max = _to_float(data.get("heart_rate_max", data.get("hr_max")))
    if hr_max > 0.0:
        ratio = hr_avg / hr_max
        normalized = _clamp((ratio - 0.45) / 0.5, 0.0, 1.0)
        return normalized, "hr_ratio", 0.22
    normalized = _clamp((hr_avg - 90.0) / 100.0, 0.0, 1.0)
    return normalized, "hr_absolute", 0.3


def _pace_internal_response(data: dict[str, Any]) -> tuple[float, str, float] | None:
    pace = _to_float(data.get("pace_min_per_km", data.get("pace", data.get("min_per_km"))))
    if pace > 0.0:
        speed_mps = 16.6666667 / pace
        normalized = _clamp(speed_mps / 5.5, 0.0, 1.0)
        return normalized, "pace", 0.28

    duration_seconds = _to_float(data.get("duration_seconds"))
    distance_meters = _to_float(data.get("distance_meters"))
    if duration_seconds > 0.0 and distance_meters > 0.0:
        speed_mps = distance_meters / duration_seconds
        normalized = _clamp(speed_mps / 5.5, 0.0, 1.0)
        return normalized, "pace_estimated", 0.34
    return None


def _rpe_internal_response(data: dict[str, Any]) -> tuple[float, str, float] | None:
    rpe = _to_float(
        data.get(
            "session_rpe",
            data.get(
                "rpe",
                data.get("intensity_rpe", data.get("rpe_borg")),
            ),
        )
    )
    if rpe > 0.0:
        normalized = _clamp(rpe / 10.0, 0.0, 1.0)
        return normalized, "rpe", 0.34

    rir = _to_float(data.get("rir"))
    if rir > 0.0:
        normalized = _clamp(1.0 - (rir / 10.0), 0.0, 1.0)
        return normalized, "rir_inverse", 0.38
    return None


def _resolve_internal_response(data: dict[str, Any]) -> tuple[float, str, float]:
    for resolver in (
        _power_internal_response,
        _heart_rate_internal_response,
        _pace_internal_response,
        _rpe_internal_response,
    ):
        resolved = resolver(data)
        if resolved is not None:
            return resolved
    return _resolve_modality_prior(data), "modality_prior", 0.52


def compute_row_load_components_v2(
    *,
    data: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, float | str]:
    weight_kg = _to_float(data.get("weight_kg", data.get("weight")))
    reps = _to_float(data.get("reps"))
    duration_seconds = _to_float(data.get("duration_seconds"))
    distance_meters = _to_float(data.get("distance_meters"))
    contacts = _to_float(data.get("contacts"))
    load_weights = profile["load_weights"]

    volume_kg = weight_kg * reps
    external_dose = (
        (volume_kg / float(load_weights["volume_divisor"]))
        + (duration_seconds / float(load_weights["duration_divisor"]))
        + (distance_meters / float(load_weights["distance_divisor"]))
        + (contacts / float(load_weights["contacts_divisor"]))
    )
    internal_response, internal_source, uncertainty = _resolve_internal_response(data)
    intensity_multiplier = 0.7 + (0.8 * internal_response)
    load_score = external_dose * intensity_multiplier

    return {
        "volume_kg": volume_kg,
        "reps": reps,
        "duration_seconds": duration_seconds,
        "distance_meters": distance_meters,
        "contacts": contacts,
        "external_dose": external_dose,
        "internal_response": internal_response,
        "internal_response_source": internal_source,
        "intensity_multiplier": intensity_multiplier,
        "uncertainty": uncertainty,
        "load_score": load_score,
    }


def compute_row_load_components_v1(
    *,
    data: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, float | str]:
    return compute_row_load_components_v2(data=data, profile=profile)


def compute_row_confidence_v1(
    *,
    data: dict[str, Any],
    source_type: str,
    session_confidence_hint: float | None,
    profile: dict[str, Any],
) -> float:
    source_confidence = profile["base_confidence_by_source"]
    base = float(source_confidence.get(source_type, source_confidence["manual"]))

    components = compute_row_load_components_v1(data=data, profile=profile)
    objective_dims = sum(
        1
        for value in (
            components["volume_kg"],
            components["duration_seconds"],
            components["distance_meters"],
            components["contacts"],
        )
        if value > 0
    )
    if objective_dims >= 1:
        base += float(profile["objective_dim_bonus_first"])
    if objective_dims >= 2:
        base += float(profile["objective_dim_bonus_second"])

    sensors = _sensor_presence(data)
    sensor_bonus = profile["sensor_bonus"]
    if sensors["hr"]:
        base += float(sensor_bonus["hr"])
    if sensors["power"]:
        base += float(sensor_bonus["power"])
    if sensors["pace"]:
        base += float(sensor_bonus["pace"])

    if isinstance(session_confidence_hint, (float, int)) and session_confidence_hint > 0:
        weights = profile["session_hint_weights"]
        clamped_hint = max(0.0, min(1.0, float(session_confidence_hint)))
        base = (base * float(weights["base_weight"])) + (
            clamped_hint * float(weights["hint_weight"])
        )

    bounds = profile["confidence_bounds"]
    return round(
        max(float(bounds["min"]), min(float(bounds["max"]), base)),
        2,
    )


def _target_confidence(sample: dict[str, Any]) -> float:
    direct = sample.get("expected_confidence")
    if isinstance(direct, (int, float)):
        return max(0.0, min(1.0, float(direct)))
    tier = str(sample.get("expected_tier") or "").strip().lower()
    if tier == "analysis_advanced":
        return 0.9
    if tier == "analysis_basic":
        return 0.68
    return 0.4


def _ranking_consistency(predicted: list[float], targets: list[float]) -> float:
    if len(predicted) < 2:
        return 1.0
    comparable = 0
    consistent = 0
    for i in range(len(predicted)):
        for j in range(i + 1, len(predicted)):
            target_delta = targets[i] - targets[j]
            if abs(target_delta) < 1e-9:
                continue
            comparable += 1
            pred_delta = predicted[i] - predicted[j]
            if (pred_delta > 0 and target_delta > 0) or (
                pred_delta < 0 and target_delta < 0
            ):
                consistent += 1
    if comparable == 0:
        return 1.0
    return consistent / comparable


def evaluate_profile_metrics(
    samples: Iterable[dict[str, Any]],
    *,
    version: str,
) -> dict[str, Any]:
    profile = calibration_profile_for_version(version)
    predicted: list[float] = []
    targets: list[float] = []

    for sample in samples:
        data = sample.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        predicted.append(
            compute_row_confidence_v1(
                data=data,
                source_type=str(sample.get("source_type") or "manual"),
                session_confidence_hint=(
                    float(sample["session_confidence_hint"])
                    if isinstance(sample.get("session_confidence_hint"), (int, float))
                    else None
                ),
                profile=profile,
            )
        )
        targets.append(_target_confidence(sample))

    sample_count = len(predicted)
    if sample_count == 0:
        return {
            "version": version,
            "sample_count": 0,
            "brier_score": 0.0,
            "mae": 0.0,
            "calibration_error": 0.0,
            "ranking_consistency": 1.0,
            "composite_score": 0.0,
        }

    brier = sum((pred - target) ** 2 for pred, target in zip(predicted, targets)) / sample_count
    mae = sum(abs(pred - target) for pred, target in zip(predicted, targets)) / sample_count
    calibration_error = abs((sum(predicted) / sample_count) - (sum(targets) / sample_count))
    ranking = _ranking_consistency(predicted, targets)
    composite_score = brier + calibration_error + ((1.0 - ranking) * 0.1)

    return {
        "version": version,
        "sample_count": sample_count,
        "brier_score": round(brier, 6),
        "mae": round(mae, 6),
        "calibration_error": round(calibration_error, 6),
        "ranking_consistency": round(ranking, 6),
        "composite_score": round(composite_score, 6),
    }


def calibration_protocol_v1() -> dict[str, Any]:
    return {
        "schema_version": CALIBRATION_PROTOCOL_VERSION,
        "target_projection": "training_load.v2",
        "cohorts": [
            "strength_manual_only",
            "sprint_interval_manual",
            "endurance_sensor_rich",
            "hybrid_strength_endurance",
            "low_data_user",
        ],
        "label_proxy": {
            "expected_confidence": "0..1",
            "expected_tier_values": ["log_valid", "analysis_basic", "analysis_advanced"],
        },
        "metrics": [
            "brier_score",
            "mae",
            "calibration_error",
            "ranking_consistency",
            "composite_score",
        ],
        "shadow_guardrails": {
            "min_samples": 20,
            "max_brier_score_degradation": 0.005,
            "max_calibration_error_degradation": 0.01,
            "max_ranking_consistency_drop": 0.05,
        },
        "parameter_registry": {
            "env_version_var": CALIBRATION_VERSION_ENV,
            "feature_flag": FEATURE_FLAG_TRAINING_LOAD_CALIBRATED,
            "baseline_version": BASELINE_PARAMETER_VERSION,
            "default_candidate_version": CALIBRATED_PARAMETER_VERSION,
            "available_versions": calibration_parameter_versions(),
        },
    }


def compare_versions_shadow(
    samples: list[dict[str, Any]],
    *,
    baseline_version: str = BASELINE_PARAMETER_VERSION,
    candidate_version: str | None = None,
) -> dict[str, Any]:
    protocol = calibration_protocol_v1()
    guardrails = protocol["shadow_guardrails"]
    candidate = candidate_version or active_calibration_version()

    baseline_metrics = evaluate_profile_metrics(samples, version=baseline_version)
    candidate_metrics = evaluate_profile_metrics(samples, version=candidate)

    delta_brier = round(
        float(candidate_metrics["brier_score"]) - float(baseline_metrics["brier_score"]),
        6,
    )
    delta_calibration = round(
        float(candidate_metrics["calibration_error"])
        - float(baseline_metrics["calibration_error"]),
        6,
    )
    delta_ranking = round(
        float(candidate_metrics["ranking_consistency"])
        - float(baseline_metrics["ranking_consistency"]),
        6,
    )

    checks = [
        {
            "check": "min_samples",
            "pass": int(candidate_metrics["sample_count"]) >= int(guardrails["min_samples"]),
            "value": int(candidate_metrics["sample_count"]),
            "threshold": int(guardrails["min_samples"]),
        },
        {
            "check": "brier_score_degradation",
            "pass": delta_brier <= float(guardrails["max_brier_score_degradation"]),
            "value": delta_brier,
            "threshold": float(guardrails["max_brier_score_degradation"]),
        },
        {
            "check": "calibration_error_degradation",
            "pass": delta_calibration <= float(guardrails["max_calibration_error_degradation"]),
            "value": delta_calibration,
            "threshold": float(guardrails["max_calibration_error_degradation"]),
        },
        {
            "check": "ranking_consistency_drop",
            "pass": (-delta_ranking) <= float(guardrails["max_ranking_consistency_drop"]),
            "value": round(-delta_ranking, 6),
            "threshold": float(guardrails["max_ranking_consistency_drop"]),
        },
    ]
    allow_rollout = all(bool(check["pass"]) for check in checks)

    return {
        "schema_version": CALIBRATION_SHADOW_VERSION,
        "baseline_version": baseline_version,
        "candidate_version": candidate,
        "metrics": {
            "baseline": baseline_metrics,
            "candidate": candidate_metrics,
            "delta": {
                "brier_score": delta_brier,
                "calibration_error": delta_calibration,
                "ranking_consistency": delta_ranking,
            },
        },
        "guardrails": {
            "status": "pass" if allow_rollout else "fail",
            "checks": checks,
        },
        "allow_rollout": allow_rollout,
    }


def select_best_calibration_version(
    samples: list[dict[str, Any]],
    *,
    candidate_versions: list[str] | None = None,
) -> str:
    versions = candidate_versions or calibration_parameter_versions()
    best_version = BASELINE_PARAMETER_VERSION
    best_score = float("inf")
    for version in versions:
        metrics = evaluate_profile_metrics(samples, version=version)
        score = float(metrics["composite_score"])
        if score < best_score:
            best_score = score
            best_version = version
    return best_version


def build_calibration_runner_report(
    samples: list[dict[str, Any]],
    *,
    candidate_versions: list[str] | None = None,
) -> dict[str, Any]:
    recommended = select_best_calibration_version(
        samples,
        candidate_versions=candidate_versions,
    )
    shadow = compare_versions_shadow(
        samples,
        baseline_version=BASELINE_PARAMETER_VERSION,
        candidate_version=recommended,
    )
    return {
        "schema_version": "training_load_calibration_runner.v1",
        "protocol_version": CALIBRATION_PROTOCOL_VERSION,
        "recommended_version": recommended,
        "shadow": shadow,
    }
