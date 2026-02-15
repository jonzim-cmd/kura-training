"""Offline replay harness for inference calibration checks.

Supports replay from:
- projection histories (current projection artifacts)
- raw event store reconstruction

Optionally persists versioned run artifacts.
"""

from __future__ import annotations

import logging
import os
import hashlib
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .causal_inference import ASSUMPTIONS, estimate_intervention_effect
from .embeddings import cosine_similarity, get_embedding_provider
from .inference_engine import (
    run_readiness_inference,
    run_strength_inference,
    weekly_phase_from_date,
)
from .utils import (
    epley_1rm,
    get_alias_map,
    get_retracted_event_ids,
    resolve_exercise_key,
    resolve_through_aliases,
)

SUPPORTED_PROJECTION_TYPES = (
    "semantic_memory",
    "strength_inference",
    "readiness_inference",
    "causal_inference",
)
EVAL_SOURCE_PROJECTION_HISTORY = "projection_history"
EVAL_SOURCE_EVENT_STORE = "event_store"
EVAL_SOURCE_BOTH = "both"
_SUPPORTED_SOURCES = (
    EVAL_SOURCE_PROJECTION_HISTORY,
    EVAL_SOURCE_EVENT_STORE,
    EVAL_SOURCE_BOTH,
)

EVAL_STATUS_OK = "ok"
EVAL_STATUS_PARTIAL = "partial"
EVAL_STATUS_FAILED = "failed"

SEMANTIC_HIGH_CONFIDENCE_MIN = 0.86
SEMANTIC_MEDIUM_CONFIDENCE_MIN = 0.78
SEMANTIC_DEFAULT_TOP_K = 5

CAUSAL_OUTCOME_READINESS = "readiness_score_t_plus_1"
CAUSAL_OUTCOME_STRENGTH_AGGREGATE = "strength_aggregate_delta_t_plus_1"
CAUSAL_OUTCOME_STRENGTH_PER_EXERCISE = "strength_delta_by_exercise_t_plus_1"
_CAUSAL_OVERLAP_CAVEAT_CODES = {
    "weak_overlap",
    "extreme_weights",
    "low_effective_sample_size",
    "positivity_violation",
    "residual_confounding_risk",
}

