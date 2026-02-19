"""Calibration protocol and parameter registry for training_load_v2."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from copy import deepcopy
from typing import Any, Iterable

FEATURE_FLAG_TRAINING_LOAD_CALIBRATED = "KURA_FEATURE_TRAINING_LOAD_CALIBRATED"
FEATURE_FLAG_TRAINING_LOAD_RELATIVE_INTENSITY = (
    "KURA_FEATURE_TRAINING_LOAD_RELATIVE_INTENSITY"
)
CALIBRATION_VERSION_ENV = "KURA_TRAINING_LOAD_CALIBRATION_VERSION"

BASELINE_PARAMETER_VERSION = "baseline_v1"
CALIBRATED_PARAMETER_VERSION = "calibrated_v1"
CALIBRATION_PROTOCOL_VERSION = "training_load_calibration.v1"
CALIBRATION_SHADOW_VERSION = "training_load_calibration_shadow.v1"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

_DEFAULT_INTENSITY_MODEL: dict[str, Any] = {
    "resolver_order": ["relative_intensity", "power", "heart_rate", "pace", "rpe"],
    "multiplier": {
        "base": 0.7,
        "response_scale": 0.8,
    },
    "relative_intensity": {
        "source": "relative_intensity",
        "minimum_pct": 10.0,
        "maximum_pct": 130.0,
        "stale_days_soft": 42,
        "stale_days_hard": 120,
        "reference_confidence_default": 0.72,
        "minimum_reference_confidence": 0.35,
        "uncertainty_fresh": 0.1,
        "uncertainty_low_confidence": 0.2,
        "fallback_uncertainty_boost": {
            "stale_reference": 0.16,
            "missing_reference": 0.12,
            "invalid_value": 0.14,
        },
        "endurance_rpe_band_guidance": {
            "threshold": [6.0, 7.0],
            "vo2max": [8.0, 9.0],
            "anaerobic_capacity": [9.0, 10.0],
            "note": (
                "Guidance only; do not treat RPE band labels as strict physiological truth."
            ),
        },
    },
    "modality_prior": {
        "default": 0.6,
        "uncertainty": 0.52,
        "rules": [
            {
                "tokens": ["strength", "squat", "bench", "deadlift", "lift"],
                "value": 0.68,
            },
            {
                "tokens": ["sprint", "accel", "speed", "maxv"],
                "value": 0.8,
            },
            {
                "tokens": ["jump", "plyo", "reactive"],
                "value": 0.75,
            },
            {
                "tokens": ["swim", "pool", "rowing", "row", "bike", "cycle", "run"],
                "value": 0.62,
            },
        ],
    },
    "power": {
        "ratio": {
            "source": "power_ratio",
            "floor": 0.4,
            "window": 0.8,
            "uncertainty": 0.12,
        },
        "absolute": {
            "source": "power_absolute",
            "divisor": 420.0,
            "uncertainty": 0.2,
        },
    },
    "heart_rate": {
        "ratio": {
            "source": "hr_ratio",
            "floor": 0.45,
            "window": 0.5,
            "uncertainty": 0.22,
        },
        "absolute": {
            "source": "hr_absolute",
            "floor": 90.0,
            "window": 100.0,
            "uncertainty": 0.3,
        },
    },
    "pace": {
        "direct": {
            "source": "pace",
            "speed_divisor_mps": 5.5,
            "uncertainty": 0.28,
        },
        "estimated": {
            "source": "pace_estimated",
            "speed_divisor_mps": 5.5,
            "uncertainty": 0.34,
        },
    },
    "rpe": {
        "rpe": {
            "source": "rpe",
            "scale": 10.0,
            "uncertainty": 0.34,
        },
        "rir_inverse": {
            "source": "rir_inverse",
            "scale": 10.0,
            "uncertainty": 0.38,
        },
    },
}

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
        "intensity_model": deepcopy(_DEFAULT_INTENSITY_MODEL),
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
        "intensity_model": deepcopy(_DEFAULT_INTENSITY_MODEL),
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
    return {version: deepcopy(profile) for version, profile in _PARAMETER_REGISTRY_V1.items()}


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


def _profile_number(
    value: Any,
    *,
    default: float,
    minimum: float | None = None,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and parsed < minimum:
        return default
    return parsed


def _intensity_model(profile: dict[str, Any]) -> dict[str, Any]:
    model = profile.get("intensity_model")
    if not isinstance(model, dict):
        return deepcopy(_DEFAULT_INTENSITY_MODEL)
    merged = deepcopy(_DEFAULT_INTENSITY_MODEL)
    for key, value in model.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def _sensor_presence(data: dict[str, Any]) -> dict[str, bool]:
    keys = {str(key).strip().lower() for key in data.keys()}
    return {
        "hr": bool({"heart_rate_avg", "heart_rate_max", "hr_avg", "hr_bpm"} & keys),
        "power": bool({"power", "power_watt", "watts"} & keys),
        "pace": bool({"pace", "pace_min_per_km", "min_per_km"} & keys),
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _to_datetime_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _nested_lookup(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for key in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        if key not in current:
            return None
        current = current.get(key)
    return current


def _pick_first(data: dict[str, Any], paths: Iterable[str]) -> Any:
    for path in paths:
        if "." in path:
            value = _nested_lookup(data, path)
            if value is not None:
                return value
            continue
        if path in data and data.get(path) is not None:
            return data.get(path)
    return None


def _relative_intensity_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    payload = data.get("relative_intensity")
    if isinstance(payload, dict):
        return payload

    value_pct = _pick_first(
        data,
        (
            "relative_intensity_value_pct",
            "relative_intensity_pct",
            "intensity_pct",
            "pct_of_reference",
            "percent_of_max",
            "relative_intensity.value_pct",
        ),
    )
    if value_pct is None:
        return None

    return {
        "value_pct": value_pct,
        "reference_type": _pick_first(
            data,
            (
                "relative_intensity_reference_type",
                "reference_type",
                "relative_intensity.reference_type",
            ),
        ),
        "reference_value": _pick_first(
            data,
            (
                "relative_intensity_reference_value",
                "reference_value",
                "relative_intensity.reference_value",
            ),
        ),
        "reference_measured_at": _pick_first(
            data,
            (
                "relative_intensity_reference_measured_at",
                "reference_measured_at",
                "relative_intensity.reference_measured_at",
            ),
        ),
        "reference_confidence": _pick_first(
            data,
            (
                "relative_intensity_reference_confidence",
                "reference_confidence",
                "relative_intensity.reference_confidence",
            ),
        ),
    }


def _relative_intensity_resolution(
    data: dict[str, Any],
    *,
    model: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "not_present",
        "source": None,
        "value_pct": None,
        "reference_type": None,
        "reference_value": None,
        "reference_measured_at": None,
        "reference_confidence": None,
        "reference_age_days": None,
        "normalized_response": None,
        "uncertainty": None,
        "fallback_reason": None,
        "fallback_uncertainty_boost": 0.0,
    }
    if not _read_flag(FEATURE_FLAG_TRAINING_LOAD_RELATIVE_INTENSITY, default=True):
        result["status"] = "disabled"
        return result

    cfg = model.get("relative_intensity") if isinstance(model.get("relative_intensity"), dict) else {}
    payload = _relative_intensity_payload(data)
    if not isinstance(payload, dict):
        return result

    value_pct = _to_float(payload.get("value_pct"))
    reference_type = str(payload.get("reference_type") or "").strip().lower() or None
    reference_value = _to_float(payload.get("reference_value"))
    reference_measured_at = _to_datetime_utc(payload.get("reference_measured_at"))
    confidence_default = _profile_number(
        cfg.get("reference_confidence_default"),
        default=0.72,
        minimum=0.0,
    )
    reference_confidence = _profile_number(
        payload.get("reference_confidence"),
        default=confidence_default,
        minimum=0.0,
    )
    reference_age_days = None
    if reference_measured_at is not None:
        reference_age_days = max(
            0.0,
            (datetime.now(tz=UTC) - reference_measured_at).total_seconds() / 86400.0,
        )

    result.update(
        {
            "source": str(cfg.get("source") or "relative_intensity"),
            "value_pct": value_pct,
            "reference_type": reference_type,
            "reference_value": reference_value,
            "reference_measured_at": (
                reference_measured_at.isoformat() if reference_measured_at is not None else None
            ),
            "reference_confidence": reference_confidence,
            "reference_age_days": round(reference_age_days, 3)
            if isinstance(reference_age_days, (int, float))
            else None,
        }
    )

    fallback_cfg = (
        cfg.get("fallback_uncertainty_boost")
        if isinstance(cfg.get("fallback_uncertainty_boost"), dict)
        else {}
    )

    if value_pct <= 0.0:
        result["status"] = "fallback_invalid_value"
        result["fallback_reason"] = "invalid_value"
        result["fallback_uncertainty_boost"] = _profile_number(
            fallback_cfg.get("invalid_value"),
            default=0.14,
            minimum=0.0,
        )
        return result

    minimum_pct = _profile_number(cfg.get("minimum_pct"), default=10.0, minimum=0.0)
    maximum_pct = _profile_number(cfg.get("maximum_pct"), default=130.0, minimum=0.0)
    if maximum_pct <= minimum_pct:
        maximum_pct = minimum_pct + 0.1
    if value_pct <= 1.3:
        normalized = value_pct
    else:
        normalized = value_pct / 100.0
    value_pct_normalized = normalized * 100.0
    if value_pct_normalized < minimum_pct or value_pct_normalized > maximum_pct:
        result["status"] = "fallback_invalid_value"
        result["fallback_reason"] = "invalid_value"
        result["fallback_uncertainty_boost"] = _profile_number(
            fallback_cfg.get("invalid_value"),
            default=0.14,
            minimum=0.0,
        )
        return result

    if reference_type is None or reference_measured_at is None:
        result["status"] = "fallback_missing_reference"
        result["fallback_reason"] = "missing_reference"
        result["fallback_uncertainty_boost"] = _profile_number(
            fallback_cfg.get("missing_reference"),
            default=0.12,
            minimum=0.0,
        )
        return result

    stale_days_soft = _profile_number(cfg.get("stale_days_soft"), default=42.0, minimum=0.0)
    stale_days_hard = _profile_number(cfg.get("stale_days_hard"), default=120.0, minimum=stale_days_soft)
    if reference_age_days is not None and reference_age_days > stale_days_hard:
        result["status"] = "fallback_stale_reference"
        result["fallback_reason"] = "stale_reference"
        result["fallback_uncertainty_boost"] = _profile_number(
            fallback_cfg.get("stale_reference"),
            default=0.16,
            minimum=0.0,
        )
        return result
    if reference_age_days is not None and reference_age_days > stale_days_soft:
        result["status"] = "fallback_stale_reference"
        result["fallback_reason"] = "stale_reference"
        result["fallback_uncertainty_boost"] = _profile_number(
            fallback_cfg.get("stale_reference"),
            default=0.16,
            minimum=0.0,
        )
        return result

    uncertainty_fresh = _profile_number(cfg.get("uncertainty_fresh"), default=0.1, minimum=0.0)
    uncertainty_low_conf = _profile_number(
        cfg.get("uncertainty_low_confidence"),
        default=0.2,
        minimum=0.0,
    )
    min_reference_conf = _profile_number(
        cfg.get("minimum_reference_confidence"),
        default=0.35,
        minimum=0.0,
    )
    confidence_term = max(0.0, 1.0 - _clamp(reference_confidence, 0.0, 1.0))
    uncertainty = uncertainty_fresh + (confidence_term * 0.12)
    if reference_confidence < min_reference_conf:
        uncertainty = max(uncertainty, uncertainty_low_conf)

    result["status"] = "used"
    result["normalized_response"] = _clamp(normalized, 0.0, 1.0)
    result["uncertainty"] = _clamp(uncertainty, 0.0, 1.0)
    result["source"] = f"{result['source']}:{reference_type}"
    return result


def _resolve_modality_prior(
    data: dict[str, Any],
    *,
    model: dict[str, Any],
) -> tuple[float, str, float]:
    text = " ".join(
        str(data.get(key) or "").strip().lower()
        for key in ("block_type", "workout_type", "sport", "exercise", "exercise_id")
    )
    prior = model.get("modality_prior") if isinstance(model.get("modality_prior"), dict) else {}
    rules = prior.get("rules") if isinstance(prior.get("rules"), list) else []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        tokens = [str(token).strip().lower() for token in rule.get("tokens") or []]
        if tokens and any(token in text for token in tokens):
            return (
                _clamp(_profile_number(rule.get("value"), default=0.6), 0.0, 1.0),
                "modality_prior",
                _clamp(
                    _profile_number(prior.get("uncertainty"), default=0.52, minimum=0.0),
                    0.0,
                    1.0,
                ),
            )
    return (
        _clamp(_profile_number(prior.get("default"), default=0.6), 0.0, 1.0),
        "modality_prior",
        _clamp(
            _profile_number(prior.get("uncertainty"), default=0.52, minimum=0.0),
            0.0,
            1.0,
        ),
    )


def _power_internal_response(
    data: dict[str, Any],
    *,
    model: dict[str, Any],
) -> tuple[float, str, float] | None:
    power = _to_float(data.get("power_watt", data.get("power", data.get("watts"))))
    if power <= 0.0:
        return None
    power_cfg = model.get("power") if isinstance(model.get("power"), dict) else {}
    ratio_cfg = power_cfg.get("ratio") if isinstance(power_cfg.get("ratio"), dict) else {}
    absolute_cfg = power_cfg.get("absolute") if isinstance(power_cfg.get("absolute"), dict) else {}
    ftp = _to_float(
        data.get("ftp_watt", data.get("ftp", data.get("critical_power_watt")))
    )
    if ftp > 0.0:
        ratio = power / ftp
        ratio_floor = _profile_number(ratio_cfg.get("floor"), default=0.4)
        ratio_window = _profile_number(ratio_cfg.get("window"), default=0.8, minimum=0.001)
        normalized = _clamp((ratio - ratio_floor) / ratio_window, 0.0, 1.0)
        return (
            normalized,
            str(ratio_cfg.get("source") or "power_ratio"),
            _profile_number(ratio_cfg.get("uncertainty"), default=0.12, minimum=0.0),
        )
    absolute_divisor = _profile_number(absolute_cfg.get("divisor"), default=420.0, minimum=0.001)
    normalized = _clamp(power / absolute_divisor, 0.0, 1.0)
    return (
        normalized,
        str(absolute_cfg.get("source") or "power_absolute"),
        _profile_number(absolute_cfg.get("uncertainty"), default=0.2, minimum=0.0),
    )


def _heart_rate_internal_response(
    data: dict[str, Any],
    *,
    model: dict[str, Any],
) -> tuple[float, str, float] | None:
    hr_avg = _to_float(data.get("heart_rate_avg", data.get("hr_avg", data.get("hr_bpm"))))
    if hr_avg <= 0.0:
        return None
    hr_cfg = model.get("heart_rate") if isinstance(model.get("heart_rate"), dict) else {}
    ratio_cfg = hr_cfg.get("ratio") if isinstance(hr_cfg.get("ratio"), dict) else {}
    absolute_cfg = hr_cfg.get("absolute") if isinstance(hr_cfg.get("absolute"), dict) else {}
    hr_max = _to_float(data.get("heart_rate_max", data.get("hr_max")))
    if hr_max > 0.0:
        ratio = hr_avg / hr_max
        ratio_floor = _profile_number(ratio_cfg.get("floor"), default=0.45)
        ratio_window = _profile_number(ratio_cfg.get("window"), default=0.5, minimum=0.001)
        normalized = _clamp((ratio - ratio_floor) / ratio_window, 0.0, 1.0)
        return (
            normalized,
            str(ratio_cfg.get("source") or "hr_ratio"),
            _profile_number(ratio_cfg.get("uncertainty"), default=0.22, minimum=0.0),
        )
    absolute_floor = _profile_number(absolute_cfg.get("floor"), default=90.0)
    absolute_window = _profile_number(absolute_cfg.get("window"), default=100.0, minimum=0.001)
    normalized = _clamp((hr_avg - absolute_floor) / absolute_window, 0.0, 1.0)
    return (
        normalized,
        str(absolute_cfg.get("source") or "hr_absolute"),
        _profile_number(absolute_cfg.get("uncertainty"), default=0.3, minimum=0.0),
    )


def _pace_internal_response(
    data: dict[str, Any],
    *,
    model: dict[str, Any],
) -> tuple[float, str, float] | None:
    pace_cfg = model.get("pace") if isinstance(model.get("pace"), dict) else {}
    direct_cfg = pace_cfg.get("direct") if isinstance(pace_cfg.get("direct"), dict) else {}
    estimated_cfg = pace_cfg.get("estimated") if isinstance(pace_cfg.get("estimated"), dict) else {}
    pace = _to_float(data.get("pace_min_per_km", data.get("pace", data.get("min_per_km"))))
    if pace > 0.0:
        speed_mps = 16.6666667 / pace
        divisor = _profile_number(direct_cfg.get("speed_divisor_mps"), default=5.5, minimum=0.001)
        normalized = _clamp(speed_mps / divisor, 0.0, 1.0)
        return (
            normalized,
            str(direct_cfg.get("source") or "pace"),
            _profile_number(direct_cfg.get("uncertainty"), default=0.28, minimum=0.0),
        )

    duration_seconds = _to_float(data.get("duration_seconds"))
    distance_meters = _to_float(data.get("distance_meters"))
    if duration_seconds > 0.0 and distance_meters > 0.0:
        speed_mps = distance_meters / duration_seconds
        divisor = _profile_number(
            estimated_cfg.get("speed_divisor_mps"),
            default=5.5,
            minimum=0.001,
        )
        normalized = _clamp(speed_mps / divisor, 0.0, 1.0)
        return (
            normalized,
            str(estimated_cfg.get("source") or "pace_estimated"),
            _profile_number(estimated_cfg.get("uncertainty"), default=0.34, minimum=0.0),
        )
    return None


def _rpe_internal_response(
    data: dict[str, Any],
    *,
    model: dict[str, Any],
) -> tuple[float, str, float] | None:
    rpe_cfg = model.get("rpe") if isinstance(model.get("rpe"), dict) else {}
    direct_cfg = rpe_cfg.get("rpe") if isinstance(rpe_cfg.get("rpe"), dict) else {}
    rir_cfg = rpe_cfg.get("rir_inverse") if isinstance(rpe_cfg.get("rir_inverse"), dict) else {}
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
        scale = _profile_number(direct_cfg.get("scale"), default=10.0, minimum=0.001)
        normalized = _clamp(rpe / scale, 0.0, 1.0)
        return (
            normalized,
            str(direct_cfg.get("source") or "rpe"),
            _profile_number(direct_cfg.get("uncertainty"), default=0.34, minimum=0.0),
        )

    rir = _to_float(data.get("rir"))
    if rir > 0.0:
        scale = _profile_number(rir_cfg.get("scale"), default=10.0, minimum=0.001)
        normalized = _clamp(1.0 - (rir / scale), 0.0, 1.0)
        return (
            normalized,
            str(rir_cfg.get("source") or "rir_inverse"),
            _profile_number(rir_cfg.get("uncertainty"), default=0.38, minimum=0.0),
        )
    return None


def _resolve_internal_response(
    data: dict[str, Any],
    *,
    profile: dict[str, Any],
) -> tuple[float, str, float, dict[str, Any]]:
    model = _intensity_model(profile)
    relative_resolution = _relative_intensity_resolution(data, model=model)
    if (
        relative_resolution.get("status") == "used"
        and isinstance(relative_resolution.get("normalized_response"), (float, int))
        and isinstance(relative_resolution.get("uncertainty"), (float, int))
    ):
        return (
            float(relative_resolution["normalized_response"]),
            str(relative_resolution.get("source") or "relative_intensity"),
            float(relative_resolution["uncertainty"]),
            relative_resolution,
        )

    resolver_map = {
        "power": _power_internal_response,
        "heart_rate": _heart_rate_internal_response,
        "pace": _pace_internal_response,
        "rpe": _rpe_internal_response,
    }
    resolver_order_raw = model.get("resolver_order")
    if isinstance(resolver_order_raw, list):
        resolver_order = [str(name).strip().lower() for name in resolver_order_raw]
    else:
        resolver_order = ["power", "heart_rate", "pace", "rpe"]
    fallback_uncertainty_boost = _profile_number(
        relative_resolution.get("fallback_uncertainty_boost"),
        default=0.0,
        minimum=0.0,
    )
    for resolver_name in resolver_order:
        if resolver_name == "relative_intensity":
            continue
        resolver = resolver_map.get(resolver_name)
        if resolver is None:
            continue
        resolved = resolver(data, model=model)
        if resolved is not None:
            normalized, source, uncertainty = resolved
            uncertainty = _clamp(uncertainty + fallback_uncertainty_boost, 0.0, 1.0)
            if relative_resolution.get("fallback_reason"):
                source = (
                    f"{source}|relative_fallback:{relative_resolution['fallback_reason']}"
                )
            return normalized, source, uncertainty, relative_resolution
    normalized, source, uncertainty = _resolve_modality_prior(data, model=model)
    uncertainty = _clamp(uncertainty + fallback_uncertainty_boost, 0.0, 1.0)
    if relative_resolution.get("fallback_reason"):
        source = f"{source}|relative_fallback:{relative_resolution['fallback_reason']}"
    return normalized, source, uncertainty, relative_resolution


def compute_row_load_components_v2(
    *,
    data: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
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
    internal_response, internal_source, uncertainty, relative_resolution = _resolve_internal_response(
        data,
        profile=profile,
    )
    model = _intensity_model(profile)
    multiplier_cfg = (
        model.get("multiplier") if isinstance(model.get("multiplier"), dict) else {}
    )
    multiplier_base = _profile_number(multiplier_cfg.get("base"), default=0.7)
    response_scale = _profile_number(multiplier_cfg.get("response_scale"), default=0.8)
    intensity_multiplier = multiplier_base + (response_scale * internal_response)
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
        "relative_intensity_status": str(relative_resolution.get("status") or "not_present"),
        "relative_intensity_source": relative_resolution.get("source"),
        "relative_intensity_value_pct": relative_resolution.get("value_pct"),
        "relative_intensity_reference_type": relative_resolution.get("reference_type"),
        "relative_intensity_reference_value": relative_resolution.get("reference_value"),
        "relative_intensity_reference_measured_at": relative_resolution.get(
            "reference_measured_at"
        ),
        "relative_intensity_reference_confidence": relative_resolution.get(
            "reference_confidence"
        ),
        "relative_intensity_reference_age_days": relative_resolution.get(
            "reference_age_days"
        ),
        "relative_intensity_fallback_reason": relative_resolution.get("fallback_reason"),
    }


def compute_row_load_components_v1(
    *,
    data: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
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
            "relative_intensity_feature_flag": FEATURE_FLAG_TRAINING_LOAD_RELATIVE_INTENSITY,
            "baseline_version": BASELINE_PARAMETER_VERSION,
            "default_candidate_version": CALIBRATED_PARAMETER_VERSION,
            "available_versions": calibration_parameter_versions(),
            "profiled_parameters": [
                "base_confidence_by_source",
                "sensor_bonus",
                "session_hint_weights",
                "confidence_bounds",
                "load_weights",
                "intensity_model",
            ],
            "dual_load_policy": {
                "external_dose": "volume_kg|duration_seconds|distance_meters|contacts",
                "internal_response": (
                    "relative_intensity->power->heart_rate->pace->rpe with deterministic fallback"
                ),
                "missing_or_stale_reference_policy": (
                    "fallback to sensor/subjective signals and increase uncertainty"
                ),
            },
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
