"""Shared runtime helpers for capability_estimation.v1 outputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import sqrt
from typing import Any, Literal

CapabilityStatus = Literal["ok", "insufficient_data", "degraded_comparability"]

STATUS_OK: CapabilityStatus = "ok"
STATUS_INSUFFICIENT_DATA: CapabilityStatus = "insufficient_data"
STATUS_DEGRADED_COMPARABILITY: CapabilityStatus = "degraded_comparability"

_CONFIDENCE_EPS = 1e-9


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_rir(value: Any) -> float | None:
    parsed = _to_float(value)
    if parsed is None:
        return None
    return round(_clamp(parsed, 0.0, 10.0), 2)


def infer_rir_from_rpe(rpe: Any) -> float | None:
    parsed = _to_float(rpe)
    if parsed is None:
        return None
    return normalize_rir(10.0 - parsed)


def effective_reps_to_failure(
    reps: Any,
    *,
    rir: Any = None,
    rpe: Any = None,
) -> tuple[float | None, str]:
    reps_value = _to_float(reps)
    if reps_value is None or reps_value <= 0.0:
        return None, "invalid"

    explicit_rir = normalize_rir(rir)
    if explicit_rir is not None:
        return reps_value + explicit_rir, "explicit"

    inferred_rir = infer_rir_from_rpe(rpe)
    if inferred_rir is not None:
        return reps_value + inferred_rir, "inferred_from_rpe"

    return reps_value, "fallback_epley"


def effort_adjusted_e1rm(
    weight_kg: Any,
    reps: Any,
    *,
    rir: Any = None,
    rpe: Any = None,
) -> tuple[float, str]:
    weight_value = _to_float(weight_kg)
    if weight_value is None or weight_value <= 0.0:
        return 0.0, "invalid"

    effective_reps, source = effective_reps_to_failure(reps, rir=rir, rpe=rpe)
    if effective_reps is None:
        return 0.0, "invalid"
    return weight_value * (1.0 + (effective_reps / 30.0)), source


def interval_around(mean: float, sd: float, *, z: float = 1.96) -> list[float]:
    sd = max(_CONFIDENCE_EPS, float(sd))
    delta = z * sd
    return [round(mean - delta, 4), round(mean + delta, 4)]


def confidence_from_evidence(
    *,
    observed_points: int,
    required_points: int,
    comparability_degraded: bool = False,
    freshness_days: float | None = None,
    freshness_half_life_days: float = 14.0,
) -> float:
    required = max(1, int(required_points))
    observed = max(0, int(observed_points))
    density = _clamp(observed / required, 0.0, 1.0)
    confidence = density

    if freshness_days is not None and freshness_days >= 0.0:
        decay = 1.0 / (1.0 + (float(freshness_days) / max(1.0, freshness_half_life_days)))
        confidence *= decay
    if comparability_degraded:
        confidence *= 0.65
    return round(_clamp(confidence, 0.0, 1.0), 4)


@dataclass(frozen=True)
class DataSufficiency:
    required_observations: int
    observed_observations: int
    uncertainty_reason_codes: list[str]
    recommended_next_observations: list[str]


def data_sufficiency_block(
    *,
    required_observations: int,
    observed_observations: int,
    uncertainty_reason_codes: list[str] | None = None,
    recommended_next_observations: list[str] | None = None,
) -> dict[str, Any]:
    payload = DataSufficiency(
        required_observations=max(0, int(required_observations)),
        observed_observations=max(0, int(observed_observations)),
        uncertainty_reason_codes=list(uncertainty_reason_codes or []),
        recommended_next_observations=list(recommended_next_observations or []),
    )
    return asdict(payload)


def build_capability_envelope(
    *,
    capability: str,
    estimate_mean: float | None,
    estimate_interval: list[float] | None,
    status: CapabilityStatus,
    confidence: float,
    data_sufficiency: dict[str, Any],
    model_version: str,
    caveats: list[dict[str, Any]] | None = None,
    protocol_signature: dict[str, Any] | None = None,
    comparability: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "capability_output.v1",
        "capability": capability,
        "status": status,
        "estimate": {
            "mean": round(float(estimate_mean), 4) if estimate_mean is not None else None,
            "interval": estimate_interval if estimate_interval is not None else [None, None],
        },
        "confidence": round(_clamp(float(confidence), 0.0, 1.0), 4),
        "data_sufficiency": data_sufficiency,
        "caveats": list(caveats or []),
        "recommended_next_observations": list(
            data_sufficiency.get("recommended_next_observations") or []
        ),
        "protocol_signature": protocol_signature or {},
        "comparability": comparability or {},
        "diagnostics": diagnostics or {},
        "model_version": model_version,
        "generated_at": _now_iso_utc(),
    }


def build_insufficient_envelope(
    *,
    capability: str,
    required_observations: int,
    observed_observations: int,
    model_version: str,
    uncertainty_reason_codes: list[str] | None = None,
    recommended_next_observations: list[str] | None = None,
    protocol_signature: dict[str, Any] | None = None,
    comparability: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sufficiency = data_sufficiency_block(
        required_observations=required_observations,
        observed_observations=observed_observations,
        uncertainty_reason_codes=uncertainty_reason_codes
        or ["insufficient_observation_count"],
        recommended_next_observations=recommended_next_observations or [],
    )
    confidence = confidence_from_evidence(
        observed_points=observed_observations,
        required_points=required_observations,
    )
    return build_capability_envelope(
        capability=capability,
        estimate_mean=None,
        estimate_interval=None,
        status=STATUS_INSUFFICIENT_DATA,
        confidence=confidence,
        data_sufficiency=sufficiency,
        caveats=[
            {
                "code": "insufficient_data",
                "severity": "high",
                "details": {
                    "required_observations": required_observations,
                    "observed_observations": observed_observations,
                },
            }
        ],
        protocol_signature=protocol_signature,
        comparability=comparability,
        diagnostics=diagnostics,
        model_version=model_version,
    )


def summarize_observations(
    values: list[float],
    *,
    variances: list[float] | None = None,
) -> tuple[float | None, float]:
    if not values:
        return None, 0.0

    cleaned_values = [float(v) for v in values]
    if variances:
        cleaned_vars = [max(_CONFIDENCE_EPS, float(v)) for v in variances[: len(cleaned_values)]]
        if len(cleaned_vars) != len(cleaned_values):
            cleaned_vars = [max(_CONFIDENCE_EPS, float(v)) for v in variances]
        if len(cleaned_vars) == len(cleaned_values):
            weights = [1.0 / value for value in cleaned_vars]
            weight_sum = sum(weights)
            if weight_sum > 0:
                mean = sum(v * w for v, w in zip(cleaned_values, weights)) / weight_sum
                sd = sqrt(1.0 / weight_sum)
                return mean, sd

    mean = sum(cleaned_values) / len(cleaned_values)
    if len(cleaned_values) == 1:
        return mean, max(0.05 * abs(mean), 0.05)
    variance = sum((v - mean) ** 2 for v in cleaned_values) / (len(cleaned_values) - 1)
    return mean, sqrt(max(_CONFIDENCE_EPS, variance))