logger = logging.getLogger(__name__)


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _ci_bounds(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    low = _as_float(value[0])
    high = _as_float(value[1])
    if low is None or high is None:
        return None
    if low > high:
        low, high = high, low
    return low, high


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _safe_mean(values: Iterable[float]) -> float | None:
    items = list(values)
    if not items:
        return None
    return sum(items) / len(items)


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _mae(errors: list[float]) -> float | None:
    if not errors:
        return None
    return sum(abs(e) for e in errors) / len(errors)


def _rmse(errors: list[float]) -> float | None:
    if not errors:
        return None
    return (sum((e * e) for e in errors) / len(errors)) ** 0.5


def _read_strength_series(history: Any) -> list[tuple[date, float]]:
    if not isinstance(history, list):
        return []
    parsed: list[tuple[date, float]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        d = _parse_date(item.get("date"))
        e1rm = _as_float(item.get("estimated_1rm"))
        if d is None or e1rm is None:
            continue
        parsed.append((d, e1rm))
    parsed.sort(key=lambda x: x[0])
    return parsed


def _read_readiness_series(daily_scores: Any) -> list[tuple[date, float]]:
    if not isinstance(daily_scores, list):
        return []
    parsed: list[tuple[date, float]] = []
    for item in daily_scores:
        if not isinstance(item, dict):
            continue
        d = _parse_date(item.get("date"))
        score = _as_float(item.get("score"))
        if d is None or score is None:
            continue
        parsed.append((d, score))
    parsed.sort(key=lambda x: x[0])
    return parsed


def _normalize_term(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().lower().split())


def _confidence_band(score: float) -> str:
    if score >= SEMANTIC_HIGH_CONFIDENCE_MIN:
        return "high"
    if score >= SEMANTIC_MEDIUM_CONFIDENCE_MIN:
        return "medium"
    return "low"


def _parse_embedding(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    out: list[float] = []
    for item in value:
        number = _as_float(item)
        if number is None:
            return []
        out.append(number)
    return out


def _normalize_projection_types(projection_types: list[str] | None) -> list[str]:
    selected = projection_types or list(SUPPORTED_PROJECTION_TYPES)
    selected = [s for s in selected if s in SUPPORTED_PROJECTION_TYPES]
    if not selected:
        return list(SUPPORTED_PROJECTION_TYPES)
    return selected


def _normalize_source(source: str | None) -> str:
    normalized = (source or EVAL_SOURCE_PROJECTION_HISTORY).strip().lower()
    if normalized not in _SUPPORTED_SOURCES:
        raise ValueError(
            f"Unsupported eval source: {normalized!r}. "
            f"Expected one of {', '.join(_SUPPORTED_SOURCES)}."
        )
    return normalized


@contextmanager
def _temporary_env(name: str, value: str | None):
    previous = os.environ.get(name)
    try:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def evaluate_strength_history(
    key: str,
    history: Any,
    *,
    strength_engine: str = "closed_form",
) -> dict[str, Any]:
    series = _read_strength_series(history)
    if len(series) < 3:
        return {
            "projection_type": "strength_inference",
            "key": key,
            "status": "insufficient_data",
            "series_points": len(series),
            "replay_windows": 0,
            "labeled_windows": 0,
            "metrics": {
                "coverage_ci95": None,
                "mae": None,
                "rmse": None,
                "mean_error": None,
                "plateau_brier": None,
                "velocity_mae": None,
                "direction_accuracy": None,
            },
        }

    start = series[0][0]
    model_points = [((d - start).days, v) for d, v in series]

    replay_windows = 0
    labeled_windows = 0
    inside_ci = 0
    errors: list[float] = []
    plateau_brier_terms: list[float] = []
    velocity_errors: list[float] = []
    direction_hits = 0
    direction_total = 0
    engines_used: dict[str, int] = {}
    horizons_seen: list[int] = []

    plateau_threshold = float(os.environ.get("KURA_BAYES_PLATEAU_SLOPE_PER_DAY", "0.02"))
    derivative_direction_eps = float(os.environ.get("KURA_STRENGTH_DERIVATIVE_VELOCITY_EPS", "0.03"))

    with _temporary_env("KURA_BAYES_ENGINE", strength_engine):
        for i in range(2, len(model_points)):
            train_points = model_points[: i + 1]
            inference = run_strength_inference(train_points)
            if inference.get("status") == "insufficient_data":
                continue

            replay_windows += 1
            engine = str(inference.get("engine", "none"))
            engines_used[engine] = engines_used.get(engine, 0) + 1

            predicted = inference.get("predicted_1rm") or {}
            pred_mean = _as_float(predicted.get("mean"))
            pred_ci = _ci_bounds(predicted.get("ci95"))
            horizon_days = int(predicted.get("horizon_days") or 0)
            if horizon_days <= 0:
                continue
            horizons_seen.append(horizon_days)

            current_date = series[i][0]
            current_e1rm = series[i][1]
            target_date = current_date + timedelta(days=horizon_days)

            actual_future: float | None = None
            for future_date, future_value in series[i + 1 :]:
                if future_date >= target_date:
                    actual_future = future_value
                    break
            if actual_future is None or pred_mean is None or pred_ci is None:
                continue

            labeled_windows += 1
            error = pred_mean - actual_future
            errors.append(error)

            if pred_ci[0] <= actual_future <= pred_ci[1]:
                inside_ci += 1

            plateau_probability = _as_float((inference.get("trend") or {}).get("plateau_probability"))
            if plateau_probability is not None:
                realized_slope = (actual_future - current_e1rm) / float(horizon_days)
                plateau_label = 1.0 if realized_slope <= plateau_threshold else 0.0
                plateau_brier_terms.append((plateau_probability - plateau_label) ** 2)

            realized_slope = (actual_future - current_e1rm) / float(horizon_days)
            dynamics = inference.get("dynamics") or {}
            predicted_velocity = _as_float(
                dynamics.get("model_velocity_per_day", dynamics.get("velocity_per_day"))
            )
            if predicted_velocity is not None:
                velocity_errors.append(predicted_velocity - realized_slope)

            predicted_direction = str(dynamics.get("direction") or "")
            if predicted_direction in {"up", "flat", "down"}:
                direction_total += 1
                if realized_slope > derivative_direction_eps:
                    realized_direction = "up"
                elif realized_slope < -derivative_direction_eps:
                    realized_direction = "down"
                else:
                    realized_direction = "flat"
                if predicted_direction == realized_direction:
                    direction_hits += 1

    metrics = {
        "coverage_ci95": _round_or_none(_safe_ratio(inside_ci, labeled_windows), 6),
        "mae": _round_or_none(_mae(errors), 6),
        "rmse": _round_or_none(_rmse(errors), 6),
        "mean_error": _round_or_none(_safe_mean(errors), 6),
        "plateau_brier": _round_or_none(_safe_mean(plateau_brier_terms), 6),
        "velocity_mae": _round_or_none(_mae(velocity_errors), 6),
        "direction_accuracy": _round_or_none(_safe_ratio(direction_hits, direction_total), 6),
    }
    status = "ok" if labeled_windows > 0 else "insufficient_labels"
    return {
        "projection_type": "strength_inference",
        "key": key,
        "status": status,
        "series_points": len(series),
        "replay_windows": replay_windows,
        "labeled_windows": labeled_windows,
        "engines_used": engines_used,
        "horizon_days_seen": sorted(set(horizons_seen)),
        "metrics": metrics,
    }


def _state_from_score(score: float) -> str:
    if score >= 0.72:
        return "high"
    if score <= 0.45:
        return "low"
    return "moderate"


def evaluate_readiness_daily_scores(key: str, daily_scores: Any) -> dict[str, Any]:
    series = _read_readiness_series(daily_scores)
    if len(series) < 5:
        return {
            "projection_type": "readiness_inference",
            "key": key,
            "status": "insufficient_data",
            "series_points": len(series),
            "replay_windows": 0,
            "labeled_windows": 0,
            "metrics": {
                "coverage_ci95_nowcast": None,
                "mae_nowcast": None,
                "rmse_nowcast": None,
                "state_accuracy": None,
                "velocity_mae_nowcast": None,
                "direction_accuracy_nowcast": None,
            },
        }

    observations = [v for _, v in series]
    replay_windows = 0
    labeled_windows = 0
    inside_ci = 0
    errors: list[float] = []
    state_hits = 0
    velocity_errors: list[float] = []
    direction_hits = 0
    direction_total = 0
    derivative_direction_eps = float(os.environ.get("KURA_READINESS_DERIVATIVE_VELOCITY_EPS", "0.015"))

    for i in range(4, len(observations)):
        subset = observations[: i + 1]
        inference = run_readiness_inference(subset)
        if inference.get("status") == "insufficient_data":
            continue
        replay_windows += 1

        readiness_today = inference.get("readiness_today") or {}
        pred_mean = _as_float(readiness_today.get("mean"))
        pred_ci = _ci_bounds(readiness_today.get("ci95"))
        pred_state = readiness_today.get("state")
        actual = observations[i]

        if pred_mean is None or pred_ci is None:
            continue

        labeled_windows += 1
        errors.append(pred_mean - actual)
        if pred_ci[0] <= actual <= pred_ci[1]:
            inside_ci += 1

        if pred_state in {"low", "moderate", "high"} and pred_state == _state_from_score(actual):
            state_hits += 1

        dynamics = inference.get("dynamics") or {}
        predicted_velocity = _as_float(dynamics.get("velocity_per_day"))
        if predicted_velocity is not None and i > 0:
            actual_velocity = observations[i] - observations[i - 1]
            velocity_errors.append(predicted_velocity - actual_velocity)

            predicted_direction = str(dynamics.get("direction") or "")
            if predicted_direction in {"up", "flat", "down"}:
                direction_total += 1
                if actual_velocity > derivative_direction_eps:
                    realized_direction = "up"
                elif actual_velocity < -derivative_direction_eps:
                    realized_direction = "down"
                else:
                    realized_direction = "flat"
                if predicted_direction == realized_direction:
                    direction_hits += 1

    metrics = {
        "coverage_ci95_nowcast": _round_or_none(_safe_ratio(inside_ci, labeled_windows), 6),
        "mae_nowcast": _round_or_none(_mae(errors), 6),
        "rmse_nowcast": _round_or_none(_rmse(errors), 6),
        "state_accuracy": _round_or_none(_safe_ratio(state_hits, labeled_windows), 6),
        "velocity_mae_nowcast": _round_or_none(_mae(velocity_errors), 6),
        "direction_accuracy_nowcast": _round_or_none(_safe_ratio(direction_hits, direction_total), 6),
    }
    status = "ok" if labeled_windows > 0 else "insufficient_labels"
    return {
        "projection_type": "readiness_inference",
        "key": key,
        "status": status,
        "series_points": len(series),
        "replay_windows": replay_windows,
        "labeled_windows": labeled_windows,
        "engines_used": {"normal_normal": replay_windows} if replay_windows > 0 else {},
        "metrics": metrics,
    }


def _causal_metrics_template() -> dict[str, Any]:
    return {
        "ok_intervention_rate": None,
        "ok_outcome_rate": None,
        "segment_ok_rate": None,
        "median_ci95_width": None,
        "mean_abs_effect": None,
        "directional_consistency": None,
        "high_severity_caveat_rate": None,
        "overlap_warning_rate": None,
        "caveat_density_per_window": None,
    }


def _sum_numeric_leaves(value: Any) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if isinstance(value, dict):
        return sum(_sum_numeric_leaves(v) for v in value.values())
    if isinstance(value, list):
        return sum(_sum_numeric_leaves(v) for v in value)
    return 0.0


def _iter_causal_outcomes(intervention_payload: dict[str, Any]) -> list[tuple[str, str | None, dict[str, Any]]]:
    outcomes = intervention_payload.get("outcomes")
    out: list[tuple[str, str | None, dict[str, Any]]] = []

    if not isinstance(outcomes, dict):
        out.append((CAUSAL_OUTCOME_READINESS, None, intervention_payload))
        return out

    readiness_payload = outcomes.get(CAUSAL_OUTCOME_READINESS)
    if isinstance(readiness_payload, dict):
        out.append((CAUSAL_OUTCOME_READINESS, None, readiness_payload))

    strength_aggregate_payload = outcomes.get(CAUSAL_OUTCOME_STRENGTH_AGGREGATE)
    if isinstance(strength_aggregate_payload, dict):
        out.append((CAUSAL_OUTCOME_STRENGTH_AGGREGATE, None, strength_aggregate_payload))

    per_exercise_payload = outcomes.get(CAUSAL_OUTCOME_STRENGTH_PER_EXERCISE)
    if isinstance(per_exercise_payload, dict):
        for exercise_id, payload in sorted(per_exercise_payload.items(), key=lambda item: str(item[0])):
            if not isinstance(payload, dict):
                continue
            out.append((CAUSAL_OUTCOME_STRENGTH_PER_EXERCISE, str(exercise_id), payload))

    return out


def _iter_causal_segment_results(
    intervention_payload: dict[str, Any],
) -> list[tuple[str, str, str, dict[str, Any]]]:
    heterogeneous = intervention_payload.get("heterogeneous_effects")
    if not isinstance(heterogeneous, dict):
        return []

    out: list[tuple[str, str, str, dict[str, Any]]] = []
    for outcome_name, outcome_payload in heterogeneous.items():
        if outcome_name == "minimum_segment_samples":
            continue
        if not isinstance(outcome_payload, dict):
            continue

        subgroup_payload = outcome_payload.get("subgroups")
        if isinstance(subgroup_payload, dict):
            for segment_label, segment_result in sorted(
                subgroup_payload.items(),
                key=lambda item: str(item[0]),
            ):
                if isinstance(segment_result, dict):
                    out.append((str(outcome_name), "subgroup", str(segment_label), segment_result))

        phase_payload = outcome_payload.get("phases")
        if isinstance(phase_payload, dict):
            for segment_label, segment_result in sorted(
                phase_payload.items(),
                key=lambda item: str(item[0]),
            ):
                if isinstance(segment_result, dict):
                    out.append((str(outcome_name), "phase", str(segment_label), segment_result))

    return out


def _estimate_causal_effect(
    samples: list[dict[str, Any]],
    *,
    min_samples: int,
    bootstrap_samples: int,
) -> dict[str, Any]:
    result = estimate_intervention_effect(
        samples,
        min_samples=min_samples,
        bootstrap_samples=bootstrap_samples,
    )
    diagnostics = dict(result.get("diagnostics") or {})
    diagnostics["observed_windows"] = len(samples)
    result["diagnostics"] = diagnostics
    return result


def _ensure_causal_segment_guardrail(
    result: dict[str, Any],
    *,
    observed_samples: int,
    min_samples: int,
    segment_type: str,
    segment_label: str,
) -> None:
    if observed_samples >= min_samples:
        return
    caveats = result.setdefault("caveats", [])
    if any(c.get("code") == "segment_insufficient_samples" for c in caveats):
        return
    caveats.append(
        {
            "code": "segment_insufficient_samples",
            "severity": "medium",
            "details": {
                "segment_type": segment_type,
                "segment_label": segment_label,
                "required_samples": min_samples,
                "observed_samples": observed_samples,
            },
        }
    )


def _estimate_causal_segment_slices(
    samples: list[dict[str, Any]],
    *,
    segment_key: str,
    min_samples: int,
    bootstrap_samples: int,
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        segment_label = str(sample.get(segment_key) or "unknown")
        buckets.setdefault(segment_label, []).append(sample)

    out: dict[str, dict[str, Any]] = {}
    for segment_label, rows in sorted(buckets.items(), key=lambda item: item[0]):
        base_rows = [
            {
                "treated": row.get("treated"),
                "outcome": row.get("outcome"),
                "confounders": row.get("confounders", {}),
            }
            for row in rows
        ]
        result = _estimate_causal_effect(
            base_rows,
            min_samples=min_samples,
            bootstrap_samples=bootstrap_samples,
        )
        _ensure_causal_segment_guardrail(
            result,
            observed_samples=len(base_rows),
            min_samples=min_samples,
            segment_type=segment_key,
            segment_label=segment_label,
        )
        diagnostics = dict(result.get("diagnostics") or {})
        diagnostics["segment_type"] = segment_key
        diagnostics["segment_label"] = segment_label
        diagnostics["segment_samples"] = len(base_rows)
        result["diagnostics"] = diagnostics
        out[segment_label] = result
    return out


def _append_causal_caveats(
    machine_caveats: list[dict[str, Any]],
    *,
    intervention: str,
    outcome: str,
    result: dict[str, Any],
    exercise_id: str | None = None,
    segment_type: str | None = None,
    segment_label: str | None = None,
) -> None:
    for caveat in result.get("caveats", []):
        payload: dict[str, Any] = {
            "intervention": intervention,
            "outcome": outcome,
            "code": caveat.get("code"),
            "severity": caveat.get("severity"),
            "details": caveat.get("details", {}),
        }
        if exercise_id:
            payload["exercise_id"] = exercise_id
        if segment_type:
            payload["segment_type"] = segment_type
        if segment_label:
            payload["segment_label"] = segment_label
        machine_caveats.append(payload)


def _causal_subgroup_bucket(readiness_score: float) -> str:
    return "low_readiness" if readiness_score < 0.55 else "high_readiness"


def _causal_phase_bucket(value: str | None) -> str:
    phase = (value or "unknown").strip().lower()
    if not phase:
        return "unknown"
    return phase


def _round_strength_snapshot(values: dict[str, float]) -> dict[str, float]:
    return {k: round(float(v), 2) for k, v in sorted(values.items())}


def evaluate_causal_projection(key: str, projection_data: Any) -> dict[str, Any]:
    data = projection_data if isinstance(projection_data, dict) else {}
    interventions = data.get("interventions")
    if not isinstance(interventions, dict):
        interventions = {}

    evidence_window = data.get("evidence_window")
    if not isinstance(evidence_window, dict):
        evidence_window = {}
    data_quality = data.get("data_quality")
    if not isinstance(data_quality, dict):
        data_quality = {}

    replay_windows = int(
        _as_float(evidence_window.get("windows_evaluated"))
        or _sum_numeric_leaves(data_quality.get("outcome_windows"))
        or 0
    )
    series_points = int(
        _as_float(evidence_window.get("days_considered"))
        or _as_float(data_quality.get("observed_days"))
        or 0
    )

    metrics = _causal_metrics_template()
    if not interventions:
        return {
            "projection_type": "causal_inference",
            "key": key,
            "status": "insufficient_data",
            "series_points": series_points,
            "replay_windows": replay_windows,
            "labeled_windows": 0,
            "engines_used": {},
            "metrics": metrics,
        }

    intervention_total = 0
    intervention_ok = 0
    outcome_total = 0
    outcome_ok = 0
    segment_total = 0
    segment_ok = 0
    ci_widths: list[float] = []
    abs_effects: list[float] = []
    direction_consistency: list[float] = []

    fallback_caveat_total = 0
    fallback_high_severity = 0
    fallback_overlap_warnings = 0

    for _, intervention_payload in sorted(interventions.items(), key=lambda item: str(item[0])):
        if not isinstance(intervention_payload, dict):
            continue
        intervention_total += 1
        if str(intervention_payload.get("status") or "") == "ok":
            intervention_ok += 1

        for _, _, outcome_payload in _iter_causal_outcomes(intervention_payload):
            outcome_total += 1
            if str(outcome_payload.get("status") or "") == "ok":
                outcome_ok += 1

                effect = outcome_payload.get("effect")
                if isinstance(effect, dict):
                    mean_ate = _as_float(effect.get("mean_ate"))
                    if mean_ate is not None:
                        abs_effects.append(abs(mean_ate))

                    ci = _ci_bounds(effect.get("ci95"))
                    if ci is not None:
                        ci_widths.append(max(0.0, ci[1] - ci[0]))

                    probability_positive = _as_float(effect.get("probability_positive"))
                    direction = str(effect.get("direction") or "uncertain")
                    if probability_positive is not None:
                        if direction == "positive":
                            direction_consistency.append(probability_positive)
                        elif direction == "negative":
                            direction_consistency.append(1.0 - probability_positive)
                        else:
                            direction_consistency.append(
                                max(0.0, 1.0 - (2.0 * abs(probability_positive - 0.5)))
                            )

            for caveat in outcome_payload.get("caveats", []):
                if not isinstance(caveat, dict):
                    continue
                fallback_caveat_total += 1
                if str(caveat.get("severity") or "") == "high":
                    fallback_high_severity += 1
                if str(caveat.get("code") or "") in _CAUSAL_OVERLAP_CAVEAT_CODES:
                    fallback_overlap_warnings += 1

        for _, _, _, segment_result in _iter_causal_segment_results(intervention_payload):
            segment_total += 1
            if str(segment_result.get("status") or "") == "ok":
                segment_ok += 1
            for caveat in segment_result.get("caveats", []):
                if not isinstance(caveat, dict):
                    continue
                fallback_caveat_total += 1
                if str(caveat.get("severity") or "") == "high":
                    fallback_high_severity += 1
                if str(caveat.get("code") or "") in _CAUSAL_OVERLAP_CAVEAT_CODES:
                    fallback_overlap_warnings += 1

    machine_caveats = data.get("machine_caveats")
    if isinstance(machine_caveats, list):
        caveat_total = len(machine_caveats)
        high_severity_caveats = sum(
            1
            for caveat in machine_caveats
            if isinstance(caveat, dict) and str(caveat.get("severity") or "") == "high"
        )
        overlap_warnings = sum(
            1
            for caveat in machine_caveats
            if isinstance(caveat, dict)
            and str(caveat.get("code") or "") in _CAUSAL_OVERLAP_CAVEAT_CODES
        )
    else:
        caveat_total = fallback_caveat_total
        high_severity_caveats = fallback_high_severity
        overlap_warnings = fallback_overlap_warnings

    metrics["ok_intervention_rate"] = _round_or_none(
        _safe_ratio(intervention_ok, intervention_total),
        6,
    )
    metrics["ok_outcome_rate"] = _round_or_none(_safe_ratio(outcome_ok, outcome_total), 6)
    metrics["segment_ok_rate"] = _round_or_none(_safe_ratio(segment_ok, segment_total), 6)
    metrics["median_ci95_width"] = (
        _round_or_none(_median(ci_widths), 6) if ci_widths else None
    )
    metrics["mean_abs_effect"] = _round_or_none(_safe_mean(abs_effects), 6)
    metrics["directional_consistency"] = _round_or_none(
        _safe_mean(direction_consistency),
        6,
    )
    metrics["high_severity_caveat_rate"] = _round_or_none(
        _safe_ratio(high_severity_caveats, max(1, outcome_total)),
        6,
    )
    metrics["overlap_warning_rate"] = _round_or_none(
        _safe_ratio(overlap_warnings, max(1, outcome_total)),
        6,
    )
    metrics["caveat_density_per_window"] = (
        _round_or_none(caveat_total / float(replay_windows), 6)
        if replay_windows > 0
        else None
    )

    if outcome_total == 0:
        status = "insufficient_data"
    elif outcome_ok == 0:
        status = "insufficient_labels"
    else:
        status = "ok"

    engine = str(data.get("engine") or "")
    return {
        "projection_type": "causal_inference",
        "key": key,
        "status": status,
        "series_points": series_points,
        "replay_windows": replay_windows,
        "labeled_windows": outcome_ok,
        "engines_used": ({engine: 1} if engine else {}),
        "metrics": metrics,
    }


def build_causal_projection_from_event_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    alias_map: dict[str, str] = {}
    for row in rows:
        if row.get("event_type") != "exercise.alias_created":
            continue
        data = row.get("data") or {}
        if not isinstance(data, dict):
            continue
        alias = str(data.get("alias") or "").strip().lower()
        target = str(data.get("exercise_id") or "").strip().lower()
        if alias and target:
            alias_map[alias] = target

    per_day: dict[date, dict[str, Any]] = {}
    for row in rows:
        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        day = ts.date()
        bucket = per_day.setdefault(
            day,
            {
                "sleep_hours_sum": 0.0,
                "sleep_entries": 0,
                "energy_sum": 0.0,
                "energy_entries": 0,
                "soreness_sum": 0.0,
                "soreness_entries": 0,
                "load_volume": 0.0,
                "protein_g": 0.0,
                "calories": 0.0,
                "program_events": 0,
                "sleep_target_events": 0,
                "nutrition_target_events": 0,
                "strength_by_exercise": {},
            },
        )

        event_type = str(row.get("event_type") or "")
        data = row.get("data")
        if not isinstance(data, dict):
            data = {}

        if event_type == "sleep.logged":
            duration = _as_float(data.get("duration_hours"))
            if duration is not None and duration > 0.0:
                bucket["sleep_hours_sum"] += duration
                bucket["sleep_entries"] += 1
        elif event_type == "energy.logged":
            energy = _as_float(data.get("level"))
            if energy is not None and energy > 0.0:
                bucket["energy_sum"] += energy
                bucket["energy_entries"] += 1
        elif event_type == "soreness.logged":
            soreness = _as_float(data.get("severity"))
            if soreness is not None and soreness > 0.0:
                bucket["soreness_sum"] += soreness
                bucket["soreness_entries"] += 1
        elif event_type == "meal.logged":
            protein = _as_float(data.get("protein_g"))
            calories = _as_float(data.get("calories"))
            bucket["protein_g"] += max(0.0, protein or 0.0)
            bucket["calories"] += max(0.0, calories or 0.0)
        elif event_type in {
            "program.started",
            "training_plan.created",
            "training_plan.updated",
            "training_plan.archived",
        }:
            bucket["program_events"] += 1
        elif event_type == "sleep_target.set":
            bucket["sleep_target_events"] += 1
        elif event_type == "nutrition_target.set":
            bucket["nutrition_target_events"] += 1
        elif event_type == "set.logged":
            weight = _as_float(data.get("weight_kg", data.get("weight")))
            reps = _as_float(data.get("reps"))
            if weight is not None and reps is not None and weight > 0.0 and reps > 0.0:
                bucket["load_volume"] += weight * reps
                raw_key = resolve_exercise_key(data)
                if raw_key:
                    canonical = resolve_through_aliases(raw_key, alias_map)
                    e1rm = epley_1rm(weight, int(round(reps)))
                    if canonical and e1rm > 0.0:
                        previous = _as_float(
                            bucket["strength_by_exercise"].get(canonical)
                        ) or 0.0
                        if e1rm > previous:
                            bucket["strength_by_exercise"][canonical] = e1rm

    observed_days = sorted(per_day)
    load_values = [
        float(per_day[day]["load_volume"])
        for day in observed_days
        if float(per_day[day]["load_volume"]) > 0.0
    ]
    load_baseline = max(1.0, _median(load_values))

    daily_context: list[dict[str, Any]] = []
    strength_state: dict[str, float] = {}
    for day in observed_days:
        bucket = per_day[day]

        sleep_hours = (
            bucket["sleep_hours_sum"] / bucket["sleep_entries"]
            if bucket["sleep_entries"] > 0
            else 6.5
        )
        energy = (
            bucket["energy_sum"] / bucket["energy_entries"]
            if bucket["energy_entries"] > 0
            else 6.0
        )
        soreness_avg = (
            bucket["soreness_sum"] / bucket["soreness_entries"]
            if bucket["soreness_entries"] > 0
            else 0.0
        )
        load_volume = float(bucket["load_volume"])
        protein_g = float(bucket["protein_g"])

        for exercise_id, value in (bucket["strength_by_exercise"] or {}).items():
            strength_state[str(exercise_id)] = _as_float(value) or 0.0
        strength_snapshot = dict(strength_state)
        strength_aggregate = (
            _safe_mean(strength_snapshot.values()) if strength_snapshot else None
        )

        sleep_score = _clamp(sleep_hours / 8.0, 0.0, 1.2)
        energy_score = _clamp(energy / 10.0, 0.0, 1.0)
        soreness_penalty = _clamp(soreness_avg / 10.0, 0.0, 1.0)
        load_penalty = _clamp(load_volume / load_baseline, 0.0, 1.4)

        readiness_score = _clamp(
            (0.45 * sleep_score)
            + (0.35 * energy_score)
            - (0.20 * soreness_penalty)
            - (0.15 * load_penalty)
            + 0.25,
            0.0,
            1.0,
        )

        daily_context.append(
            {
                "date": day.isoformat(),
                "readiness_score": round(readiness_score, 3),
                "sleep_hours": round(sleep_hours, 2),
                "load_volume": round(load_volume, 2),
                "protein_g": round(protein_g, 2),
                "calories": round(float(bucket["calories"]), 2),
                "strength_aggregate_e1rm": (
                    round(float(strength_aggregate), 2)
                    if strength_aggregate is not None
                    else None
                ),
                "strength_by_exercise": _round_strength_snapshot(strength_snapshot),
                "program_change_event": bool(bucket["program_events"]),
                "sleep_target_event": bool(bucket["sleep_target_events"]),
                "nutrition_target_event": bool(bucket["nutrition_target_events"]),
            }
        )

    history_days_required = 7
    windows_evaluated = 0
    samples_by_intervention: dict[str, dict[str, Any]] = {
        "program_change": {
            "outcomes": {
                CAUSAL_OUTCOME_READINESS: [],
                CAUSAL_OUTCOME_STRENGTH_AGGREGATE: [],
            },
            "strength_by_exercise": {},
        },
        "nutrition_shift": {
            "outcomes": {
                CAUSAL_OUTCOME_READINESS: [],
                CAUSAL_OUTCOME_STRENGTH_AGGREGATE: [],
            },
            "strength_by_exercise": {},
        },
        "sleep_intervention": {
            "outcomes": {
                CAUSAL_OUTCOME_READINESS: [],
                CAUSAL_OUTCOME_STRENGTH_AGGREGATE: [],
            },
            "strength_by_exercise": {},
        },
    }

    for idx in range(history_days_required, len(daily_context) - 1):
        current = daily_context[idx]
        next_day = daily_context[idx + 1]
        history = daily_context[idx - history_days_required:idx]
        windows_evaluated += 1

        baseline_readiness = _safe_mean(
            _as_float(day.get("readiness_score")) or 0.0 for day in history
        ) or 0.5
        baseline_sleep = _safe_mean(
            _as_float(day.get("sleep_hours")) or 0.0 for day in history
        ) or 6.5
        baseline_load = _safe_mean(
            _as_float(day.get("load_volume")) or 0.0 for day in history
        ) or 0.0
        baseline_protein = _safe_mean(
            _as_float(day.get("protein_g")) or 0.0 for day in history
        ) or 0.0
        baseline_strength_aggregate = _safe_mean(
            (_as_float(day.get("strength_aggregate_e1rm")) or 0.0)
            for day in history
            if day.get("strength_aggregate_e1rm") is not None
        ) or 0.0

        current_readiness = _as_float(current.get("readiness_score")) or 0.0
        current_strength_aggregate = (
            _as_float(current.get("strength_aggregate_e1rm"))
            if current.get("strength_aggregate_e1rm") is not None
            else None
        )
        next_strength_aggregate = (
            _as_float(next_day.get("strength_aggregate_e1rm"))
            if next_day.get("strength_aggregate_e1rm") is not None
            else None
        )
        readiness_outcome = _as_float(next_day.get("readiness_score")) or 0.0

        sleep_shift = (_as_float(current.get("sleep_hours")) or 0.0) >= (baseline_sleep + 0.75)
        nutrition_shift = (_as_float(current.get("protein_g")) or 0.0) >= (baseline_protein + 20.0)

        subgroup = _causal_subgroup_bucket(current_readiness)
        phase = _causal_phase_bucket(weekly_phase_from_date(current.get("date")).get("phase"))

        common_confounders = {
            "baseline_readiness": baseline_readiness,
            "baseline_sleep_hours": baseline_sleep,
            "baseline_load_volume": baseline_load,
            "baseline_protein_g": baseline_protein,
            "baseline_strength_aggregate": baseline_strength_aggregate,
            "current_readiness": current_readiness,
            "current_sleep_hours": _as_float(current.get("sleep_hours")) or 0.0,
            "current_load_volume": _as_float(current.get("load_volume")) or 0.0,
            "current_protein_g": _as_float(current.get("protein_g")) or 0.0,
            "current_calories": _as_float(current.get("calories")) or 0.0,
            "current_strength_aggregate": current_strength_aggregate or 0.0,
        }

        flags = {
            "program_change": 1 if bool(current.get("program_change_event")) else 0,
            "nutrition_shift": 1
            if bool(current.get("nutrition_target_event")) or nutrition_shift
            else 0,
            "sleep_intervention": 1
            if bool(current.get("sleep_target_event")) or sleep_shift
            else 0,
        }

        aggregate_delta: float | None = None
        if current_strength_aggregate is not None and next_strength_aggregate is not None:
            aggregate_delta = next_strength_aggregate - current_strength_aggregate

        current_strength_map = current.get("strength_by_exercise") or {}
        next_strength_map = next_day.get("strength_by_exercise") or {}
        exercise_deltas: dict[str, float] = {}
        for exercise_id in set(current_strength_map).intersection(next_strength_map):
            current_value = _as_float(current_strength_map.get(exercise_id)) or 0.0
            next_value = _as_float(next_strength_map.get(exercise_id)) or current_value
            exercise_deltas[str(exercise_id)] = next_value - current_value

        for intervention_name, treated_flag in flags.items():
            bucket = samples_by_intervention[intervention_name]
            outcomes_bucket = bucket["outcomes"]
            base_sample = {
                "treated": treated_flag,
                "confounders": dict(common_confounders),
                "subgroup": subgroup,
                "phase": phase,
            }
            outcomes_bucket[CAUSAL_OUTCOME_READINESS].append(
                {**base_sample, "outcome": readiness_outcome}
            )
            if aggregate_delta is not None:
                outcomes_bucket[CAUSAL_OUTCOME_STRENGTH_AGGREGATE].append(
                    {**base_sample, "outcome": aggregate_delta}
                )

            strength_outcomes = bucket["strength_by_exercise"]
            for exercise_id, delta in exercise_deltas.items():
                per_ex_confounders = dict(common_confounders)
                per_ex_confounders["current_exercise_strength"] = (
                    _as_float(current_strength_map.get(exercise_id)) or 0.0
                )
                strength_outcomes.setdefault(exercise_id, []).append(
                    {
                        "treated": treated_flag,
                        "outcome": delta,
                        "confounders": per_ex_confounders,
                        "subgroup": subgroup,
                        "phase": phase,
                    }
                )

    min_samples = max(12, int(os.environ.get("KURA_CAUSAL_MIN_SAMPLES", "24")))
    strength_min_samples = max(
        12,
        int(
            os.environ.get(
                "KURA_CAUSAL_STRENGTH_MIN_SAMPLES",
                str(max(12, min_samples - 6)),
            )
        ),
    )
    segment_min_samples = max(
        10,
        int(
            os.environ.get(
                "KURA_CAUSAL_SEGMENT_MIN_SAMPLES",
                str(max(10, min_samples // 2)),
            )
        ),
    )
    bootstrap_samples = max(80, int(os.environ.get("KURA_CAUSAL_BOOTSTRAP_SAMPLES", "250")))

    interventions: dict[str, Any] = {}
    machine_caveats: list[dict[str, Any]] = []
    treated_windows: dict[str, int] = {}
    outcome_windows: dict[str, Any] = {}
    has_ok = False

    for intervention_name, sample_payload in samples_by_intervention.items():
        outcome_samples = sample_payload["outcomes"]
        per_ex_samples = sample_payload["strength_by_exercise"]
        readiness_samples = outcome_samples[CAUSAL_OUTCOME_READINESS]
        aggregate_samples = outcome_samples[CAUSAL_OUTCOME_STRENGTH_AGGREGATE]

        treated_windows[intervention_name] = sum(
            1
            for sample in readiness_samples
            if int(_as_float(sample.get("treated")) or 0) == 1
        )

        readiness_result = _estimate_causal_effect(
            readiness_samples,
            min_samples=min_samples,
            bootstrap_samples=bootstrap_samples,
        )
        aggregate_result = _estimate_causal_effect(
            aggregate_samples,
            min_samples=strength_min_samples,
            bootstrap_samples=bootstrap_samples,
        )

        per_ex_results: dict[str, Any] = {}
        per_ex_windows: dict[str, int] = {}
        for exercise_id, samples in sorted(per_ex_samples.items(), key=lambda item: item[0]):
            per_ex_windows[exercise_id] = len(samples)
            per_ex_results[exercise_id] = _estimate_causal_effect(
                samples,
                min_samples=strength_min_samples,
                bootstrap_samples=bootstrap_samples,
            )

        hetero = {
            "minimum_segment_samples": segment_min_samples,
            CAUSAL_OUTCOME_READINESS: {
                "subgroups": _estimate_causal_segment_slices(
                    readiness_samples,
                    segment_key="subgroup",
                    min_samples=segment_min_samples,
                    bootstrap_samples=bootstrap_samples,
                ),
                "phases": _estimate_causal_segment_slices(
                    readiness_samples,
                    segment_key="phase",
                    min_samples=segment_min_samples,
                    bootstrap_samples=bootstrap_samples,
                ),
            },
            CAUSAL_OUTCOME_STRENGTH_AGGREGATE: {
                "subgroups": _estimate_causal_segment_slices(
                    aggregate_samples,
                    segment_key="subgroup",
                    min_samples=segment_min_samples,
                    bootstrap_samples=bootstrap_samples,
                ),
                "phases": _estimate_causal_segment_slices(
                    aggregate_samples,
                    segment_key="phase",
                    min_samples=segment_min_samples,
                    bootstrap_samples=bootstrap_samples,
                ),
            },
        }

        _append_causal_caveats(
            machine_caveats,
            intervention=intervention_name,
            outcome=CAUSAL_OUTCOME_READINESS,
            result=readiness_result,
        )
        _append_causal_caveats(
            machine_caveats,
            intervention=intervention_name,
            outcome=CAUSAL_OUTCOME_STRENGTH_AGGREGATE,
            result=aggregate_result,
        )
        for exercise_id, result in per_ex_results.items():
            _append_causal_caveats(
                machine_caveats,
                intervention=intervention_name,
                outcome=CAUSAL_OUTCOME_STRENGTH_PER_EXERCISE,
                result=result,
                exercise_id=exercise_id,
            )
        for outcome_name in (CAUSAL_OUTCOME_READINESS, CAUSAL_OUTCOME_STRENGTH_AGGREGATE):
            for segment_type, bucket in (
                ("subgroup", hetero[outcome_name]["subgroups"]),
                ("phase", hetero[outcome_name]["phases"]),
            ):
                for segment_label, segment_result in bucket.items():
                    _append_causal_caveats(
                        machine_caveats,
                        intervention=intervention_name,
                        outcome=outcome_name,
                        result=segment_result,
                        segment_type=segment_type,
                        segment_label=segment_label,
                    )

        statuses = [
            readiness_result.get("status") == "ok",
            aggregate_result.get("status") == "ok",
            any(str(v.get("status")) == "ok" for v in per_ex_results.values()),
        ]
        intervention_status = "ok" if any(statuses) else "insufficient_data"
        has_ok = has_ok or intervention_status == "ok"

        intervention_payload = dict(readiness_result)
        intervention_payload["status"] = intervention_status
        intervention_payload["primary_outcome"] = CAUSAL_OUTCOME_READINESS
        intervention_payload["outcomes"] = {
            CAUSAL_OUTCOME_READINESS: readiness_result,
            CAUSAL_OUTCOME_STRENGTH_AGGREGATE: aggregate_result,
            CAUSAL_OUTCOME_STRENGTH_PER_EXERCISE: per_ex_results,
        }
        intervention_payload["heterogeneous_effects"] = hetero
        interventions[intervention_name] = intervention_payload

        outcome_windows[intervention_name] = {
            CAUSAL_OUTCOME_READINESS: len(readiness_samples),
            CAUSAL_OUTCOME_STRENGTH_AGGREGATE: len(aggregate_samples),
            CAUSAL_OUTCOME_STRENGTH_PER_EXERCISE: per_ex_windows,
        }

    return {
        "status": "ok" if has_ok else "insufficient_data",
        "engine": "propensity_ipw_bootstrap",
        "assumptions": ASSUMPTIONS,
        "interventions": interventions,
        "machine_caveats": machine_caveats,
        "evidence_window": {
            "days_considered": len(daily_context),
            "windows_evaluated": windows_evaluated,
            "history_days_required": history_days_required,
            "minimum_segment_samples": segment_min_samples,
        },
        "daily_context": daily_context[-60:],
        "data_quality": {
            "events_processed": len(rows),
            "observed_days": len(observed_days),
            "treated_windows": treated_windows,
            "outcome_windows": outcome_windows,
        },
    }


def build_semantic_labels_from_event_rows(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Build term -> canonical labels from user-confirmed exercise mappings."""
    term_targets: dict[str, Counter[str]] = {}

    for row in rows:
        event_type = str(row.get("event_type") or "")
        data = row.get("data") or {}
        if not isinstance(data, dict):
            continue

        term = ""
        target = ""
        if event_type == "exercise.alias_created":
            term = _normalize_term(data.get("alias"))
            target = _normalize_term(data.get("exercise_id"))
        elif event_type == "set.logged":
            term = _normalize_term(data.get("exercise"))
            target = _normalize_term(data.get("exercise_id"))
        if not term or not target:
            continue

        bucket = term_targets.setdefault(term, Counter())
        bucket[target] += 1

    labels: dict[str, str] = {}
    for term, targets in term_targets.items():
        winner = targets.most_common(1)
        if winner:
            labels[term] = winner[0][0]
    return labels


def _evaluate_semantic_predictions(
    *,
    key: str,
    labels: dict[str, str],
    predictions: dict[str, list[tuple[str, float]]],
    top_k: int,
) -> dict[str, Any]:
    if not labels:
        return {
            "projection_type": "semantic_memory",
            "key": key,
            "status": "insufficient_data",
            "series_points": 0,
            "replay_windows": 0,
            "labeled_windows": 0,
            "metrics": {
                "coverage": None,
                "top1_accuracy": None,
                "topk_recall": None,
                "mrr": None,
                "top1_brier": None,
            },
            "confidence_calibration": {},
        }

    covered = 0
    top1_hits = 0
    topk_hits = 0
    mrr_sum = 0.0
    brier_terms: list[float] = []
    confidence_band_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    confidence_band_hits: dict[str, int] = {"high": 0, "medium": 0, "low": 0}

    for term, target in labels.items():
        ranked = predictions.get(term) or []
        if not ranked:
            continue
        covered += 1

        top_key, top_score = ranked[0]
        band = _confidence_band(top_score)
        confidence_band_counts[band] += 1

        if top_key == target:
            top1_hits += 1
            confidence_band_hits[band] += 1
            brier_terms.append((top_score - 1.0) ** 2)
        else:
            brier_terms.append((top_score - 0.0) ** 2)

        rank_of_target: int | None = None
        for rank, (candidate_key, _) in enumerate(ranked[:top_k], start=1):
            if candidate_key == target:
                rank_of_target = rank
                break
        if rank_of_target is not None:
            topk_hits += 1
            mrr_sum += 1.0 / rank_of_target

    labeled = len(labels)
    calibration: dict[str, dict[str, float | int | None]] = {}
    for band in ("high", "medium", "low"):
        total = confidence_band_counts[band]
        hits = confidence_band_hits[band]
        calibration[band] = {
            "count": total,
            "precision": _round_or_none(_safe_ratio(hits, total), 6),
        }

    metrics = {
        "coverage": _round_or_none(_safe_ratio(covered, labeled), 6),
        "top1_accuracy": _round_or_none(_safe_ratio(top1_hits, labeled), 6),
        "topk_recall": _round_or_none(_safe_ratio(topk_hits, labeled), 6),
        "mrr": _round_or_none((mrr_sum / labeled) if labeled else None, 6),
        "top1_brier": _round_or_none(_safe_mean(brier_terms), 6),
    }
    status = "ok" if covered > 0 else "insufficient_labels"
    return {
        "projection_type": "semantic_memory",
        "key": key,
        "status": status,
        "series_points": covered,
        "replay_windows": covered,
        "labeled_windows": labeled,
        "metrics": metrics,
        "confidence_calibration": calibration,
    }


def evaluate_semantic_memory_projection_labels(
    key: str,
    projection_data: Any,
    labels: dict[str, str],
    *,
    top_k: int = SEMANTIC_DEFAULT_TOP_K,
) -> dict[str, Any]:
    data = projection_data if isinstance(projection_data, dict) else {}
    candidates = data.get("exercise_candidates")
    predictions: dict[str, list[tuple[str, float]]] = {}
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            term = _normalize_term(item.get("term"))
            canonical = _normalize_term(item.get("suggested_exercise_id"))
            score = _as_float(item.get("score"))
            if not term or not canonical or score is None:
                continue
            predictions[term] = [(canonical, score)]
    result = _evaluate_semantic_predictions(
        key=key,
        labels=labels,
        predictions=predictions,
        top_k=max(1, int(top_k)),
    )
    result["top_k"] = max(1, int(top_k))
    return result


def evaluate_semantic_event_store_labels(
    labels: dict[str, str],
    catalog_embeddings: list[dict[str, Any]],
    *,
    top_k: int = SEMANTIC_DEFAULT_TOP_K,
) -> dict[str, Any]:
    if not labels:
        return _evaluate_semantic_predictions(
            key="overview",
            labels={},
            predictions={},
            top_k=max(1, int(top_k)),
        )

    provider = get_embedding_provider()
    terms = sorted(labels.keys())
    vectors = provider.embed_many(terms)
    predictions: dict[str, list[tuple[str, float]]] = {}
    rank_limit = max(1, int(top_k))

    for term, term_vec in zip(terms, vectors):
        scored: list[tuple[str, float]] = []
        for catalog in catalog_embeddings:
            score = cosine_similarity(term_vec, catalog["embedding"])
            if score <= 0.0:
                continue
            scored.append((catalog["canonical_key"], score))
        scored.sort(key=lambda x: x[1], reverse=True)
        predictions[term] = scored[:rank_limit]

    result = _evaluate_semantic_predictions(
        key="overview",
        labels=labels,
        predictions=predictions,
        top_k=rank_limit,
    )
    result["top_k"] = rank_limit
    return result


def _median(values: list[float]) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def filter_retracted_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop event.retracted records and the events they retract."""
    retracted_ids: set[str] = set()
    for row in rows:
        if row.get("event_type") != "event.retracted":
            continue
        data = row.get("data") or {}
        retracted_id = str(data.get("retracted_event_id") or "").strip()
        if retracted_id:
            retracted_ids.add(retracted_id)

    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("event_type") == "event.retracted":
            continue
        row_id = str(row.get("id") or "")
        if row_id and row_id in retracted_ids:
            continue
        out.append(row)
    return out


def build_strength_histories_from_event_rows(
    rows: list[dict[str, Any]],
    alias_map: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """Reconstruct strength history per canonical exercise from raw events."""
    session_best: dict[str, dict[str, tuple[datetime, float]]] = {}
    day_best: dict[str, dict[str, float]] = {}

    for row in rows:
        if row.get("event_type") != "set.logged":
            continue
        data = row.get("data") or {}
        raw_key = resolve_exercise_key(data)
        if not raw_key:
            continue
        canonical = resolve_through_aliases(raw_key, alias_map)
        if not canonical:
            continue

        try:
            weight = float(data.get("weight_kg", data.get("weight", 0)))
            reps = int(data.get("reps", 0))
        except (ValueError, TypeError):
            continue

        e1rm = epley_1rm(weight, reps)
        if e1rm <= 0:
            continue

        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        session_id = str(metadata.get("session_id") or ts.date().isoformat())
        day_key = ts.date().isoformat()

        per_ex_session = session_best.setdefault(canonical, {})
        prev = per_ex_session.get(session_id)
        if prev is None or e1rm > prev[1]:
            per_ex_session[session_id] = (ts, e1rm)

        per_ex_day = day_best.setdefault(canonical, {})
        prev_day = per_ex_day.get(day_key, 0.0)
        if e1rm > prev_day:
            per_ex_day[day_key] = e1rm

    out: dict[str, list[dict[str, Any]]] = {}
    for canonical, by_day in day_best.items():
        history = [
            {"date": d, "estimated_1rm": round(v, 2)}
            for d, v in sorted(by_day.items())
        ]
        if history:
            out[canonical] = history
    return out


def build_readiness_daily_scores_from_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct readiness daily_scores from raw events."""
    per_day: dict[str, dict[str, Any]] = {}
    load_values: list[float] = []

    for row in rows:
        event_type = row.get("event_type")
        if event_type not in {"set.logged", "sleep.logged", "soreness.logged", "energy.logged"}:
            continue
        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        day = ts.date().isoformat()
        bucket = per_day.setdefault(day, {})
        data = row.get("data") or {}

        if event_type == "sleep.logged":
            try:
                bucket["sleep_hours"] = float(data.get("duration_hours"))
            except (TypeError, ValueError):
                pass
        elif event_type == "energy.logged":
            try:
                bucket["energy"] = float(data.get("level"))
            except (TypeError, ValueError):
                pass
        elif event_type == "soreness.logged":
            try:
                sev = float(data.get("severity"))
            except (TypeError, ValueError):
                continue
            bucket["soreness_sum"] = bucket.get("soreness_sum", 0.0) + sev
            bucket["soreness_count"] = bucket.get("soreness_count", 0) + 1
        elif event_type == "set.logged":
            try:
                weight = float(data.get("weight_kg", data.get("weight", 0)))
                reps = float(data.get("reps", 0))
            except (TypeError, ValueError):
                continue
            volume = max(0.0, weight * reps)
            bucket["load_volume"] = bucket.get("load_volume", 0.0) + volume

    for values in per_day.values():
        load = float(values.get("load_volume", 0.0))
        if load > 0.0:
            load_values.append(load)
    load_baseline = max(1.0, _median(load_values))

    daily_scores: list[dict[str, Any]] = []
    for day in sorted(per_day):
        values = per_day[day]
        has_any = any(
            key in values for key in ("sleep_hours", "energy", "soreness_sum", "load_volume")
        )
        if not has_any:
            continue

        sleep_score = _clamp(float(values.get("sleep_hours", 6.5)) / 8.0, 0.0, 1.2)
        energy_score = _clamp(float(values.get("energy", 6.0)) / 10.0, 0.0, 1.0)
        soreness_avg = 0.0
        if values.get("soreness_count", 0):
            soreness_avg = float(values.get("soreness_sum", 0.0)) / float(values["soreness_count"])
        soreness_penalty = _clamp(soreness_avg / 10.0, 0.0, 1.0)

        load = float(values.get("load_volume", 0.0))
        load_penalty = _clamp(load / load_baseline, 0.0, 1.4)

        score = (
            0.45 * sleep_score
            + 0.35 * energy_score
            - 0.20 * soreness_penalty
            - 0.15 * load_penalty
            + 0.25
        )
        score = _clamp(score, 0.0, 1.0)

        daily_scores.append(
            {
                "date": day,
                "score": round(score, 3),
                "components": {
                    "sleep": round(sleep_score, 3),
                    "energy": round(energy_score, 3),
                    "soreness_penalty": round(soreness_penalty, 3),
                    "load_penalty": round(load_penalty, 3),
                },
            }
        )

    return daily_scores


def evaluate_from_event_store_rows(
    rows: list[dict[str, Any]],
    *,
    projection_types: list[str] | None = None,
    strength_engine: str = "closed_form",
    semantic_top_k: int = SEMANTIC_DEFAULT_TOP_K,
    catalog_embeddings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Run replay evaluation directly from raw event rows."""
    selected = _normalize_projection_types(projection_types)
    active_rows = filter_retracted_event_rows(rows)
    labels = build_semantic_labels_from_event_rows(active_rows)
    alias_map: dict[str, str] = {}
    for row in active_rows:
        if row.get("event_type") != "exercise.alias_created":
            continue
        data = row.get("data") or {}
        alias = str(data.get("alias", "")).strip().lower()
        target = str(data.get("exercise_id", "")).strip().lower()
        if alias and target:
            alias_map[alias] = target

    results: list[dict[str, Any]] = []
    if "strength_inference" in selected:
        histories = build_strength_histories_from_event_rows(active_rows, alias_map)
        for key in sorted(histories):
            result = evaluate_strength_history(
                key,
                histories[key],
                strength_engine=strength_engine,
            )
            result["source"] = EVAL_SOURCE_EVENT_STORE
            results.append(result)

    if "readiness_inference" in selected:
        readiness_daily = build_readiness_daily_scores_from_event_rows(active_rows)
        result = evaluate_readiness_daily_scores("overview", readiness_daily)
        result["source"] = EVAL_SOURCE_EVENT_STORE
        results.append(result)

    if "semantic_memory" in selected:
        semantic_catalog = catalog_embeddings or []
        result = evaluate_semantic_event_store_labels(
            labels,
            semantic_catalog,
            top_k=semantic_top_k,
        )
        result["source"] = EVAL_SOURCE_EVENT_STORE
        results.append(result)

    if "causal_inference" in selected:
        causal_projection = build_causal_projection_from_event_rows(active_rows)
        result = evaluate_causal_projection("overview", causal_projection)
        result["source"] = EVAL_SOURCE_EVENT_STORE
        results.append(result)

    return results


def summarize_projection_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        ptype = str(row.get("projection_type") or "unknown")
        by_type.setdefault(ptype, []).append(row)

    summary: dict[str, Any] = {}
    for ptype, rows in by_type.items():
        total_replay = sum(int(r.get("replay_windows", 0) or 0) for r in rows)
        total_labeled = sum(int(r.get("labeled_windows", 0) or 0) for r in rows)
        ok_count = sum(1 for r in rows if r.get("status") == "ok")
        summary[ptype] = {
            "projection_rows": len(rows),
            "ok_rows": ok_count,
            "replay_windows": total_replay,
            "labeled_windows": total_labeled,
        }
    return summary


def summarize_projection_results_by_source(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, dict[str, Any]] = {}
    for row in results:
        source = str(row.get("source") or "unknown")
        ptype = str(row.get("projection_type") or "unknown")
        source_bucket = by_source.setdefault(
            source,
            {
                "projection_rows": 0,
                "ok_rows": 0,
                "replay_windows": 0,
                "labeled_windows": 0,
                "by_projection_type": {},
            },
        )
        source_bucket["projection_rows"] += 1
        if row.get("status") == "ok":
            source_bucket["ok_rows"] += 1
        source_bucket["replay_windows"] += int(row.get("replay_windows", 0) or 0)
        source_bucket["labeled_windows"] += int(row.get("labeled_windows", 0) or 0)

        by_type = source_bucket["by_projection_type"].setdefault(
            ptype,
            {"projection_rows": 0, "ok_rows": 0},
        )
        by_type["projection_rows"] += 1
        if row.get("status") == "ok":
            by_type["ok_rows"] += 1
    return by_source


def _metric_threshold(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _metric_values_for_source(
    results: list[dict[str, Any]],
    *,
    projection_type: str,
    metric_name: str,
    source: str,
) -> list[float]:
    out: list[float] = []
    for row in results:
        if row.get("projection_type") != projection_type:
            continue
        if row.get("source") != source:
            continue
        if row.get("status") != "ok":
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            continue
        value = _as_float(metrics.get(metric_name))
        if value is None:
            continue
        out.append(value)
    return out


def _compare_metric(value: float, comparator: str, threshold: float) -> bool:
    if comparator == "ge":
        return value >= threshold
    if comparator == "le":
        return value <= threshold
    raise ValueError(f"Unsupported comparator: {comparator!r}")


def build_shadow_mode_rollout_checks(
    results: list[dict[str, Any]],
    *,
    source_mode: str,
) -> dict[str, Any]:
    threshold_specs = [
        ("strength_inference", "coverage_ci95", "ge", _metric_threshold("KURA_SHADOW_STRENGTH_COVERAGE_MIN", 0.7)),
        ("strength_inference", "mae", "le", _metric_threshold("KURA_SHADOW_STRENGTH_MAE_MAX", 15.0)),
        ("readiness_inference", "coverage_ci95_nowcast", "ge", _metric_threshold("KURA_SHADOW_READINESS_COVERAGE_MIN", 0.7)),
        ("readiness_inference", "mae_nowcast", "le", _metric_threshold("KURA_SHADOW_READINESS_MAE_MAX", 0.18)),
        ("semantic_memory", "top1_accuracy", "ge", _metric_threshold("KURA_SHADOW_SEMANTIC_TOP1_MIN", 0.6)),
        ("semantic_memory", "topk_recall", "ge", _metric_threshold("KURA_SHADOW_SEMANTIC_TOPK_MIN", 0.8)),
        ("causal_inference", "ok_outcome_rate", "ge", _metric_threshold("KURA_SHADOW_CAUSAL_OUTCOME_OK_MIN", 0.34)),
        ("causal_inference", "segment_ok_rate", "ge", _metric_threshold("KURA_SHADOW_CAUSAL_SEGMENT_OK_MIN", 0.2)),
        ("causal_inference", "high_severity_caveat_rate", "le", _metric_threshold("KURA_SHADOW_CAUSAL_HIGH_SEVERITY_MAX", 0.6)),
        ("causal_inference", "median_ci95_width", "le", _metric_threshold("KURA_SHADOW_CAUSAL_CI95_WIDTH_MAX", 0.35)),
    ]

    source_order = [source_mode]
    if source_mode == EVAL_SOURCE_BOTH:
        source_order = [EVAL_SOURCE_EVENT_STORE, EVAL_SOURCE_PROJECTION_HISTORY]

    checks: list[dict[str, Any]] = []
    for projection_type, metric_name, comparator, threshold in threshold_specs:
        values: list[float] = []
        used_source: str | None = None
        for source in source_order:
            source_values = _metric_values_for_source(
                results,
                projection_type=projection_type,
                metric_name=metric_name,
                source=source,
            )
            if source_values:
                values = source_values
                used_source = source
                break

        mean_value = _safe_mean(values)
        passed = (
            _compare_metric(mean_value, comparator, threshold)
            if mean_value is not None
            else False
        )
        checks.append(
            {
                "projection_type": projection_type,
                "metric": metric_name,
                "source": used_source,
                "comparator": comparator,
                "threshold": threshold,
                "value": _round_or_none(mean_value, 6),
                "samples": len(values),
                "passed": passed,
            }
        )

    if checks and all(c["passed"] for c in checks):
        status = "pass"
    elif any(c["value"] is None for c in checks):
        status = "insufficient_data"
    else:
        status = "fail"

    return {
        "status": status,
        "allow_autonomous_behavior_changes": status == "pass",
        "checks": checks,
    }


_SHADOW_DELTA_RULES = [
    {
        "projection_type": "strength_inference",
        "metric": "coverage_ci95",
        "direction": "higher_is_better",
        "max_delta": -0.03,
    },
    {
        "projection_type": "strength_inference",
        "metric": "mae",
        "direction": "lower_is_better",
        "max_delta": 1.0,
    },
    {
        "projection_type": "readiness_inference",
        "metric": "coverage_ci95_nowcast",
        "direction": "higher_is_better",
        "max_delta": -0.03,
    },
    {
        "projection_type": "readiness_inference",
        "metric": "mae_nowcast",
        "direction": "lower_is_better",
        "max_delta": 0.03,
    },
    {
        "projection_type": "semantic_memory",
        "metric": "top1_accuracy",
        "direction": "higher_is_better",
        "max_delta": -0.05,
    },
    {
        "projection_type": "semantic_memory",
        "metric": "topk_recall",
        "direction": "higher_is_better",
        "max_delta": -0.03,
    },
    {
        "projection_type": "causal_inference",
        "metric": "ok_outcome_rate",
        "direction": "higher_is_better",
        "max_delta": -0.05,
    },
    {
        "projection_type": "causal_inference",
        "metric": "high_severity_caveat_rate",
        "direction": "lower_is_better",
        "max_delta": 0.10,
    },
]
_SHADOW_RELEASE_POLICY_VERSION = "shadow_eval_gate_v1"
_SHADOW_TIER_MATRIX_POLICY_VERSION = "shadow_eval_tier_matrix_v1"
_MODEL_TIER_ORDER = {
    "strict": 0,
    "moderate": 1,
    "advanced": 2,
}
_DEFAULT_SHADOW_MODEL_TIERS = ("strict", "moderate", "advanced")
_PROOF_IN_PRODUCTION_SCHEMA_VERSION = "proof_in_production_decision_artifact.v1"
_SYNTHETIC_ADVERSARIAL_CORPUS_SCHEMA_VERSION = "synthetic_adversarial_corpus.v1"
_ADVERSARIAL_FAILURE_MODES = (
    "hallucination",
    "overconfidence",
    "retrieval_miss",
    "data_integrity_drift",
)
_ADVERSARIAL_FAILURE_RATE_DELTA_LIMITS = {
    "hallucination": 0.04,
    "overconfidence": 0.04,
    "retrieval_miss": 0.03,
    "data_integrity_drift": 0.03,
}
_ADVERSARIAL_EXPECTED_REGRET_FLOOR = {
    "hallucination": "medium",
    "overconfidence": "medium",
    "retrieval_miss": "high",
    "data_integrity_drift": "medium",
}
_ADVERSARIAL_EXPECTED_LAAJ_VERDICT = "review"
_ADVERSARIAL_MIN_SIDECAR_ALIGNMENT_RATE = 0.70
_REGRET_BAND_ORDER = {"low": 0, "medium": 1, "high": 2}
_ADVERSARIAL_SCENARIOS_PER_MODE = 50


def _aggregate_metric_mean(
    results: list[dict[str, Any]],
    *,
    projection_type: str,
    metric_name: str,
) -> tuple[float | None, int]:
    values: list[float] = []
    for row in results:
        if row.get("projection_type") != projection_type:
            continue
        if row.get("status") != "ok":
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            continue
        value = _as_float(metrics.get(metric_name))
        if value is None:
            continue
        values.append(value)
    return _safe_mean(values), len(values)


def _delta_passes(direction: str, delta_abs: float, max_delta: float) -> bool:
    if direction == "higher_is_better":
        return delta_abs >= max_delta
    if direction == "lower_is_better":
        return delta_abs <= max_delta
    raise ValueError(f"Unsupported shadow delta direction: {direction!r}")


def _normalize_model_tier(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in _MODEL_TIER_ORDER:
        return raw
    return "moderate"


def _model_tier_sort_key(tier: str) -> tuple[int, str]:
    normalized = _normalize_model_tier(tier)
    return (_MODEL_TIER_ORDER.get(normalized, len(_MODEL_TIER_ORDER)), normalized)


def _sorted_model_tiers(tiers: Iterable[str]) -> list[str]:
    return sorted({_normalize_model_tier(tier) for tier in tiers}, key=_model_tier_sort_key)


def _selected_shadow_projection_types(
    baseline_eval: dict[str, Any],
    candidate_eval: dict[str, Any],
) -> list[str]:
    return sorted(
        set(baseline_eval.get("projection_types") or [])
        | set(candidate_eval.get("projection_types") or [])
    )


def _compute_shadow_metric_deltas(
    *,
    baseline_results: list[dict[str, Any]],
    candidate_results: list[dict[str, Any]],
    selected_projection_types: list[str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    applicable_rules = [
        rule for rule in _SHADOW_DELTA_RULES if rule["projection_type"] in selected_projection_types
    ]

    metric_deltas: list[dict[str, Any]] = []
    missing_metrics: list[str] = []
    failed_metrics: list[str] = []
    for rule in applicable_rules:
        projection_type = str(rule["projection_type"])
        metric_name = str(rule["metric"])
        direction = str(rule["direction"])
        max_delta = float(rule["max_delta"])

        baseline_mean, baseline_samples = _aggregate_metric_mean(
            baseline_results,
            projection_type=projection_type,
            metric_name=metric_name,
        )
        candidate_mean, candidate_samples = _aggregate_metric_mean(
            candidate_results,
            projection_type=projection_type,
            metric_name=metric_name,
        )
        available = baseline_mean is not None and candidate_mean is not None
        delta_abs = (float(candidate_mean) - float(baseline_mean)) if available else None
        delta_pct = None
        if available and baseline_mean and baseline_mean != 0.0:
            delta_pct = (float(candidate_mean) / float(baseline_mean)) - 1.0
        passed = (
            _delta_passes(direction, float(delta_abs), max_delta)
            if available and delta_abs is not None
            else False
        )
        metric_key = f"{projection_type}:{metric_name}"
        if not available:
            missing_metrics.append(metric_key)
        elif not passed:
            failed_metrics.append(metric_key)
        metric_deltas.append(
            {
                "projection_type": projection_type,
                "metric": metric_name,
                "direction": direction,
                "max_delta": max_delta,
                "baseline_mean": _round_or_none(baseline_mean, 6),
                "candidate_mean": _round_or_none(candidate_mean, 6),
                "delta_abs": _round_or_none(delta_abs, 6),
                "delta_pct": _round_or_none(delta_pct, 6),
                "baseline_samples": baseline_samples,
                "candidate_samples": candidate_samples,
                "value_available": available,
                "passed": passed,
            }
        )

    return metric_deltas, sorted(missing_metrics), sorted(failed_metrics)


def _resolve_shadow_release_gate(
    *,
    missing_metrics: list[str],
    failed_metrics: list[str],
    candidate_shadow_status: str,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if missing_metrics:
        reasons.append(
            "insufficient_metric_coverage: " + ", ".join(sorted(missing_metrics))
        )
    if failed_metrics:
        reasons.append(
            "metric_delta_failures: " + ", ".join(sorted(failed_metrics))
        )
    if candidate_shadow_status != "pass":
        reasons.append(f"candidate_shadow_mode_status={candidate_shadow_status}")

    if missing_metrics:
        return "insufficient_data", reasons
    if failed_metrics or candidate_shadow_status != "pass":
        return "fail", reasons
    return "pass", reasons


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return None


def _normalize_adversarial_failure_mode(value: Any) -> str | None:
    mode = str(value or "").strip().lower()
    if mode in _ADVERSARIAL_FAILURE_MODES:
        return mode
    return None


def _normalize_regret_band(value: Any) -> str:
    band = str(value or "").strip().lower()
    if band in _REGRET_BAND_ORDER:
        return band
    return "unknown"


def _normalize_laaj_verdict(value: Any) -> str:
    verdict = str(value or "").strip().lower()
    if verdict in {"pass", "review"}:
        return verdict
    return "unknown"


def _adversarial_rows_from_eval(eval_output: dict[str, Any]) -> list[dict[str, Any]]:
    payload = eval_output.get("adversarial_corpus")
    if not isinstance(payload, dict):
        return []

    raw_rows = payload.get("scenarios")
    if not isinstance(raw_rows, list):
        raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        return []

    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(raw_rows):
        if not isinstance(row, dict):
            continue
        mode = _normalize_adversarial_failure_mode(row.get("failure_mode"))
        if mode is None:
            continue
        triggered = _as_bool(row.get("triggered_failure"))
        if triggered is None:
            triggered = _as_bool(row.get("triggered"))
        if triggered is None:
            continue
        rows.append(
            {
                "scenario_id": str(row.get("scenario_id") or f"{mode}_{idx + 1}"),
                "failure_mode": mode,
                "triggered_failure": triggered,
                "retrieval_regret_band": _normalize_regret_band(
                    row.get("retrieval_regret_band")
                ),
                "laaj_verdict": _normalize_laaj_verdict(row.get("laaj_verdict")),
            }
        )
    return rows


def _rate_from_rows(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    triggered = sum(1 for row in rows if bool(row.get("triggered_failure")))
    return triggered / len(rows)


def _safe_prob(value: float | None, default: float) -> float:
    if value is None:
        return default
    return _clamp(float(value), 0.0, 1.0)


def _status_failure_rate(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    failed = sum(1 for row in rows if str(row.get("status") or "") != "ok")
    return failed / len(rows)


def _shadow_check_failure_rate(eval_output: dict[str, Any]) -> float:
    checks = (eval_output.get("shadow_mode") or {}).get("checks") or []
    if not isinstance(checks, list) or not checks:
        return 0.0
    failures = sum(
        1 for check in checks if isinstance(check, dict) and not bool(check.get("passed"))
    )
    return failures / len(checks)


def _mode_failure_probabilities(eval_output: dict[str, Any]) -> dict[str, float]:
    results = list(eval_output.get("results") or [])
    strength_coverage, _ = _aggregate_metric_mean(
        results,
        projection_type="strength_inference",
        metric_name="coverage_ci95",
    )
    readiness_coverage, _ = _aggregate_metric_mean(
        results,
        projection_type="readiness_inference",
        metric_name="coverage_ci95_nowcast",
    )
    semantic_top1, semantic_top1_samples = _aggregate_metric_mean(
        results,
        projection_type="semantic_memory",
        metric_name="top1_accuracy",
    )
    semantic_topk, semantic_topk_samples = _aggregate_metric_mean(
        results,
        projection_type="semantic_memory",
        metric_name="topk_recall",
    )
    causal_high_severity, _ = _aggregate_metric_mean(
        results,
        projection_type="causal_inference",
        metric_name="high_severity_caveat_rate",
    )

    semantic_rows = [row for row in results if row.get("projection_type") == "semantic_memory"]
    semantic_status_failure = _status_failure_rate(semantic_rows)
    overall_status_failure = _status_failure_rate(results)
    shadow_check_failure = _shadow_check_failure_rate(eval_output)

    strength_cov = _safe_prob(strength_coverage, 0.78)
    readiness_cov = _safe_prob(readiness_coverage, 0.78)
    top1 = _safe_prob(semantic_top1, 0.80)
    topk = _safe_prob(semantic_topk, 0.82)
    high_severity = _safe_prob(causal_high_severity, 0.30)

    missing_semantic_penalty = 0.0 if semantic_topk_samples > 0 else 0.20
    if semantic_top1_samples == 0:
        missing_semantic_penalty += 0.10

    hallucination = _clamp((1.0 - top1) * 0.85 + semantic_status_failure * 0.15, 0.0, 1.0)
    overconfidence = _clamp(
        (1.0 - strength_cov) * 0.45
        + (1.0 - readiness_cov) * 0.35
        + high_severity * 0.20,
        0.0,
        1.0,
    )
    retrieval_miss = _clamp(
        (1.0 - topk) * 0.65
        + semantic_status_failure * 0.20
        + missing_semantic_penalty,
        0.0,
        1.0,
    )
    data_integrity_drift = _clamp(
        overall_status_failure * 0.50
        + shadow_check_failure * 0.35
        + (1.0 - min(strength_cov, readiness_cov)) * 0.15,
        0.0,
        1.0,
    )

    return {
        "hallucination": hallucination,
        "overconfidence": overconfidence,
        "retrieval_miss": retrieval_miss,
        "data_integrity_drift": data_integrity_drift,
    }


def _regret_band_for_mode(mode: str, *, triggered: bool, probability: float) -> str:
    if not triggered:
        return "low"
    if mode == "retrieval_miss":
        return "high"
    if probability >= 0.70:
        return "high"
    return "medium"


def _build_synthetic_adversarial_scenarios(eval_output: dict[str, Any]) -> list[dict[str, Any]]:
    model_tier = _normalize_model_tier(eval_output.get("model_tier"))
    mode_probs = _mode_failure_probabilities(eval_output)
    scenarios: list[dict[str, Any]] = []
    for mode in _ADVERSARIAL_FAILURE_MODES:
        probability = _safe_prob(mode_probs.get(mode), 0.0)
        total = _ADVERSARIAL_SCENARIOS_PER_MODE
        triggered_total = int(round(probability * total))
        triggered_total = max(0, min(total, triggered_total))
        for idx in range(total):
            triggered = idx < triggered_total
            scenarios.append(
                {
                    "scenario_id": f"{model_tier}.{mode}.{idx + 1:03d}",
                    "failure_mode": mode,
                    "triggered_failure": triggered,
                    "retrieval_regret_band": _regret_band_for_mode(
                        mode,
                        triggered=triggered,
                        probability=probability,
                    ),
                    "laaj_verdict": _ADVERSARIAL_EXPECTED_LAAJ_VERDICT if triggered else "pass",
                }
            )
    return scenarios


def _attach_synthetic_adversarial_corpus(eval_output: dict[str, Any]) -> dict[str, Any]:
    existing = eval_output.get("adversarial_corpus")
    if isinstance(existing, dict):
        scenarios = existing.get("scenarios")
        rows = existing.get("rows")
        if isinstance(scenarios, list) or isinstance(rows, list):
            return eval_output

    enriched = dict(eval_output)
    enriched["adversarial_corpus"] = {
        "schema_version": _SYNTHETIC_ADVERSARIAL_CORPUS_SCHEMA_VERSION,
        "generator": "shadow_eval_deterministic_v1",
        "scenarios": _build_synthetic_adversarial_scenarios(eval_output),
    }
    return enriched


def _regret_band_at_least(actual_band: str, minimum_band: str) -> bool:
    actual_rank = _REGRET_BAND_ORDER.get(actual_band, -1)
    minimum_rank = _REGRET_BAND_ORDER.get(minimum_band, -1)
    return actual_rank >= minimum_rank >= 0


def evaluate_synthetic_adversarial_corpus(
    *,
    baseline_eval: dict[str, Any],
    candidate_eval: dict[str, Any],
) -> dict[str, Any]:
    baseline_rows = _adversarial_rows_from_eval(baseline_eval)
    candidate_rows = _adversarial_rows_from_eval(candidate_eval)
    if not baseline_rows and not candidate_rows:
        return {
            "schema_version": _SYNTHETIC_ADVERSARIAL_CORPUS_SCHEMA_VERSION,
            "policy_role": "advisory_regression_gate",
            "status": "not_available",
            "required_failure_modes": list(_ADVERSARIAL_FAILURE_MODES),
            "evaluated_modes": [],
            "missing_modes": list(_ADVERSARIAL_FAILURE_MODES),
            "partial_modes": [],
            "failed_modes": [],
            "baseline_rows": 0,
            "candidate_rows": 0,
            "mode_reports": [],
            "sidecar_alignment": {
                "min_alignment_rate": _ADVERSARIAL_MIN_SIDECAR_ALIGNMENT_RATE,
                "retrieval_regret_signal_type": "retrieval_regret_observed",
                "laaj_signal_type": "laaj_sidecar_assessed",
                "expected_laaj_verdict_when_triggered": _ADVERSARIAL_EXPECTED_LAAJ_VERDICT,
            },
        }

    mode_reports: list[dict[str, Any]] = []
    evaluated_modes: list[str] = []
    missing_modes: list[str] = []
    partial_modes: list[str] = []
    failed_modes: list[str] = []
    for mode in _ADVERSARIAL_FAILURE_MODES:
        baseline_mode_rows = [row for row in baseline_rows if row["failure_mode"] == mode]
        candidate_mode_rows = [row for row in candidate_rows if row["failure_mode"] == mode]
        baseline_failure_rate = _rate_from_rows(baseline_mode_rows)
        candidate_failure_rate = _rate_from_rows(candidate_mode_rows)
        failure_rate_delta = None
        if baseline_failure_rate is not None and candidate_failure_rate is not None:
            failure_rate_delta = candidate_failure_rate - baseline_failure_rate

        max_delta = _ADVERSARIAL_FAILURE_RATE_DELTA_LIMITS[mode]
        regression_passed = (
            failure_rate_delta <= max_delta if failure_rate_delta is not None else None
        )

        candidate_triggered_rows = [
            row for row in candidate_mode_rows if bool(row.get("triggered_failure"))
        ]
        regret_alignment_rate = None
        laaj_alignment_rate = None
        if candidate_triggered_rows:
            expected_regret_floor = _ADVERSARIAL_EXPECTED_REGRET_FLOOR[mode]
            regret_alignment_hits = sum(
                1
                for row in candidate_triggered_rows
                if _regret_band_at_least(row["retrieval_regret_band"], expected_regret_floor)
            )
            laaj_alignment_hits = sum(
                1
                for row in candidate_triggered_rows
                if row["laaj_verdict"] == _ADVERSARIAL_EXPECTED_LAAJ_VERDICT
            )
            regret_alignment_rate = regret_alignment_hits / len(candidate_triggered_rows)
            laaj_alignment_rate = laaj_alignment_hits / len(candidate_triggered_rows)

        sidecar_alignment_passed = True
        if regret_alignment_rate is not None and (
            regret_alignment_rate < _ADVERSARIAL_MIN_SIDECAR_ALIGNMENT_RATE
        ):
            sidecar_alignment_passed = False
        if laaj_alignment_rate is not None and (
            laaj_alignment_rate < _ADVERSARIAL_MIN_SIDECAR_ALIGNMENT_RATE
        ):
            sidecar_alignment_passed = False

        mode_status = "pass"
        failed_reasons: list[str] = []
        if not baseline_mode_rows and not candidate_mode_rows:
            mode_status = "not_covered"
            missing_modes.append(mode)
        else:
            evaluated_modes.append(mode)
            if regression_passed is False:
                mode_status = "fail"
                failed_reasons.append("failure_rate_delta_exceeded")
            elif regression_passed is None:
                mode_status = "partial"
                partial_modes.append(mode)
                failed_reasons.append("missing_baseline_or_candidate_rows")
            if not sidecar_alignment_passed:
                mode_status = "fail"
                failed_reasons.append("sidecar_alignment_below_threshold")
            if mode_status == "fail":
                failed_modes.append(mode)

        mode_reports.append(
            {
                "failure_mode": mode,
                "status": mode_status,
                "baseline_total": len(baseline_mode_rows),
                "candidate_total": len(candidate_mode_rows),
                "baseline_failure_rate": _round_or_none(baseline_failure_rate, 6),
                "candidate_failure_rate": _round_or_none(candidate_failure_rate, 6),
                "failure_rate_delta": _round_or_none(failure_rate_delta, 6),
                "max_failure_rate_delta": max_delta,
                "regression_passed": regression_passed,
                "candidate_triggered_total": len(candidate_triggered_rows),
                "sidecar_alignment": {
                    "min_alignment_rate": _ADVERSARIAL_MIN_SIDECAR_ALIGNMENT_RATE,
                    "expected_regret_floor": _ADVERSARIAL_EXPECTED_REGRET_FLOOR[mode],
                    "expected_laaj_verdict": _ADVERSARIAL_EXPECTED_LAAJ_VERDICT,
                    "regret_alignment_rate": _round_or_none(regret_alignment_rate, 6),
                    "laaj_alignment_rate": _round_or_none(laaj_alignment_rate, 6),
                    "passed": sidecar_alignment_passed,
                },
                "failed_reasons": failed_reasons,
            }
        )

    status = "pass"
    if failed_modes:
        status = "fail"
    elif not evaluated_modes:
        status = "not_available"
    elif partial_modes:
        status = "partial"

    return {
        "schema_version": _SYNTHETIC_ADVERSARIAL_CORPUS_SCHEMA_VERSION,
        "policy_role": "advisory_regression_gate",
        "status": status,
        "required_failure_modes": list(_ADVERSARIAL_FAILURE_MODES),
        "evaluated_modes": sorted(evaluated_modes),
        "missing_modes": sorted(missing_modes),
        "partial_modes": sorted(set(partial_modes)),
        "failed_modes": sorted(set(failed_modes)),
        "baseline_rows": len(baseline_rows),
        "candidate_rows": len(candidate_rows),
        "mode_reports": mode_reports,
        "sidecar_alignment": {
            "min_alignment_rate": _ADVERSARIAL_MIN_SIDECAR_ALIGNMENT_RATE,
            "retrieval_regret_signal_type": "retrieval_regret_observed",
            "laaj_signal_type": "laaj_sidecar_assessed",
            "expected_laaj_verdict_when_triggered": _ADVERSARIAL_EXPECTED_LAAJ_VERDICT,
        },
    }


def _normalize_tier_eval_map(
    reports: dict[str, dict[str, Any]] | None,
    *,
    fallback_eval: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not reports:
        fallback_tier = _normalize_model_tier(fallback_eval.get("model_tier"))
        return {fallback_tier: fallback_eval}

    out: dict[str, dict[str, Any]] = {}
    for raw_tier, payload in reports.items():
        if not isinstance(payload, dict):
            continue
        tier = _normalize_model_tier(raw_tier)
        out[tier] = payload

    if out:
        return out

    fallback_tier = _normalize_model_tier(fallback_eval.get("model_tier"))
    return {fallback_tier: fallback_eval}


def _build_shadow_tier_matrix(
    *,
    baseline_eval: dict[str, Any],
    candidate_eval: dict[str, Any],
    baseline_tier_reports: dict[str, dict[str, Any]] | None = None,
    candidate_tier_reports: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    baseline_map = _normalize_tier_eval_map(
        baseline_tier_reports,
        fallback_eval=baseline_eval,
    )
    candidate_map = _normalize_tier_eval_map(
        candidate_tier_reports,
        fallback_eval=candidate_eval,
    )

    model_tiers = _sorted_model_tiers(set(baseline_map) | set(candidate_map))
    entries: list[dict[str, Any]] = []
    for model_tier in model_tiers:
        baseline_tier_eval = baseline_map.get(model_tier)
        candidate_tier_eval = candidate_map.get(model_tier)

        if baseline_tier_eval is None or candidate_tier_eval is None:
            missing_side = "baseline" if baseline_tier_eval is None else "candidate"
            missing_metrics = [f"tier_report_missing:{missing_side}"]
            failed_metrics: list[str] = []
            gate_status = "insufficient_data"
            reasons = [f"missing_{missing_side}_tier_report"]
            metric_deltas: list[dict[str, Any]] = []
            candidate_shadow_status = "unknown"
        else:
            selected_projection_types = _selected_shadow_projection_types(
                baseline_tier_eval,
                candidate_tier_eval,
            )
            metric_deltas, missing_metrics, failed_metrics = _compute_shadow_metric_deltas(
                baseline_results=list(baseline_tier_eval.get("results") or []),
                candidate_results=list(candidate_tier_eval.get("results") or []),
                selected_projection_types=selected_projection_types,
            )
            candidate_shadow_status = str(
                (candidate_tier_eval.get("shadow_mode") or {}).get("status") or "unknown"
            )
            gate_status, reasons = _resolve_shadow_release_gate(
                missing_metrics=missing_metrics,
                failed_metrics=failed_metrics,
                candidate_shadow_status=candidate_shadow_status,
            )

        entries.append(
            {
                "model_tier": model_tier,
                "metric_deltas": metric_deltas,
                "release_gate": {
                    "policy_version": _SHADOW_RELEASE_POLICY_VERSION,
                    "status": gate_status,
                    "allow_rollout": gate_status == "pass",
                    "missing_metrics": missing_metrics,
                    "failed_metrics": failed_metrics,
                    "candidate_shadow_mode_status": candidate_shadow_status,
                    "reasons": reasons,
                },
            }
        )

    weakest_tier = model_tiers[0] if model_tiers else None
    for entry in entries:
        entry["is_weakest"] = entry["model_tier"] == weakest_tier

    tier_statuses = [entry["release_gate"]["status"] for entry in entries]
    if entries and all(status == "pass" for status in tier_statuses):
        matrix_status = "pass"
    elif any(status == "insufficient_data" for status in tier_statuses):
        matrix_status = "insufficient_data"
    elif entries:
        matrix_status = "fail"
    else:
        matrix_status = "insufficient_data"

    return {
        "policy_version": _SHADOW_TIER_MATRIX_POLICY_VERSION,
        "status": matrix_status,
        "weakest_tier": weakest_tier,
        "tiers": entries,
    }


def _failure_class_summary(eval_output: dict[str, Any]) -> dict[str, Any]:
    projection_failures: dict[str, dict[str, int]] = {}
    for row in eval_output.get("results", []):
        projection_type = str(row.get("projection_type") or "unknown")
        status = str(row.get("status") or "unknown")
        if status == "ok":
            continue
        bucket = projection_failures.setdefault(projection_type, {})
        bucket[status] = bucket.get(status, 0) + 1

    shadow_checks = (eval_output.get("shadow_mode") or {}).get("checks") or []
    gate_failures = [
        {
            "projection_type": str(check.get("projection_type") or "unknown"),
            "metric": str(check.get("metric") or "unknown"),
            "value": check.get("value"),
            "threshold": check.get("threshold"),
            "comparator": check.get("comparator"),
            "samples": int(check.get("samples") or 0),
        }
        for check in shadow_checks
        if isinstance(check, dict) and not bool(check.get("passed"))
    ]
    return {
        "projection_status_failures": projection_failures,
        "shadow_gate_failures": gate_failures,
    }


def build_shadow_evaluation_report(
    *,
    baseline_eval: dict[str, Any],
    candidate_eval: dict[str, Any],
    change_context: dict[str, Any] | None = None,
    baseline_tier_reports: dict[str, dict[str, Any]] | None = None,
    candidate_tier_reports: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    baseline_results = list(baseline_eval.get("results") or [])
    candidate_results = list(candidate_eval.get("results") or [])

    selected_projection_types = _selected_shadow_projection_types(
        baseline_eval,
        candidate_eval,
    )
    metric_deltas, missing_metrics, failed_metrics = _compute_shadow_metric_deltas(
        baseline_results=baseline_results,
        candidate_results=candidate_results,
        selected_projection_types=selected_projection_types,
    )

    baseline_shadow_status = str((baseline_eval.get("shadow_mode") or {}).get("status") or "unknown")
    candidate_shadow_status = str((candidate_eval.get("shadow_mode") or {}).get("status") or "unknown")

    gate_status, reasons = _resolve_shadow_release_gate(
        missing_metrics=missing_metrics,
        failed_metrics=failed_metrics,
        candidate_shadow_status=candidate_shadow_status,
    )

    tier_matrix = _build_shadow_tier_matrix(
        baseline_eval=baseline_eval,
        candidate_eval=candidate_eval,
        baseline_tier_reports=baseline_tier_reports,
        candidate_tier_reports=candidate_tier_reports,
    )
    weakest_tier = tier_matrix.get("weakest_tier")
    weakest_entry = next(
        (
            entry
            for entry in tier_matrix.get("tiers", [])
            if entry.get("model_tier") == weakest_tier
        ),
        None,
    )
    if isinstance(weakest_entry, dict):
        weakest_status = str((weakest_entry.get("release_gate") or {}).get("status") or "unknown")
        if weakest_status != "pass":
            reason = f"weakest_tier_gate_status={weakest_tier}:{weakest_status}"
            if reason not in reasons:
                reasons.append(reason)
            if weakest_status == "insufficient_data":
                gate_status = "insufficient_data"
            elif gate_status != "insufficient_data":
                gate_status = "fail"

    adversarial_corpus = evaluate_synthetic_adversarial_corpus(
        baseline_eval=baseline_eval,
        candidate_eval=candidate_eval,
    )
    release_failed_metrics = sorted(set(failed_metrics))
    if adversarial_corpus["status"] == "fail":
        failed_modes = [
            str(mode) for mode in adversarial_corpus.get("failed_modes") or []
        ]
        if failed_modes:
            reason = "adversarial_failure_mode_regression: " + ", ".join(failed_modes)
            if reason not in reasons:
                reasons.append(reason)
            release_failed_metrics.extend(
                [f"adversarial_corpus:{mode}" for mode in failed_modes]
            )
        if gate_status != "insufficient_data":
            gate_status = "fail"
    release_failed_metrics = sorted(set(release_failed_metrics))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "change_context": change_context or {},
        "baseline": {
            "model_tier": _normalize_model_tier(baseline_eval.get("model_tier")),
            "source": baseline_eval.get("source"),
            "strength_engine": baseline_eval.get("strength_engine"),
            "eval_status": baseline_eval.get("eval_status"),
            "shadow_mode_status": baseline_shadow_status,
            "summary": baseline_eval.get("summary") or {},
            "summary_by_source": baseline_eval.get("summary_by_source") or {},
        },
        "candidate": {
            "model_tier": _normalize_model_tier(candidate_eval.get("model_tier")),
            "source": candidate_eval.get("source"),
            "strength_engine": candidate_eval.get("strength_engine"),
            "eval_status": candidate_eval.get("eval_status"),
            "shadow_mode_status": candidate_shadow_status,
            "summary": candidate_eval.get("summary") or {},
            "summary_by_source": candidate_eval.get("summary_by_source") or {},
        },
        "metric_deltas": metric_deltas,
        "tier_matrix": tier_matrix,
        "failure_classes": {
            "baseline": _failure_class_summary(baseline_eval),
            "candidate": _failure_class_summary(candidate_eval),
        },
        "adversarial_corpus": adversarial_corpus,
        "release_gate": {
            "policy_version": _SHADOW_RELEASE_POLICY_VERSION,
            "tier_matrix_policy_version": _SHADOW_TIER_MATRIX_POLICY_VERSION,
            "weakest_tier": weakest_tier,
            "tier_matrix_status": tier_matrix.get("status"),
            "status": gate_status,
            "allow_rollout": gate_status == "pass",
            "failed_metrics": release_failed_metrics,
            "missing_metrics": sorted(missing_metrics),
            "reasons": reasons,
        },
    }


def _proof_decision_status(gate_status: str) -> str:
    if gate_status == "pass":
        return "approve_rollout"
    if gate_status == "insufficient_data":
        return "needs_data"
    return "hold"


def _proof_missing_data(
    release_gate: dict[str, Any],
    tier_matrix: dict[str, Any],
) -> list[str]:
    missing_data: list[str] = []
    for item in release_gate.get("missing_metrics") or []:
        missing_data.append(str(item))

    for tier in tier_matrix.get("tiers") or []:
        if not isinstance(tier, dict):
            continue
        tier_name = str(tier.get("model_tier") or "unknown")
        tier_gate = tier.get("release_gate") or {}
        for item in tier_gate.get("missing_metrics") or []:
            missing_data.append(f"{tier_name}:{item}")

    return sorted(set(missing_data))


def _proof_recommended_next_steps(
    *,
    gate_status: str,
    reasons: list[str],
    missing_data: list[str],
) -> list[str]:
    steps: list[str] = []
    if gate_status == "pass":
        steps.append(
            "Proceed with controlled rollout ramp and monitor tier_matrix + release_gate status."
        )
    else:
        steps.append(
            "Block rollout, address gate findings, and rerun shadow evaluation before promotion."
        )

    if missing_data:
        steps.append(
            "Backfill missing replay coverage for: " + ", ".join(sorted(missing_data)[:5])
        )
    for reason in reasons:
        if reason.startswith("weakest_tier_gate_status="):
            payload = reason.split("=", 1)[1]
            steps.append(f"Resolve weakest-tier regressions first ({payload}).")
        elif reason.startswith("metric_delta_failures:"):
            steps.append("Fix regressed protected metrics and rerun gate.")
        elif reason.startswith("adversarial_failure_mode_regression:"):
            steps.append(
                "Reduce adversarial failure-mode regressions and improve sidecar alignment before promotion."
            )
        elif reason.startswith("candidate_shadow_mode_status="):
            steps.append("Investigate candidate shadow-mode check failures.")

    # Stable dedupe while preserving intent ordering.
    seen: set[str] = set()
    deduped: list[str] = []
    for step in steps:
        if step in seen:
            continue
        seen.add(step)
        deduped.append(step)
    return deduped


def build_proof_in_production_artifact(shadow_report: dict[str, Any]) -> dict[str, Any]:
    release_gate = shadow_report.get("release_gate") or {}
    tier_matrix = shadow_report.get("tier_matrix") or {}

    gate_status = str(release_gate.get("status") or "unknown")
    decision_status = _proof_decision_status(gate_status)
    reasons = [str(item) for item in (release_gate.get("reasons") or [])]
    missing_data = _proof_missing_data(release_gate, tier_matrix)
    recommended_next_steps = _proof_recommended_next_steps(
        gate_status=gate_status,
        reasons=reasons,
        missing_data=missing_data,
    )

    weakest_tier = str(
        release_gate.get("weakest_tier") or tier_matrix.get("weakest_tier") or "unknown"
    )
    headline = (
        f"Rollout decision: {decision_status} "
        f"(gate={gate_status}, weakest_tier={weakest_tier})"
    )

    return {
        "schema_version": _PROOF_IN_PRODUCTION_SCHEMA_VERSION,
        "generated_at": shadow_report.get("generated_at") or datetime.now(timezone.utc).isoformat(),
        "decision": {
            "status": decision_status,
            "allow_rollout": bool(release_gate.get("allow_rollout")),
            "gate_status": gate_status,
            "weakest_tier": weakest_tier,
            "tier_matrix_status": str(release_gate.get("tier_matrix_status") or "unknown"),
        },
        "gate": {
            "policy_version": str(release_gate.get("policy_version") or ""),
            "tier_matrix_policy_version": str(
                release_gate.get("tier_matrix_policy_version") or ""
            ),
            "failed_metrics": sorted(str(item) for item in (release_gate.get("failed_metrics") or [])),
            "missing_metrics": sorted(
                str(item) for item in (release_gate.get("missing_metrics") or [])
            ),
            "primary_reasons": reasons,
        },
        "missing_data": missing_data,
        "recommended_next_steps": recommended_next_steps,
        "stakeholder_summary": {
            "headline": headline,
            "decision_status": decision_status,
            "gate_status": gate_status,
            "primary_reasons": reasons[:3],
            "missing_data": missing_data[:5],
            "recommended_next_steps": recommended_next_steps[:5],
        },
    }


def render_proof_in_production_markdown(artifact: dict[str, Any]) -> str:
    decision = artifact.get("decision") or {}
    summary = artifact.get("stakeholder_summary") or {}
    lines = [
        "# Proof In Production Decision Artifact",
        "",
        f"- Decision: `{decision.get('status', 'unknown')}`",
        f"- Gate status: `{decision.get('gate_status', 'unknown')}`",
        f"- Weakest tier: `{decision.get('weakest_tier', 'unknown')}`",
        "",
        "## Headline",
        str(summary.get("headline") or ""),
        "",
        "## Primary Reasons",
    ]
    reasons = list(summary.get("primary_reasons") or [])
    if reasons:
        lines.extend([f"- {reason}" for reason in reasons])
    else:
        lines.append("- none")

    lines.extend(["", "## Missing Data"])
    missing_data = list(summary.get("missing_data") or [])
    if missing_data:
        lines.extend([f"- {item}" for item in missing_data])
    else:
        lines.append("- none")

    lines.extend(["", "## Recommended Next Steps"])
    next_steps = list(summary.get("recommended_next_steps") or [])
    if next_steps:
        lines.extend([f"- {step}" for step in next_steps])
    else:
        lines.append("- none")

    lines.append("")
    return "\n".join(lines)


def _shadow_config(
    config: dict[str, Any] | None,
    *,
    default_projection_types: list[str] | None = None,
) -> dict[str, Any]:
    raw = config or {}
    projection_types = raw.get("projection_types")
    if not isinstance(projection_types, list):
        projection_types = default_projection_types
    return {
        "projection_types": _normalize_projection_types(projection_types),
        "strength_engine": str(raw.get("strength_engine") or "closed_form"),
        "semantic_top_k": max(1, int(raw.get("semantic_top_k") or SEMANTIC_DEFAULT_TOP_K)),
        "source": _normalize_source(str(raw.get("source") or EVAL_SOURCE_BOTH)),
        "persist": bool(raw.get("persist", False)),
        "model_tier": _normalize_model_tier(raw.get("model_tier")),
    }


def _shadow_tier_variants(
    config: dict[str, Any] | None,
    *,
    default_projection_types: list[str] | None = None,
    default_model_tiers: list[str] | None = None,
) -> list[dict[str, Any]]:
    raw = config or {}
    variants: list[dict[str, Any]] = []

    tier_matrix = raw.get("tier_matrix")
    if isinstance(tier_matrix, list):
        for entry in tier_matrix:
            if not isinstance(entry, dict):
                continue
            variants.append(
                _shadow_config(
                    entry,
                    default_projection_types=default_projection_types,
                )
            )

    if not variants:
        base = _shadow_config(raw, default_projection_types=default_projection_types)
        model_tiers_raw = raw.get("model_tiers")
        if isinstance(model_tiers_raw, list) and model_tiers_raw:
            model_tiers = [_normalize_model_tier(item) for item in model_tiers_raw]
        elif default_model_tiers:
            model_tiers = [_normalize_model_tier(item) for item in default_model_tiers]
        else:
            model_tiers = [str(base["model_tier"])]
        for model_tier in model_tiers:
            variant = dict(base)
            variant["model_tier"] = model_tier
            variants.append(variant)

    deduped: dict[str, dict[str, Any]] = {}
    for variant in variants:
        normalized = dict(variant)
        normalized["model_tier"] = _normalize_model_tier(normalized.get("model_tier"))
        deduped.setdefault(str(normalized["model_tier"]), normalized)

    return [deduped[tier] for tier in _sorted_model_tiers(deduped.keys())]


def _pseudonymize_shadow_user(user_id: str) -> str:
    digest = hashlib.sha1(str(user_id).encode("utf-8")).hexdigest()[:12]
    return f"shadow_u_{digest}"


def _merge_shadow_eval_outputs(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    all_results: list[dict[str, Any]] = []
    projection_types: set[str] = set()
    user_refs: list[str] = []
    source_mode = EVAL_SOURCE_BOTH
    strength_engine = "closed_form"
    for output in outputs:
        user_id = str(output.get("user_id") or "")
        if user_id:
            user_refs.append(_pseudonymize_shadow_user(user_id))
        projection_types.update(output.get("projection_types") or [])
        source_mode = str(output.get("source") or source_mode)
        strength_engine = str(output.get("strength_engine") or strength_engine)
        for row in output.get("results") or []:
            enriched = dict(row)
            if user_id:
                enriched["user_ref"] = _pseudonymize_shadow_user(user_id)
            all_results.append(enriched)

    summary = summarize_projection_results(all_results)
    summary_by_source = summarize_projection_results_by_source(all_results)
    shadow_mode = build_shadow_mode_rollout_checks(all_results, source_mode=source_mode)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "projection_types": sorted(projection_types),
        "source": source_mode,
        "strength_engine": strength_engine,
        "user_refs": sorted(set(user_refs)),
        "projection_rows": len(all_results),
        "summary": summary,
        "summary_by_source": summary_by_source,
        "eval_status": _eval_run_status(all_results),
        "shadow_mode": shadow_mode,
        "results": all_results,
    }


async def _run_shadow_tier(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_ids: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    outputs: list[dict[str, Any]] = []
    for user_id in user_ids:
        outputs.append(
            await run_eval_harness(
                conn,
                user_id=user_id,
                projection_types=config["projection_types"],
                strength_engine=config["strength_engine"],
                semantic_top_k=int(config["semantic_top_k"]),
                source=str(config["source"]),
                persist=bool(config["persist"]),
            )
        )

    aggregate = _merge_shadow_eval_outputs(outputs)
    aggregate["model_tier"] = str(config["model_tier"])
    aggregate["config"] = {
        "projection_types": list(config["projection_types"]),
        "strength_engine": str(config["strength_engine"]),
        "semantic_top_k": int(config["semantic_top_k"]),
        "source": str(config["source"]),
        "persist": bool(config["persist"]),
        "model_tier": str(config["model_tier"]),
    }
    return aggregate


async def run_shadow_evaluation(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_ids: list[str],
    baseline_config: dict[str, Any] | None = None,
    candidate_config: dict[str, Any] | None = None,
    change_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not user_ids:
        raise ValueError("run_shadow_evaluation requires at least one user_id")

    normalized_user_ids = sorted({str(user_id) for user_id in user_ids if str(user_id).strip()})
    if not normalized_user_ids:
        raise ValueError("run_shadow_evaluation received only empty user IDs")

    baseline_variants = _shadow_tier_variants(
        baseline_config,
        default_model_tiers=list(_DEFAULT_SHADOW_MODEL_TIERS),
    )
    candidate_variants = _shadow_tier_variants(
        candidate_config,
        default_projection_types=baseline_variants[0]["projection_types"],
        default_model_tiers=[str(item["model_tier"]) for item in baseline_variants],
    )

    baseline_tier_reports: dict[str, dict[str, Any]] = {}
    for config in baseline_variants:
        baseline_tier_reports[str(config["model_tier"])] = await _run_shadow_tier(
            conn,
            user_ids=normalized_user_ids,
            config=config,
        )
    baseline_tier_reports = {
        tier: _attach_synthetic_adversarial_corpus(report)
        for tier, report in baseline_tier_reports.items()
    }

    candidate_tier_reports: dict[str, dict[str, Any]] = {}
    for config in candidate_variants:
        candidate_tier_reports[str(config["model_tier"])] = await _run_shadow_tier(
            conn,
            user_ids=normalized_user_ids,
            config=config,
        )
    candidate_tier_reports = {
        tier: _attach_synthetic_adversarial_corpus(report)
        for tier, report in candidate_tier_reports.items()
    }

    comparison_tiers = _sorted_model_tiers(set(baseline_tier_reports) | set(candidate_tier_reports))
    focus_tier = comparison_tiers[0] if comparison_tiers else _normalize_model_tier(None)

    baseline_focus = baseline_tier_reports.get(focus_tier)
    if baseline_focus is None:
        baseline_focus = next(iter(baseline_tier_reports.values()))

    candidate_focus = candidate_tier_reports.get(focus_tier)
    if candidate_focus is None:
        candidate_focus = next(iter(candidate_tier_reports.values()))

    report = build_shadow_evaluation_report(
        baseline_eval=baseline_focus,
        candidate_eval=candidate_focus,
        baseline_tier_reports=baseline_tier_reports,
        candidate_tier_reports=candidate_tier_reports,
        change_context=change_context
        or {
            "user_count": len(normalized_user_ids),
            "comparison": {
                "baseline_model_tiers": [item["model_tier"] for item in baseline_variants],
                "candidate_model_tiers": [item["model_tier"] for item in candidate_variants],
                "baseline_strength_engines": sorted(
                    {str(item["strength_engine"]) for item in baseline_variants}
                ),
                "candidate_strength_engines": sorted(
                    {str(item["strength_engine"]) for item in candidate_variants}
                ),
                "baseline_sources": sorted({str(item["source"]) for item in baseline_variants}),
                "candidate_sources": sorted({str(item["source"]) for item in candidate_variants}),
            },
        },
    )

    user_refs: set[str] = set()
    for aggregate in baseline_tier_reports.values():
        user_refs.update(str(ref) for ref in aggregate.get("user_refs") or [])

    report["corpus"] = {
        "user_count": len(normalized_user_ids),
        "user_refs": sorted(user_refs),
        "model_tiers": comparison_tiers,
    }
    report["baseline"]["projection_rows"] = int(baseline_focus.get("projection_rows") or 0)
    report["candidate"]["projection_rows"] = int(candidate_focus.get("projection_rows") or 0)
    report["proof_in_production"] = build_proof_in_production_artifact(report)
    return report


def _eval_run_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return EVAL_STATUS_FAILED
    statuses = [str(r.get("status", "")) for r in results]
    if statuses and all(s == "ok" for s in statuses):
        return EVAL_STATUS_OK
    if any(s == "ok" for s in statuses):
        return EVAL_STATUS_PARTIAL
    return EVAL_STATUS_FAILED


async def persist_eval_run(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    source: str,
    projection_types: list[str],
    strength_engine: str,
    summary: dict[str, Any],
    summary_by_source: dict[str, Any],
    shadow_mode: dict[str, Any],
    results: list[dict[str, Any]],
) -> str:
    persisted_source = "combined" if source == EVAL_SOURCE_BOTH else source
    status = "completed" if _eval_run_status(results) != EVAL_STATUS_FAILED else "failed"
    config = {
        "source": source,
        "projection_types": projection_types,
        "strength_engine": strength_engine,
    }

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            INSERT INTO inference_eval_runs (
                user_id, source, projection_types, strength_engine, status,
                config, summary
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id,
                persisted_source,
                Json(projection_types),
                strength_engine,
                status,
                Json(config),
                Json(
                    {
                        "by_projection_type": summary,
                        "by_source": summary_by_source,
                        "shadow_mode": shadow_mode,
                    }
                ),
            ),
        )
        run_row = await cur.fetchone()
    run_id = str(run_row["id"])

    if results:
        async with conn.cursor() as cur:
            for result in results:
                projection_type = str(result.get("projection_type") or "unknown")
                projection_key = str(result.get("key") or "unknown")
                artifact_source = str(result.get("source") or "unknown")
                await cur.execute(
                    """
                    INSERT INTO inference_eval_artifacts (
                        run_id, user_id, source, projection_type, projection_key, artifact
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        user_id,
                        artifact_source,
                        projection_type,
                        projection_key,
                        Json(result),
                    ),
                )

    return run_id


async def _fetch_projection_rows(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_types: list[str],
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT projection_type, key, data, updated_at
            FROM projections
            WHERE user_id = %s
              AND projection_type = ANY(%s)
            ORDER BY projection_type ASC, key ASC
            """,
            (user_id, projection_types),
        )
        return await cur.fetchall()


async def _fetch_semantic_catalog_embeddings(
    conn: psycopg.AsyncConnection[Any],
    *,
    domain: str = "exercise",
) -> list[dict[str, Any]]:
    try:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT DISTINCT ON (c.canonical_key)
                    c.canonical_key,
                    c.canonical_label,
                    ce.embedding
                FROM semantic_catalog c
                JOIN semantic_catalog_embeddings ce ON ce.catalog_id = c.id
                WHERE c.domain = %s
                ORDER BY c.canonical_key, ce.updated_at DESC, ce.created_at DESC
                """,
                (domain,),
            )
            rows = await cur.fetchall()
    except Exception as exc:
        logger.warning("Semantic catalog embeddings unavailable for eval harness: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        vec = _parse_embedding(row["embedding"])
        if not vec:
            continue
        out.append(
            {
                "canonical_key": str(row["canonical_key"]),
                "canonical_label": str(row["canonical_label"]),
                "embedding": vec,
            }
        )
    return out


async def _fetch_active_semantic_label_rows(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
) -> list[dict[str, Any]]:
    event_types = ["set.logged", "exercise.alias_created", "event.retracted"]
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id, event_types),
        )
        rows = await cur.fetchall()

    retracted_ids = await get_retracted_event_ids(conn, user_id)
    filtered_rows = [r for r in rows if str(r["id"]) not in retracted_ids]
    return filter_retracted_event_rows(filtered_rows)


async def _projection_history_results(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_types: list[str],
    strength_engine: str,
    semantic_labels: dict[str, str] | None = None,
    semantic_top_k: int = SEMANTIC_DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    rows = await _fetch_projection_rows(conn, user_id=user_id, projection_types=projection_types)
    results: list[dict[str, Any]] = []
    for row in rows:
        projection_type = row["projection_type"]
        key = row["key"]
        data = row["data"] or {}
        if projection_type == "strength_inference":
            eval_result = evaluate_strength_history(
                key,
                data.get("history"),
                strength_engine=strength_engine,
            )
        elif projection_type == "readiness_inference":
            eval_result = evaluate_readiness_daily_scores(
                key,
                data.get("daily_scores"),
            )
        elif projection_type == "semantic_memory":
            eval_result = evaluate_semantic_memory_projection_labels(
                key,
                data,
                semantic_labels or {},
                top_k=semantic_top_k,
            )
        elif projection_type == "causal_inference":
            eval_result = evaluate_causal_projection(key, data)
        else:
            continue
        eval_result["updated_at"] = (
            row["updated_at"].isoformat()
            if hasattr(row["updated_at"], "isoformat")
            else str(row["updated_at"])
        )
        eval_result["source"] = EVAL_SOURCE_PROJECTION_HISTORY
        results.append(eval_result)
    return results


async def _event_store_results(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_types: list[str],
    strength_engine: str,
    semantic_top_k: int = SEMANTIC_DEFAULT_TOP_K,
    semantic_catalog_embeddings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    event_types: set[str] = set()
    if "semantic_memory" in projection_types:
        event_types.update(("set.logged", "exercise.alias_created", "event.retracted"))
    if "strength_inference" in projection_types:
        event_types.update(("set.logged", "exercise.alias_created", "event.retracted"))
    if "readiness_inference" in projection_types:
        event_types.update(
            ("set.logged", "sleep.logged", "soreness.logged", "energy.logged", "event.retracted")
        )
    if "causal_inference" in projection_types:
        event_types.update(
            (
                "set.logged",
                "sleep.logged",
                "soreness.logged",
                "energy.logged",
                "meal.logged",
                "nutrition_target.set",
                "sleep_target.set",
                "program.started",
                "training_plan.created",
                "training_plan.updated",
                "training_plan.archived",
                "exercise.alias_created",
                "event.retracted",
            )
        )
    if not event_types:
        return []

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id, sorted(event_types)),
        )
        rows = await cur.fetchall()

    # DB-derived retraction set to mirror handler behavior.
    retracted_ids = await get_retracted_event_ids(conn, user_id)
    filtered_rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    # Build alias map from active events for retroactive canonical resolution.
    alias_map = await get_alias_map(conn, user_id, retracted_ids=retracted_ids)
    active_rows = filter_retracted_event_rows(filtered_rows)

    results: list[dict[str, Any]] = []
    if "strength_inference" in projection_types:
        histories = build_strength_histories_from_event_rows(active_rows, alias_map)
        for key in sorted(histories):
            eval_result = evaluate_strength_history(
                key,
                histories[key],
                strength_engine=strength_engine,
            )
            eval_result["source"] = EVAL_SOURCE_EVENT_STORE
            results.append(eval_result)

    if "readiness_inference" in projection_types:
        readiness_daily = build_readiness_daily_scores_from_event_rows(active_rows)
        eval_result = evaluate_readiness_daily_scores("overview", readiness_daily)
        eval_result["source"] = EVAL_SOURCE_EVENT_STORE
        results.append(eval_result)

    if "causal_inference" in projection_types:
        causal_projection = build_causal_projection_from_event_rows(active_rows)
        causal_result = evaluate_causal_projection("overview", causal_projection)
        causal_result["source"] = EVAL_SOURCE_EVENT_STORE
        results.append(causal_result)

    if "semantic_memory" in projection_types:
        labels = build_semantic_labels_from_event_rows(active_rows)
        semantic_result = evaluate_semantic_event_store_labels(
            labels,
            semantic_catalog_embeddings or [],
            top_k=semantic_top_k,
        )
        semantic_result["source"] = EVAL_SOURCE_EVENT_STORE
        results.append(semantic_result)

    return results


async def run_eval_harness(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_types: list[str] | None = None,
    strength_engine: str = "closed_form",
    semantic_top_k: int = SEMANTIC_DEFAULT_TOP_K,
    source: str = EVAL_SOURCE_PROJECTION_HISTORY,
    persist: bool = False,
) -> dict[str, Any]:
    selected = _normalize_projection_types(projection_types)
    source_mode = _normalize_source(source)
    semantic_top_k = max(1, int(semantic_top_k))

    semantic_labels: dict[str, str] = {}
    semantic_catalog_embeddings: list[dict[str, Any]] = []
    if "semantic_memory" in selected:
        semantic_rows = await _fetch_active_semantic_label_rows(conn, user_id=user_id)
        semantic_labels = build_semantic_labels_from_event_rows(semantic_rows)
        if source_mode in {EVAL_SOURCE_EVENT_STORE, EVAL_SOURCE_BOTH}:
            semantic_catalog_embeddings = await _fetch_semantic_catalog_embeddings(
                conn,
                domain="exercise",
            )

    results: list[dict[str, Any]] = []
    if source_mode in {EVAL_SOURCE_PROJECTION_HISTORY, EVAL_SOURCE_BOTH}:
        results.extend(
            await _projection_history_results(
                conn,
                user_id=user_id,
                projection_types=selected,
                strength_engine=strength_engine,
                semantic_labels=semantic_labels,
                semantic_top_k=semantic_top_k,
            )
        )
    if source_mode in {EVAL_SOURCE_EVENT_STORE, EVAL_SOURCE_BOTH}:
        results.extend(
            await _event_store_results(
                conn,
                user_id=user_id,
                projection_types=selected,
                strength_engine=strength_engine,
                semantic_top_k=semantic_top_k,
                semantic_catalog_embeddings=semantic_catalog_embeddings,
            )
        )

    summary = summarize_projection_results(results)
    summary_by_source = summarize_projection_results_by_source(results)
    shadow_mode = build_shadow_mode_rollout_checks(results, source_mode=source_mode)
    output: dict[str, Any] = {
        "user_id": user_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "projection_types": selected,
        "source": source_mode,
        "strength_engine": strength_engine,
        "semantic_top_k": semantic_top_k,
        "projection_rows": len(results),
        "summary": summary,
        "summary_by_source": summary_by_source,
        "eval_status": _eval_run_status(results),
        "shadow_mode": shadow_mode,
        "results": results,
    }

    if persist:
        run_id = await persist_eval_run(
            conn,
            user_id=user_id,
            source=source_mode,
            projection_types=selected,
            strength_engine=strength_engine,
            summary=summary,
            summary_by_source=summary_by_source,
            shadow_mode=shadow_mode,
            results=results,
        )
        output["run_id"] = run_id

    return output
