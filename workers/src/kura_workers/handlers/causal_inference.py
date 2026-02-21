"""Causal inference projection handler.

Builds observational intervention-effect estimates for:
- program changes,
- nutrition shifts,
- sleep interventions.

The projection uses propensity-style adjustment with uncertainty intervals and
machine-readable caveats for transparent agent decisions.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..causal_inference import ASSUMPTIONS, estimate_intervention_effect
from ..causal_estimand_registry_v2 import (
    CAUSAL_ESTIMAND_REGISTRY_SCHEMA_VERSION,
    build_estimand_identity_v2,
    resolve_estimand_spec_v2,
)
from ..inference_engine import weekly_phase_from_date
from ..inference_event_registry import CAUSAL_SIGNAL_EVENT_TYPES
from ..inference_telemetry import (
    INFERENCE_ERROR_INSUFFICIENT_DATA,
    classify_inference_error,
    safe_record_inference_run,
)
from ..population_priors import build_causal_estimand_target_key, resolve_population_prior
from ..readiness_signals import build_readiness_daily_scores
from ..registry import projection_handler
from ..training_signal_normalization import normalize_training_signal_rows
from ..utils import (
    epley_1rm,
    get_retracted_event_ids,
    load_timezone_preference,
    normalize_temporal_point,
    resolve_exercise_key,
    resolve_timezone_context,
    resolve_through_aliases,
)

logger = logging.getLogger(__name__)

OUTCOME_READINESS = "readiness_score_t_plus_1"
OUTCOME_STRENGTH_AGGREGATE = "strength_aggregate_delta_t_plus_1"
OUTCOME_STRENGTH_PER_EXERCISE = "strength_delta_by_exercise_t_plus_1"
OBJECTIVE_MODES = {"journal", "collaborate", "coach"}
KNOWN_MODALITIES = {
    "running",
    "cycling",
    "rowing",
    "swimming",
    "strength",
    "hybrid",
    "team_sport",
    "endurance",
    "unknown",
}
POSITIVITY_ALERT_CODES = {
    "weak_overlap",
    "extreme_weights",
    "low_effective_sample_size",
    "positivity_violation",
}
SUPPLEMENT_EVIDENCE_OVERLAY_SCHEMA_VERSION = "supplement_evidence_overlay.v1"
SUPPLEMENT_EVIDENCE_DISCLOSURE = (
    "Observational evidence tier only. This is not a randomized or blinded efficacy proof."
)


def _median(values: list[float]) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _mean(values: list[float], *, fallback: float = 0.0) -> float:
    if not values:
        return fallback
    return sum(values) / len(values)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _index_supplement_daily_status(data: dict[str, Any]) -> dict[date, dict[str, Any]]:
    """Index supplements daily_status rows by date for causal context joins."""
    rows = data.get("daily_status")
    if not isinstance(rows, list):
        return {}
    indexed: dict[date, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day_raw = row.get("date")
        if not isinstance(day_raw, str):
            continue
        try:
            local_day = date.fromisoformat(day_raw)
        except ValueError:
            continue
        indexed[local_day] = row
    return indexed


def _supplement_adherence_intervention_flag(
    *,
    current_adherence: float,
    baseline_adherence: float,
    expected_count: int,
) -> int:
    if expected_count <= 0:
        return 0
    threshold = max(0.8, baseline_adherence + 0.1)
    return 1 if current_adherence >= threshold else 0


def _sample_date_key(sample: dict[str, Any]) -> date | None:
    raw = sample.get("date")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _build_placebo_lead_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Negative-control design: tomorrow's treatment should not explain today's outcome."""
    dated_samples = [sample for sample in samples if _sample_date_key(sample) is not None]
    if len(dated_samples) < 3:
        return []

    ordered = sorted(dated_samples, key=lambda sample: _sample_date_key(sample) or date.min)
    placebo_samples: list[dict[str, Any]] = []
    for idx in range(len(ordered) - 1):
        current = ordered[idx]
        future = ordered[idx + 1]
        placebo_samples.append(
            {
                "treated": int(_safe_float(future.get("treated"), default=0.0) >= 0.5),
                "outcome": _safe_float(current.get("outcome"), default=0.0),
                "confounders": dict(current.get("confounders") or {}),
            }
        )
    return placebo_samples


def _supplement_explicit_confirmation_rate(samples: list[dict[str, Any]]) -> float | None:
    explicit_total = 0
    assumed_total = 0
    for sample in samples:
        expected_count = _safe_int(sample.get("supplement_expected_count"), default=0)
        if expected_count <= 0:
            continue
        explicit_total += max(
            0,
            _safe_int(sample.get("supplement_taken_explicit_count"), default=0),
        )
        assumed_total += max(
            0,
            _safe_int(sample.get("supplement_taken_assumed_count"), default=0),
        )

    denominator = explicit_total + assumed_total
    if denominator <= 0:
        return None
    return explicit_total / denominator


def _build_supplement_evidence_overlay(
    readiness_samples: list[dict[str, Any]],
    readiness_result: dict[str, Any],
    *,
    min_samples: int,
    bootstrap_samples: int,
    placebo_result_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observed_samples = len(readiness_samples)
    treated_samples = sum(
        1 for sample in readiness_samples if int(_safe_float(sample.get("treated"), default=0.0)) == 1
    )
    control_samples = max(0, observed_samples - treated_samples)

    diagnostics = readiness_result.get("diagnostics") if isinstance(readiness_result, dict) else {}
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    overlap = diagnostics.get("overlap")
    overlap_width = _safe_float(
        overlap.get("overlap_width") if isinstance(overlap, dict) else None,
        default=0.0,
    )

    ci95 = (
        readiness_result.get("effect", {}).get("ci95")
        if isinstance(readiness_result, dict) and isinstance(readiness_result.get("effect"), dict)
        else None
    )
    ci95_width = None
    if isinstance(ci95, (list, tuple)) and len(ci95) == 2:
        lower = _safe_float(ci95[0], default=float("nan"))
        upper = _safe_float(ci95[1], default=float("nan"))
        if math.isfinite(lower) and math.isfinite(upper) and upper > lower:
            ci95_width = upper - lower

    explicit_rate = _supplement_explicit_confirmation_rate(readiness_samples)

    placebo_samples = _build_placebo_lead_samples(readiness_samples)
    if placebo_result_override is not None:
        placebo_result = placebo_result_override
    elif placebo_samples:
        placebo_result = _estimate_effect(
            placebo_samples,
            min_samples=max(16, min_samples // 2),
            bootstrap_samples=max(80, bootstrap_samples // 2),
        )
    else:
        placebo_result = {
            "status": "insufficient_data",
            "effect": None,
            "diagnostics": {"observed_samples": len(placebo_samples)},
            "caveats": [],
        }

    placebo_effect = placebo_result.get("effect") if isinstance(placebo_result, dict) else None
    placebo_ci = placebo_effect.get("ci95") if isinstance(placebo_effect, dict) else None
    placebo_mean = _safe_float(
        placebo_effect.get("mean_ate") if isinstance(placebo_effect, dict) else None,
        default=0.0,
    )
    placebo_flags_signal = False
    if isinstance(placebo_ci, (list, tuple)) and len(placebo_ci) == 2:
        placebo_lower = _safe_float(placebo_ci[0], default=0.0)
        placebo_upper = _safe_float(placebo_ci[1], default=0.0)
        placebo_flags_signal = (
            (placebo_lower > 0.0 or placebo_upper < 0.0)
            and abs(placebo_mean) >= 0.02
        )

    checks: list[dict[str, Any]] = []

    sample_support_strong = (
        observed_samples >= 84 and treated_samples >= 21 and control_samples >= 21
    )
    sample_support_suggestive = (
        observed_samples >= 48 and treated_samples >= 12 and control_samples >= 12
    )
    checks.append(
        {
            "check": "sample_support",
            "strong_pass": sample_support_strong,
            "suggestive_pass": sample_support_suggestive,
            "details": {
                "observed_samples": observed_samples,
                "treated_samples": treated_samples,
                "control_samples": control_samples,
                "thresholds": {
                    "strong": {"observed": 84, "treated": 21, "control": 21},
                    "suggestive": {"observed": 48, "treated": 12, "control": 12},
                },
            },
        }
    )

    overlap_strong = overlap_width >= 0.25
    overlap_suggestive = overlap_width >= 0.15
    checks.append(
        {
            "check": "overlap",
            "strong_pass": overlap_strong,
            "suggestive_pass": overlap_suggestive,
            "details": {
                "overlap_width": round(overlap_width, 4),
                "thresholds": {"strong": 0.25, "suggestive": 0.15},
            },
        }
    )

    precision_strong = ci95_width is not None and ci95_width <= 0.12
    precision_suggestive = ci95_width is not None and ci95_width <= 0.2
    checks.append(
        {
            "check": "precision",
            "strong_pass": precision_strong,
            "suggestive_pass": precision_suggestive,
            "details": {
                "ci95_width": round(ci95_width, 4) if ci95_width is not None else None,
                "thresholds": {"strong_max": 0.12, "suggestive_max": 0.2},
            },
        }
    )

    explicit_strong = explicit_rate is not None and explicit_rate >= 0.6
    explicit_suggestive = explicit_rate is not None and explicit_rate >= 0.4
    checks.append(
        {
            "check": "explicit_confirmation",
            "strong_pass": explicit_strong,
            "suggestive_pass": explicit_suggestive,
            "details": {
                "explicit_rate": round(explicit_rate, 4) if explicit_rate is not None else None,
                "thresholds": {"strong_min": 0.6, "suggestive_min": 0.4},
            },
        }
    )

    placebo_pass = not placebo_flags_signal
    checks.append(
        {
            "check": "placebo_lead_sanity",
            "strong_pass": placebo_pass,
            "suggestive_pass": placebo_pass,
            "details": {
                "status": str(placebo_result.get("status") or "unknown")
                if isinstance(placebo_result, dict)
                else "unknown",
                "mean_ate": round(placebo_mean, 4) if isinstance(placebo_result, dict) else None,
                "ci95": placebo_ci if isinstance(placebo_ci, (list, tuple)) else None,
                "flagged_signal": placebo_flags_signal,
                "rule": "fail when lead placebo ci95 excludes 0 and |mean_ate| >= 0.02",
            },
        }
    )

    strong_passed = sum(1 for check in checks if bool(check.get("strong_pass")))
    suggestive_passed = sum(1 for check in checks if bool(check.get("suggestive_pass")))
    total_checks = len(checks)

    if strong_passed == total_checks:
        tier = "strong_observational"
    elif suggestive_passed >= 4 and bool(readiness_result.get("status") == "ok"):
        tier = "suggestive"
    else:
        tier = "exploratory"

    return {
        "schema_version": SUPPLEMENT_EVIDENCE_OVERLAY_SCHEMA_VERSION,
        "policy_role": "advisory_only",
        "tier": tier,
        "summary": {
            "strong_checks_passed": strong_passed,
            "suggestive_checks_passed": suggestive_passed,
            "total_checks": total_checks,
        },
        "checks": checks,
        "disclosure": SUPPLEMENT_EVIDENCE_DISCLOSURE,
    }


def _normalize_objective_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in OBJECTIVE_MODES:
        return mode
    return "unknown"


def _normalize_modality(value: Any) -> str:
    modality = str(value or "").strip().lower()
    if not modality:
        return "unknown"
    if modality in KNOWN_MODALITIES:
        return modality
    if modality in {"bodybuilding", "powerlifting"}:
        return "strength"
    if modality in {"crossfit", "mixed"}:
        return "hybrid"
    return "unknown"


async def _resolve_objective_context(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
) -> tuple[str, str]:
    objective_mode = "unknown"
    modality = "unknown"

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT projection_type, data
            FROM projections
            WHERE user_id = %s
              AND (
                (projection_type = 'objective_state' AND key = 'active')
                OR (projection_type = 'user_profile' AND key = 'me')
              )
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    for row in rows:
        projection_type = str(row.get("projection_type") or "")
        data = row.get("data")
        if not isinstance(data, dict):
            continue

        if projection_type == "objective_state":
            active_objective = data.get("active_objective")
            if isinstance(active_objective, dict):
                objective_mode = _normalize_objective_mode(
                    active_objective.get("mode") or objective_mode
                )
                primary_goal = active_objective.get("primary_goal")
                if isinstance(primary_goal, dict):
                    modality = _normalize_modality(
                        primary_goal.get("modality")
                        or primary_goal.get("sport")
                        or primary_goal.get("discipline")
                        or modality
                    )
        elif projection_type == "user_profile":
            user = data.get("user")
            if not isinstance(user, dict):
                continue
            profile = user.get("profile")
            if isinstance(profile, dict):
                modality = _normalize_modality(
                    profile.get("training_modality") or modality
                )
            if objective_mode == "unknown":
                objectives = user.get("objectives")
                if isinstance(objectives, list):
                    for objective_event in reversed(objectives):
                        if not isinstance(objective_event, dict):
                            continue
                        objective_mode = _normalize_objective_mode(
                            objective_event.get("mode")
                        )
                        if objective_mode != "unknown":
                            break
            if objective_mode == "unknown":
                workflow_state = user.get("workflow_state")
                if isinstance(workflow_state, dict):
                    objective_mode = _normalize_objective_mode(workflow_state.get("mode"))

    return objective_mode, modality


def _attach_estimand_contract(
    result: dict[str, Any],
    *,
    intervention: str,
    outcome: str,
    objective_mode: str,
    modality: str,
    exercise_id: str | None = None,
) -> dict[str, Any]:
    updated = dict(result)
    estimand_spec = resolve_estimand_spec_v2(intervention, outcome)
    identity = build_estimand_identity_v2(
        intervention=intervention,
        outcome=outcome,
        objective_mode=objective_mode,
        modality=modality,
        exercise_id=exercise_id,
    )
    updated["estimand_identity"] = {
        "schema_version": CAUSAL_ESTIMAND_REGISTRY_SCHEMA_VERSION,
        "identity": identity,
        "type": estimand_spec.get("estimand_type"),
        "confounders": estimand_spec.get("confounders"),
        "required_diagnostics": estimand_spec.get("required_diagnostics"),
        "notes": estimand_spec.get("notes"),
    }

    diagnostics = dict(updated.get("diagnostics") or {})
    caveats = updated.get("caveats")
    positivity_alerts: list[str] = []
    if isinstance(caveats, list):
        positivity_alerts = sorted(
            {
                str(caveat.get("code") or "")
                for caveat in caveats
                if isinstance(caveat, dict)
                and str(caveat.get("code") or "") in POSITIVITY_ALERT_CODES
            }
        )
    diagnostics["estimand_identity"] = identity
    diagnostics["required_confounders"] = list(estimand_spec.get("confounders") or [])
    diagnostics["required_diagnostics"] = list(
        estimand_spec.get("required_diagnostics") or []
    )
    diagnostics["positivity_alerts"] = positivity_alerts
    updated["diagnostics"] = diagnostics
    return updated


def _map_effect_strength(effect_payload: Any) -> float:
    if not isinstance(effect_payload, dict):
        return 0.0
    return _safe_float(effect_payload.get("mean_ate"), default=0.0)


def _normal_cdf(value: float, *, mu: float = 0.0, sigma: float = 1.0) -> float:
    sigma = max(1e-9, sigma)
    z = (value - mu) / (sigma * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))


def _effect_variance(result: dict[str, Any]) -> float | None:
    diagnostics = result.get("diagnostics")
    if isinstance(diagnostics, dict):
        effect_sd = _safe_float(diagnostics.get("effect_sd"), default=-1.0)
        if effect_sd > 0.0:
            return max(1e-6, effect_sd * effect_sd)

    effect = result.get("effect")
    if not isinstance(effect, dict):
        return None
    ci95 = effect.get("ci95")
    if not isinstance(ci95, (list, tuple)) or len(ci95) != 2:
        return None
    lower = _safe_float(ci95[0], default=float("nan"))
    upper = _safe_float(ci95[1], default=float("nan"))
    if not math.isfinite(lower) or not math.isfinite(upper) or upper <= lower:
        return None
    sd = (upper - lower) / 3.92
    return max(1e-6, sd * sd)


def _attach_population_prior_diagnostics(
    result: dict[str, Any],
    prior_meta: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(result)
    diagnostics = dict(updated.get("diagnostics") or {})
    diagnostics["population_prior"] = prior_meta
    updated["diagnostics"] = diagnostics
    updated["population_prior"] = prior_meta
    return updated


def _blend_population_prior_into_effect(
    result: dict[str, Any],
    *,
    target_key: str,
    population_prior: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if result.get("status") != "ok":
        meta = {
            "attempted": False,
            "applied": False,
            "target_key": target_key,
            "reason": "outcome_not_ok",
        }
        return _attach_population_prior_diagnostics(result, meta), meta

    effect = result.get("effect")
    if not isinstance(effect, dict):
        meta = {
            "attempted": False,
            "applied": False,
            "target_key": target_key,
            "reason": "missing_effect",
        }
        return _attach_population_prior_diagnostics(result, meta), meta

    local_mean = _safe_float(effect.get("mean_ate"), default=float("nan"))
    local_var = _effect_variance(result)
    if not math.isfinite(local_mean) or local_var is None or local_var <= 0.0:
        meta = {
            "attempted": False,
            "applied": False,
            "target_key": target_key,
            "reason": "invalid_local_estimate",
        }
        return _attach_population_prior_diagnostics(result, meta), meta

    if not isinstance(population_prior, dict):
        meta = {
            "attempted": True,
            "applied": False,
            "target_key": target_key,
            "reason": "prior_unavailable_or_invalid",
        }
        return _attach_population_prior_diagnostics(result, meta), meta

    prior_mean = _safe_float(population_prior.get("mean"), default=float("nan"))
    prior_var = _safe_float(population_prior.get("var"), default=float("nan"))
    blend_weight = _safe_float(population_prior.get("blend_weight"), default=0.35)
    blend_weight = _clamp(blend_weight, 0.0, 0.95)
    if (
        not math.isfinite(prior_mean)
        or not math.isfinite(prior_var)
        or prior_var <= 0.0
    ):
        meta = {
            "attempted": True,
            "applied": False,
            "target_key": target_key,
            "reason": "invalid_prior_stats",
        }
        return _attach_population_prior_diagnostics(result, meta), meta

    blended_mean = ((1.0 - blend_weight) * local_mean) + (blend_weight * prior_mean)
    blended_var = ((1.0 - blend_weight) * local_var) + (blend_weight * prior_var)
    blended_var = max(1e-6, blended_var)
    blended_sd = math.sqrt(blended_var)
    delta = 1.96 * blended_sd
    ci95 = [blended_mean - delta, blended_mean + delta]
    probability_positive = _normal_cdf(blended_mean, sigma=blended_sd)
    direction = "uncertain"
    if ci95[0] > 0.0:
        direction = "positive"
    elif ci95[1] < 0.0:
        direction = "negative"

    updated_effect = dict(effect)
    updated_effect["mean_ate"] = round(blended_mean, 4)
    updated_effect["ci95"] = [round(ci95[0], 4), round(ci95[1], 4)]
    updated_effect["direction"] = direction
    updated_effect["probability_positive"] = round(probability_positive, 4)

    meta = {
        "attempted": True,
        "applied": True,
        "target_key": str(population_prior.get("target_key") or target_key),
        "cohort_key": population_prior.get("cohort_key"),
        "blend_weight": round(blend_weight, 4),
        "participants_count": population_prior.get("participants_count"),
        "sample_size": population_prior.get("sample_size"),
        "computed_at": population_prior.get("computed_at"),
        "local_mean_ate": round(local_mean, 4),
        "local_var": round(local_var, 6),
        "prior_mean": round(prior_mean, 4),
        "prior_var": round(prior_var, 6),
        "blended_mean_ate": round(blended_mean, 4),
        "blended_var": round(blended_var, 6),
    }

    updated = dict(result)
    updated["effect"] = updated_effect
    return _attach_population_prior_diagnostics(updated, meta), meta


def _estimate_effect(
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


def _ensure_segment_guardrail_caveat(
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


def _estimate_segment_slices(
    samples: list[dict[str, Any]],
    *,
    segment_key: str,
    min_samples: int,
    bootstrap_samples: int,
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        segment_label = str(sample.get(segment_key) or "unknown")
        buckets[segment_label].append(sample)

    results: dict[str, dict[str, Any]] = {}
    for segment_label, rows in sorted(buckets.items(), key=lambda item: item[0]):
        base_rows = [
            {
                "treated": row.get("treated"),
                "outcome": row.get("outcome"),
                "confounders": row.get("confounders", {}),
            }
            for row in rows
        ]
        segment_result = _estimate_effect(
            base_rows,
            min_samples=min_samples,
            bootstrap_samples=bootstrap_samples,
        )
        _ensure_segment_guardrail_caveat(
            segment_result,
            observed_samples=len(base_rows),
            min_samples=min_samples,
            segment_type=segment_key,
            segment_label=segment_label,
        )
        diagnostics = dict(segment_result.get("diagnostics") or {})
        diagnostics["segment_type"] = segment_key
        diagnostics["segment_label"] = segment_label
        diagnostics["segment_samples"] = len(base_rows)
        segment_result["diagnostics"] = diagnostics
        results[segment_label] = segment_result
    return results


def _append_result_caveats(
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


def _phase_bucket(value: str | None) -> str:
    phase = (value or "unknown").strip().lower()
    if not phase:
        return "unknown"
    return phase


def _subgroup_bucket(readiness_score: float) -> str:
    return "low_readiness" if readiness_score < 0.55 else "high_readiness"


def _round_strength_map(values: dict[str, float]) -> dict[str, float]:
    return {key: round(float(val), 2) for key, val in sorted(values.items())}


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not projection_rows:
        return {}
    data = projection_rows[0]["data"] or {}
    interventions = data.get("interventions", {})
    available = [name for name, result in interventions.items() if result.get("status") == "ok"]
    result: dict[str, Any] = {
        "interventions_modeled": available,
        "insight_count": len(available),
    }

    strongest_name: str | None = None
    strongest_value = 0.0
    strongest_outcome: str | None = None
    strongest_exercise: str | None = None
    for name, payload in interventions.items():
        effect = payload.get("effect") or {}
        mean_ate = _map_effect_strength(effect)
        if abs(mean_ate) > abs(strongest_value):
            strongest_name = name
            strongest_value = mean_ate
            strongest_outcome = OUTCOME_READINESS
            strongest_exercise = None

        outcomes = payload.get("outcomes") or {}
        agg_payload = outcomes.get(OUTCOME_STRENGTH_AGGREGATE) or {}
        agg_effect = _map_effect_strength((agg_payload or {}).get("effect"))
        if abs(agg_effect) > abs(strongest_value):
            strongest_name = name
            strongest_value = agg_effect
            strongest_outcome = OUTCOME_STRENGTH_AGGREGATE
            strongest_exercise = None

        per_exercise = outcomes.get(OUTCOME_STRENGTH_PER_EXERCISE) or {}
        if isinstance(per_exercise, dict):
            for exercise_id, exercise_payload in per_exercise.items():
                exercise_effect = _map_effect_strength((exercise_payload or {}).get("effect"))
                if abs(exercise_effect) > abs(strongest_value):
                    strongest_name = name
                    strongest_value = exercise_effect
                    strongest_outcome = OUTCOME_STRENGTH_PER_EXERCISE
                    strongest_exercise = str(exercise_id)
    if strongest_name is not None:
        result["strongest_signal"] = {
            "intervention": strongest_name,
            "mean_ate": round(strongest_value, 4),
        }
        if strongest_outcome is not None:
            result["strongest_signal"]["outcome"] = strongest_outcome
        if strongest_exercise is not None:
            result["strongest_signal"]["exercise_id"] = strongest_exercise
    return result


@projection_handler(
    *CAUSAL_SIGNAL_EVENT_TYPES,
    dimension_meta={
        "name": "causal_inference",
        "description": (
            "Observational intervention effect estimates using propensity-adjusted "
            "comparisons and uncertainty intervals"
        ),
        "key_structure": "single overview per user",
        "projection_key": "overview",
        "granularity": ["day", "intervention_window"],
        "relates_to": {
            "readiness_inference": {"join": "day", "why": "causal outcome metric is readiness-like"},
            "recovery": {"join": "day", "why": "sleep/energy/soreness confounders"},
            "nutrition": {"join": "day", "why": "protein and calorie confounding signals"},
            "supplements": {"join": "day", "why": "adherence intervention and confounding context"},
            "training_plan": {"join": "event", "why": "program-change intervention markers"},
            "strength_inference": {"join": "week", "why": "future extension target outcome"},
        },
        "context_seeds": [
            "current_program",
            "nutrition_goals",
            "supplement_stack",
            "sleep_habits",
            "training_frequency",
        ],
        "output_schema": {
            "status": "string — ok|insufficient_data",
            "engine": "string — propensity_ipw_bootstrap",
            "generated_at": "ISO 8601 datetime",
            "timezone_context": {
                "timezone": "IANA timezone used for day/week grouping (e.g. Europe/Berlin)",
                "source": "preference|assumed_default",
                "assumed": "boolean",
                "assumption_disclosure": "string|null",
            },
            "outcome_definition": {
                "metric": "string",
                "horizon": "string",
                "notes": "string",
            },
            "outcome_definitions": {
                "readiness_score_t_plus_1": "next-day readiness composite score",
                "strength_aggregate_delta_t_plus_1": "next-day delta in aggregate daily best estimated 1RM",
                "strength_delta_by_exercise_t_plus_1": "next-day delta in per-exercise daily best estimated 1RM",
            },
            "assumptions": [{"code": "string", "description": "string"}],
            "interventions": {
                "<intervention_name>": {
                    "status": "string",
                    "primary_outcome": "string",
                    "estimand": "string",
                    "effect": {
                        "mean_ate": "number",
                        "ci95": "[number, number]",
                        "direction": "string",
                        "probability_positive": "number [0,1]",
                    },
                    "propensity": {
                        "method": "string",
                        "treated_prevalence": "number [0,1]",
                        "feature_names": ["string"],
                    },
                    "diagnostics": "object",
                    "caveats": [{"code": "string", "severity": "string", "details": "object"}],
                    "estimand_identity": {
                        "schema_version": "string",
                        "identity": "object",
                        "type": "string",
                        "confounders": "list[string]",
                        "required_diagnostics": "list[string]",
                        "notes": "string",
                    },
                    "outcomes": {
                        "readiness_score_t_plus_1": "effect object",
                        "strength_aggregate_delta_t_plus_1": "effect object",
                        "strength_delta_by_exercise_t_plus_1": {
                            "<exercise_id>": "effect object",
                        },
                    },
                    "heterogeneous_effects": {
                        "minimum_segment_samples": "integer",
                        "<outcome_name>": {
                            "subgroups": {"<segment>": "effect object"},
                            "phases": {"<phase>": "effect object"},
                        },
                    },
                    "evidence_overlay": {
                        "schema_version": "supplement_evidence_overlay.v1 (supplement_adherence only)",
                        "policy_role": "advisory_only",
                        "tier": "exploratory|suggestive|strong_observational",
                        "summary": "object",
                        "checks": [{
                            "check": "string",
                            "strong_pass": "boolean",
                            "suggestive_pass": "boolean",
                            "details": "object",
                        }],
                        "disclosure": "string",
                    },
                },
            },
            "population_prior": {
                "applied": "boolean",
                "attempted_estimands": "integer",
                "applied_estimands": "integer",
                "details": [{
                    "intervention": "string",
                    "outcome": "string",
                    "exercise_id": "string (optional)",
                    "attempted": "boolean",
                    "applied": "boolean",
                    "reason": "string (optional)",
                    "target_key": "string",
                }],
            },
            "objective_context": {
                "objective_mode": "string (journal|collaborate|coach|unknown)",
                "modality": "string (open-set modality label)",
            },
            "estimand_registry_version": "string",
            "machine_caveats": [{
                "intervention": "string",
                "outcome": "string",
                "code": "string",
                "severity": "string",
                "details": "object",
                "exercise_id": "string (optional)",
                "segment_type": "string (optional)",
                "segment_label": "string (optional)",
            }],
            "evidence_window": {
                "days_considered": "integer",
                "windows_evaluated": "integer",
                "history_days_required": "integer",
                "minimum_segment_samples": "integer",
            },
            "daily_context": [{
                "date": "ISO 8601 date",
                "readiness_score": "number [0,1]",
                "sleep_hours": "number",
                "protein_g": "number",
                "load_volume": "number (modality-aware daily load score)",
                "strength_aggregate_e1rm": "number|null",
                "strength_by_exercise": {"<exercise_id>": "number"},
                "program_change_event": "boolean",
                "sleep_target_event": "boolean",
                "nutrition_target_event": "boolean",
                "supplement_adherence": "number [0,1]",
                "supplement_expected_count": "integer",
                "supplement_taken_explicit_count": "integer",
                "supplement_taken_assumed_count": "integer",
                "supplement_skipped_count": "integer",
            }],
            "data_quality": {
                "events_processed": "integer",
                "observed_days": "integer",
                "temporal_conflicts": {"<conflict_type>": "integer"},
                "treated_windows": {
                    "program_change": "integer",
                    "nutrition_shift": "integer",
                    "sleep_intervention": "integer",
                    "supplement_adherence": "integer",
                },
                "outcome_windows": {
                    "<intervention_name>": {
                        "readiness_score_t_plus_1": "integer",
                        "strength_aggregate_delta_t_plus_1": "integer",
                        "strength_delta_by_exercise_t_plus_1": {
                            "<exercise_id>": "integer",
                        },
                    },
                },
            },
        },
        "manifest_contribution": _manifest_contribution,
    },
)
async def update_causal_inference(
    conn: psycopg.AsyncConnection[Any],
    payload: dict[str, Any],
) -> None:
    user_id = payload["user_id"]
    event_type = payload.get("event_type", "")
    started_at = datetime.now(timezone.utc)
    telemetry_engine = "propensity_ipw_bootstrap"

    async def _record(
        status: str,
        diagnostics: dict[str, Any],
        *,
        error_message: str | None = None,
        error_taxonomy: str | None = None,
    ) -> None:
        await safe_record_inference_run(
            conn,
            user_id=user_id,
            projection_type="causal_inference",
            key="overview",
            engine=telemetry_engine,
            status=status,
            diagnostics=diagnostics,
            error_message=error_message,
            error_taxonomy=error_taxonomy,
            started_at=started_at,
        )

    try:
        retracted_ids = await get_retracted_event_ids(conn, user_id)
        timezone_pref = await load_timezone_preference(conn, user_id, retracted_ids)
        timezone_context = resolve_timezone_context(timezone_pref)
        timezone_name = timezone_context["timezone"]
        objective_mode, objective_modality = await _resolve_objective_context(conn, user_id)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, timestamp, event_type, data, metadata
                FROM events
                WHERE user_id = %s
                  AND event_type = ANY(%s)
                ORDER BY timestamp ASC
                """,
                (user_id, list(CAUSAL_SIGNAL_EVENT_TYPES)),
            )
            rows = await cur.fetchall()

        rows = [row for row in rows if str(row["id"]) not in retracted_ids]
        if not rows:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM projections
                    WHERE user_id = %s
                      AND projection_type = 'causal_inference'
                      AND key = 'overview'
                    """,
                    (user_id,),
                )
            await _record(
                "skipped",
                {
                    "skip_reason": "no_signals",
                    "event_type": event_type,
                },
                error_taxonomy=INFERENCE_ERROR_INSUFFICIENT_DATA,
            )
            return

        signal_rows = normalize_training_signal_rows(rows)

        readiness_signals = build_readiness_daily_scores(
            signal_rows,
            timezone_name=timezone_name,
        )
        readiness_daily = list(readiness_signals.get("daily_scores") or [])
        if not readiness_daily:
            await _record(
                "skipped",
                {
                    "skip_reason": "no_readiness_observations",
                    "event_type": event_type,
                },
                error_taxonomy=INFERENCE_ERROR_INSUFFICIENT_DATA,
            )
            return

        readiness_by_day = {
            str(entry["date"]): entry
            for entry in readiness_daily
            if isinstance(entry, dict) and isinstance(entry.get("date"), str)
        }
        supplement_daily_by_day: dict[date, dict[str, Any]] = {}
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT data
                FROM projections
                WHERE user_id = %s
                  AND projection_type = 'supplements'
                  AND key = 'overview'
                """,
                (user_id,),
            )
            supplement_projection_row = await cur.fetchone()
        if supplement_projection_row and isinstance(supplement_projection_row.get("data"), dict):
            supplement_daily_by_day = _index_supplement_daily_status(
                supplement_projection_row["data"]
            )

        alias_map: dict[str, str] = {}
        for row in signal_rows:
            if row.get("event_type") != "exercise.alias_created":
                continue
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            alias = str(data.get("alias") or "").strip().lower()
            target = str(data.get("exercise_id") or "").strip().lower()
            if alias and target:
                alias_map[alias] = target

        component_priors = readiness_signals.get("component_priors") or {}
        fallback_sleep_hours = _safe_float(component_priors.get("sleep_hours"), default=7.0)
        fallback_energy_level = _safe_float(component_priors.get("energy_level"), default=6.0)
        fallback_soreness_level = _safe_float(component_priors.get("soreness_level"), default=2.0)
        temporal_conflicts: dict[str, int] = dict(
            readiness_signals.get("temporal_conflicts") or {}
        )

        per_day_aux: dict[date, dict[str, Any]] = defaultdict(
            lambda: {
                "protein_g": 0.0,
                "calories": 0.0,
                "program_events": 0,
                "sleep_target_events": 0,
                "nutrition_target_events": 0,
                "strength_by_exercise": {},
            }
        )

        for row in signal_rows:
            timestamp = row.get("timestamp")
            if not isinstance(timestamp, datetime):
                continue
            row_event_type = row["event_type"]
            data = row["data"] if isinstance(row.get("data"), dict) else {}
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            temporal = normalize_temporal_point(
                timestamp,
                timezone_name=timezone_name,
                data=data,
                metadata=metadata,
            )
            day = temporal.local_date
            for conflict in temporal.conflicts:
                temporal_conflicts[conflict] = temporal_conflicts.get(conflict, 0) + 1
            bucket = per_day_aux[day]

            if row_event_type in {"set.logged", "session.logged"}:
                weight = _safe_float(data.get("weight_kg", data.get("weight")), default=0.0)
                reps = _safe_float(data.get("reps"), default=0.0)
                if weight > 0.0 and reps > 0.0:
                    raw_key = resolve_exercise_key(data)
                    exercise_key = (
                        resolve_through_aliases(raw_key, alias_map)
                        if raw_key
                        else None
                    )
                    if exercise_key:
                        e1rm = epley_1rm(weight, int(round(reps)))
                        if e1rm > 0.0:
                            strength_map = bucket["strength_by_exercise"]
                            previous = _safe_float(
                                strength_map.get(exercise_key),
                                default=0.0,
                            )
                            if e1rm > previous:
                                strength_map[exercise_key] = e1rm
            elif row_event_type == "meal.logged":
                bucket["protein_g"] += max(0.0, _safe_float(data.get("protein_g"), default=0.0))
                bucket["calories"] += max(0.0, _safe_float(data.get("calories"), default=0.0))
            elif row_event_type in {
                "program.started",
                "training_plan.created",
                "training_plan.updated",
                "training_plan.archived",
            }:
                bucket["program_events"] += 1
            elif row_event_type == "sleep_target.set":
                bucket["sleep_target_events"] += 1
            elif row_event_type == "nutrition_target.set":
                bucket["nutrition_target_events"] += 1

        observed_days = sorted(date.fromisoformat(day_iso) for day_iso in readiness_by_day)
        daily_context: list[dict[str, Any]] = []
        strength_state: dict[str, float] = {}
        for day in observed_days:
            signal_row = readiness_by_day.get(day.isoformat()) or {}
            signal_values = signal_row.get("signals") if isinstance(signal_row.get("signals"), dict) else {}
            bucket = per_day_aux[day]
            supplement_row = supplement_daily_by_day.get(day) or {}

            sleep_hours = _safe_float(signal_values.get("sleep_hours"), default=fallback_sleep_hours)
            energy = _safe_float(signal_values.get("energy_level"), default=fallback_energy_level)
            soreness_avg = _safe_float(signal_values.get("soreness_level"), default=fallback_soreness_level)
            load_volume = _safe_float(signal_values.get("load_score"), default=0.0)
            readiness_score = _safe_float(signal_row.get("score"), default=0.0)
            protein_g = float(bucket["protein_g"])
            supplement_adherence = _safe_float(
                supplement_row.get("adherence_rate"),
                default=0.0,
            )
            supplement_expected = _safe_int(supplement_row.get("expected_count"), default=0)
            supplement_taken_explicit = _safe_int(
                len(supplement_row.get("taken_explicit") or []),
                default=0,
            )
            supplement_taken_assumed = _safe_int(
                len(supplement_row.get("taken_assumed") or []),
                default=0,
            )
            supplement_skipped = _safe_int(
                len(supplement_row.get("skipped") or []),
                default=0,
            )

            for exercise_id, value in (bucket["strength_by_exercise"] or {}).items():
                strength_state[str(exercise_id)] = _safe_float(value, default=0.0)
            strength_snapshot = dict(strength_state)
            strength_aggregate = (
                _mean(list(strength_snapshot.values()))
                if strength_snapshot
                else None
            )

            daily_context.append(
                {
                    "date": day.isoformat(),
                    "readiness_score": round(readiness_score, 3),
                    "sleep_hours": round(sleep_hours, 2),
                    "energy_level": round(energy, 2),
                    "soreness_level": round(soreness_avg, 2),
                    "load_volume": round(load_volume, 2),
                    "protein_g": round(protein_g, 2),
                    "calories": round(float(bucket["calories"]), 2),
                    "strength_aggregate_e1rm": (
                        round(float(strength_aggregate), 2)
                        if strength_aggregate is not None
                        else None
                    ),
                    "strength_by_exercise": _round_strength_map(strength_snapshot),
                    "program_change_event": bool(bucket["program_events"]),
                    "sleep_target_event": bool(bucket["sleep_target_events"]),
                    "nutrition_target_event": bool(bucket["nutrition_target_events"]),
                    "supplement_adherence": round(supplement_adherence, 3),
                    "supplement_expected_count": supplement_expected,
                    "supplement_taken_explicit_count": supplement_taken_explicit,
                    "supplement_taken_assumed_count": supplement_taken_assumed,
                    "supplement_skipped_count": supplement_skipped,
                }
            )

        history_days_required = 7
        windows_evaluated = 0
        samples_by_intervention: dict[str, dict[str, Any]] = {
            "program_change": {
                "outcomes": {
                    OUTCOME_READINESS: [],
                    OUTCOME_STRENGTH_AGGREGATE: [],
                },
                "strength_by_exercise": defaultdict(list),
                "objective_mode": objective_mode,
                "modality": objective_modality,
            },
            "nutrition_shift": {
                "outcomes": {
                    OUTCOME_READINESS: [],
                    OUTCOME_STRENGTH_AGGREGATE: [],
                },
                "strength_by_exercise": defaultdict(list),
                "objective_mode": objective_mode,
                "modality": objective_modality,
            },
            "sleep_intervention": {
                "outcomes": {
                    OUTCOME_READINESS: [],
                    OUTCOME_STRENGTH_AGGREGATE: [],
                },
                "strength_by_exercise": defaultdict(list),
                "objective_mode": objective_mode,
                "modality": objective_modality,
            },
            "supplement_adherence": {
                "outcomes": {
                    OUTCOME_READINESS: [],
                    OUTCOME_STRENGTH_AGGREGATE: [],
                },
                "strength_by_exercise": defaultdict(list),
                "objective_mode": objective_mode,
                "modality": objective_modality,
            },
        }

        context_by_date = {
            date.fromisoformat(entry["date"]): entry
            for entry in daily_context
        }
        ordered_days = sorted(context_by_date.keys())

        for idx, current_day in enumerate(ordered_days):
            next_day_key = current_day + timedelta(days=1)
            if next_day_key not in context_by_date:
                continue
            if idx < history_days_required:
                continue

            current = context_by_date[current_day]
            next_day = context_by_date[next_day_key]
            history = [
                context_by_date[d]
                for d in ordered_days[idx - history_days_required:idx]
            ]
            windows_evaluated += 1

            baseline_readiness = _mean(
                [_safe_float(day.get("readiness_score")) for day in history],
                fallback=0.5,
            )
            baseline_sleep = _mean(
                [_safe_float(day.get("sleep_hours")) for day in history],
                fallback=fallback_sleep_hours,
            )
            baseline_load = _mean(
                [_safe_float(day.get("load_volume")) for day in history],
                fallback=0.0,
            )
            baseline_protein = _mean(
                [_safe_float(day.get("protein_g")) for day in history],
                fallback=0.0,
            )
            baseline_supplement_adherence = _mean(
                [_safe_float(day.get("supplement_adherence")) for day in history],
                fallback=0.0,
            )
            baseline_supplement_expected = _mean(
                [_safe_float(day.get("supplement_expected_count")) for day in history],
                fallback=0.0,
            )
            baseline_strength_aggregate = _mean(
                [
                    _safe_float(day.get("strength_aggregate_e1rm"), default=0.0)
                    for day in history
                    if day.get("strength_aggregate_e1rm") is not None
                ],
                fallback=0.0,
            )

            sleep_shift = _safe_float(current.get("sleep_hours")) >= (baseline_sleep + 0.75)
            nutrition_shift = _safe_float(current.get("protein_g")) >= (baseline_protein + 20.0)

            current_readiness = _safe_float(current.get("readiness_score"), default=0.0)
            current_strength_aggregate = (
                _safe_float(current.get("strength_aggregate_e1rm"), default=0.0)
                if current.get("strength_aggregate_e1rm") is not None
                else None
            )
            next_strength_aggregate = (
                _safe_float(next_day.get("strength_aggregate_e1rm"), default=0.0)
                if next_day.get("strength_aggregate_e1rm") is not None
                else None
            )

            segment_subgroup = _subgroup_bucket(current_readiness)
            phase_info = weekly_phase_from_date(current.get("date"))
            segment_phase = _phase_bucket(phase_info.get("phase"))

            common_confounders = {
                "baseline_readiness": baseline_readiness,
                "baseline_sleep_hours": baseline_sleep,
                "baseline_load_volume": baseline_load,
                "baseline_protein_g": baseline_protein,
                "baseline_supplement_adherence": baseline_supplement_adherence,
                "baseline_supplement_expected": baseline_supplement_expected,
                "baseline_strength_aggregate": baseline_strength_aggregate,
                "current_readiness": current_readiness,
                "current_sleep_hours": _safe_float(current.get("sleep_hours"), default=0.0),
                "current_load_volume": _safe_float(current.get("load_volume"), default=0.0),
                "current_protein_g": _safe_float(current.get("protein_g"), default=0.0),
                "current_calories": _safe_float(current.get("calories"), default=0.0),
                "current_supplement_adherence": _safe_float(
                    current.get("supplement_adherence"),
                    default=0.0,
                ),
                "current_supplement_expected": _safe_float(
                    current.get("supplement_expected_count"),
                    default=0.0,
                ),
                "current_supplement_skipped": _safe_float(
                    current.get("supplement_skipped_count"),
                    default=0.0,
                ),
                "current_strength_aggregate": current_strength_aggregate or 0.0,
            }

            intervention_flags = {
                "program_change": 1 if bool(current.get("program_change_event")) else 0,
                "nutrition_shift": 1
                if bool(current.get("nutrition_target_event")) or nutrition_shift
                else 0,
                "sleep_intervention": 1
                if bool(current.get("sleep_target_event")) or sleep_shift
                else 0,
                "supplement_adherence": _supplement_adherence_intervention_flag(
                    current_adherence=_safe_float(
                        current.get("supplement_adherence"),
                        default=0.0,
                    ),
                    baseline_adherence=baseline_supplement_adherence,
                    expected_count=_safe_int(
                        current.get("supplement_expected_count"),
                        default=0,
                    ),
                ),
            }

            readiness_outcome = _safe_float(next_day.get("readiness_score"), default=0.0)
            strength_aggregate_delta: float | None = None
            if current_strength_aggregate is not None and next_strength_aggregate is not None:
                strength_aggregate_delta = next_strength_aggregate - current_strength_aggregate

            current_strength_map = current.get("strength_by_exercise") or {}
            next_strength_map = next_day.get("strength_by_exercise") or {}
            exercise_deltas: dict[str, float] = {}
            for exercise_id in set(current_strength_map).intersection(next_strength_map):
                current_value = _safe_float(current_strength_map.get(exercise_id), default=0.0)
                next_value = _safe_float(next_strength_map.get(exercise_id), default=current_value)
                exercise_deltas[str(exercise_id)] = next_value - current_value

            for intervention_name, treated_flag in intervention_flags.items():
                bucket = samples_by_intervention[intervention_name]
                outcome_bucket = bucket["outcomes"]

                base_sample = {
                    "treated": treated_flag,
                    "date": str(current.get("date") or ""),
                    "confounders": dict(common_confounders),
                    "subgroup": segment_subgroup,
                    "phase": segment_phase,
                    "supplement_expected_count": _safe_int(
                        current.get("supplement_expected_count"),
                        default=0,
                    ),
                    "supplement_taken_explicit_count": _safe_int(
                        current.get("supplement_taken_explicit_count"),
                        default=0,
                    ),
                    "supplement_taken_assumed_count": _safe_int(
                        current.get("supplement_taken_assumed_count"),
                        default=0,
                    ),
                }

                outcome_bucket[OUTCOME_READINESS].append(
                    {
                        **base_sample,
                        "outcome": readiness_outcome,
                    }
                )
                if strength_aggregate_delta is not None:
                    outcome_bucket[OUTCOME_STRENGTH_AGGREGATE].append(
                        {
                            **base_sample,
                            "outcome": strength_aggregate_delta,
                        }
                    )

                for exercise_id, delta in exercise_deltas.items():
                    per_exercise_confounders = dict(common_confounders)
                    per_exercise_confounders["current_exercise_strength"] = _safe_float(
                        current_strength_map.get(exercise_id),
                        default=0.0,
                    )
                    bucket["strength_by_exercise"][exercise_id].append(
                        {
                            "treated": treated_flag,
                            "outcome": delta,
                            "confounders": per_exercise_confounders,
                            "subgroup": segment_subgroup,
                            "phase": segment_phase,
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
        bootstrap_samples = max(
            80,
            int(os.environ.get("KURA_CAUSAL_BOOTSTRAP_SAMPLES", "250")),
        )

        intervention_results: dict[str, Any] = {}
        treated_windows: dict[str, int] = {}
        outcome_windows: dict[str, Any] = {}
        machine_caveats: list[dict[str, Any]] = []
        has_ok = False
        insightful_outcomes = 0
        prior_cache: dict[str, dict[str, Any] | None] = {}
        population_prior_usage: list[dict[str, Any]] = []

        async def _cached_population_prior(target_key: str) -> dict[str, Any] | None:
            if target_key in prior_cache:
                return prior_cache[target_key]
            prior = await resolve_population_prior(
                conn,
                user_id=user_id,
                projection_type="causal_inference",
                target_key=target_key,
                retracted_ids=retracted_ids,
            )
            prior_cache[target_key] = prior
            return prior

        for name, sample_payload in samples_by_intervention.items():
            outcome_samples = sample_payload["outcomes"]
            strength_by_exercise_samples = sample_payload["strength_by_exercise"]
            intervention_objective_mode = _normalize_objective_mode(
                sample_payload.get("objective_mode")
            )
            intervention_modality = _normalize_modality(sample_payload.get("modality"))

            readiness_samples = outcome_samples[OUTCOME_READINESS]
            treated_windows[name] = sum(
                1
                for sample in readiness_samples
                if int(_safe_float(sample.get("treated"), default=0.0)) == 1
            )

            readiness_result = _estimate_effect(
                readiness_samples,
                min_samples=min_samples,
                bootstrap_samples=bootstrap_samples,
            )
            strength_aggregate_samples = outcome_samples[OUTCOME_STRENGTH_AGGREGATE]
            strength_aggregate_result = _estimate_effect(
                strength_aggregate_samples,
                min_samples=strength_min_samples,
                bootstrap_samples=bootstrap_samples,
            )

            strength_per_exercise_results: dict[str, dict[str, Any]] = {}
            strength_per_exercise_windows: dict[str, int] = {}
            for exercise_id, exercise_samples in sorted(
                strength_by_exercise_samples.items(),
                key=lambda item: item[0],
            ):
                strength_per_exercise_windows[exercise_id] = len(exercise_samples)
                strength_per_exercise_results[exercise_id] = _estimate_effect(
                    exercise_samples,
                    min_samples=strength_min_samples,
                    bootstrap_samples=bootstrap_samples,
                )

            readiness_target_key = build_causal_estimand_target_key(
                intervention=name,
                outcome=OUTCOME_READINESS,
                objective_mode=intervention_objective_mode,
                modality=intervention_modality,
            )
            readiness_prior = await _cached_population_prior(readiness_target_key)
            readiness_result, readiness_prior_meta = _blend_population_prior_into_effect(
                readiness_result,
                target_key=readiness_target_key,
                population_prior=readiness_prior,
            )
            readiness_result = _attach_estimand_contract(
                readiness_result,
                intervention=name,
                outcome=OUTCOME_READINESS,
                objective_mode=intervention_objective_mode,
                modality=intervention_modality,
            )
            population_prior_usage.append(
                {
                    "intervention": name,
                    "outcome": OUTCOME_READINESS,
                    **readiness_prior_meta,
                }
            )

            strength_aggregate_target_key = build_causal_estimand_target_key(
                intervention=name,
                outcome=OUTCOME_STRENGTH_AGGREGATE,
                objective_mode=intervention_objective_mode,
                modality=intervention_modality,
            )
            strength_aggregate_prior = await _cached_population_prior(strength_aggregate_target_key)
            strength_aggregate_result, strength_aggregate_prior_meta = _blend_population_prior_into_effect(
                strength_aggregate_result,
                target_key=strength_aggregate_target_key,
                population_prior=strength_aggregate_prior,
            )
            strength_aggregate_result = _attach_estimand_contract(
                strength_aggregate_result,
                intervention=name,
                outcome=OUTCOME_STRENGTH_AGGREGATE,
                objective_mode=intervention_objective_mode,
                modality=intervention_modality,
            )
            population_prior_usage.append(
                {
                    "intervention": name,
                    "outcome": OUTCOME_STRENGTH_AGGREGATE,
                    **strength_aggregate_prior_meta,
                }
            )

            for exercise_id, exercise_result in list(strength_per_exercise_results.items()):
                exercise_target_key = build_causal_estimand_target_key(
                    intervention=name,
                    outcome=OUTCOME_STRENGTH_PER_EXERCISE,
                    objective_mode=intervention_objective_mode,
                    modality=intervention_modality,
                    exercise_id=str(exercise_id),
                )
                exercise_prior = await _cached_population_prior(exercise_target_key)
                blended_exercise_result, exercise_prior_meta = _blend_population_prior_into_effect(
                    exercise_result,
                    target_key=exercise_target_key,
                    population_prior=exercise_prior,
                )
                blended_exercise_result = _attach_estimand_contract(
                    blended_exercise_result,
                    intervention=name,
                    outcome=OUTCOME_STRENGTH_PER_EXERCISE,
                    objective_mode=intervention_objective_mode,
                    modality=intervention_modality,
                    exercise_id=str(exercise_id),
                )
                strength_per_exercise_results[exercise_id] = blended_exercise_result
                population_prior_usage.append(
                    {
                        "intervention": name,
                        "outcome": OUTCOME_STRENGTH_PER_EXERCISE,
                        "exercise_id": str(exercise_id),
                        **exercise_prior_meta,
                    }
                )

            heterogeneous_effects = {
                "minimum_segment_samples": segment_min_samples,
                OUTCOME_READINESS: {
                    "subgroups": _estimate_segment_slices(
                        readiness_samples,
                        segment_key="subgroup",
                        min_samples=segment_min_samples,
                        bootstrap_samples=bootstrap_samples,
                    ),
                    "phases": _estimate_segment_slices(
                        readiness_samples,
                        segment_key="phase",
                        min_samples=segment_min_samples,
                        bootstrap_samples=bootstrap_samples,
                    ),
                },
                OUTCOME_STRENGTH_AGGREGATE: {
                    "subgroups": _estimate_segment_slices(
                        strength_aggregate_samples,
                        segment_key="subgroup",
                        min_samples=segment_min_samples,
                        bootstrap_samples=bootstrap_samples,
                    ),
                    "phases": _estimate_segment_slices(
                        strength_aggregate_samples,
                        segment_key="phase",
                        min_samples=segment_min_samples,
                        bootstrap_samples=bootstrap_samples,
                    ),
                },
            }

            _append_result_caveats(
                machine_caveats,
                intervention=name,
                outcome=OUTCOME_READINESS,
                result=readiness_result,
            )
            _append_result_caveats(
                machine_caveats,
                intervention=name,
                outcome=OUTCOME_STRENGTH_AGGREGATE,
                result=strength_aggregate_result,
            )
            for exercise_id, exercise_result in strength_per_exercise_results.items():
                _append_result_caveats(
                    machine_caveats,
                    intervention=name,
                    outcome=OUTCOME_STRENGTH_PER_EXERCISE,
                    result=exercise_result,
                    exercise_id=exercise_id,
                )

            for outcome_name in (OUTCOME_READINESS, OUTCOME_STRENGTH_AGGREGATE):
                for segment_type, segment_results in (
                    ("subgroup", heterogeneous_effects[outcome_name]["subgroups"]),
                    ("phase", heterogeneous_effects[outcome_name]["phases"]),
                ):
                    for segment_label, segment_result in segment_results.items():
                        _append_result_caveats(
                            machine_caveats,
                            intervention=name,
                            outcome=outcome_name,
                            result=segment_result,
                            segment_type=segment_type,
                            segment_label=segment_label,
                        )

            outcome_statuses = [
                readiness_result.get("status") == "ok",
                strength_aggregate_result.get("status") == "ok",
                any(
                    payload.get("status") == "ok"
                    for payload in strength_per_exercise_results.values()
                ),
            ]
            insightful_outcomes += sum(1 for status in outcome_statuses if status)
            intervention_status = "ok" if any(outcome_statuses) else "insufficient_data"
            has_ok = has_ok or intervention_status == "ok"

            intervention_payload = dict(readiness_result)
            intervention_payload["status"] = intervention_status
            intervention_payload["primary_outcome"] = OUTCOME_READINESS
            intervention_payload["objective_mode"] = intervention_objective_mode
            intervention_payload["modality"] = intervention_modality
            intervention_payload["outcomes"] = {
                OUTCOME_READINESS: readiness_result,
                OUTCOME_STRENGTH_AGGREGATE: strength_aggregate_result,
                OUTCOME_STRENGTH_PER_EXERCISE: strength_per_exercise_results,
            }
            intervention_payload["heterogeneous_effects"] = heterogeneous_effects
            if name == "supplement_adherence":
                intervention_payload["evidence_overlay"] = _build_supplement_evidence_overlay(
                    readiness_samples,
                    readiness_result,
                    min_samples=min_samples,
                    bootstrap_samples=bootstrap_samples,
                )
            intervention_results[name] = intervention_payload

            outcome_windows[name] = {
                OUTCOME_READINESS: len(readiness_samples),
                OUTCOME_STRENGTH_AGGREGATE: len(strength_aggregate_samples),
                OUTCOME_STRENGTH_PER_EXERCISE: strength_per_exercise_windows,
            }

        population_prior_attempted = sum(
            1 for usage in population_prior_usage if bool(usage.get("attempted"))
        )
        population_prior_applied = sum(
            1 for usage in population_prior_usage if bool(usage.get("applied"))
        )
        population_prior_summary = {
            "attempted_estimands": population_prior_attempted,
            "applied_estimands": population_prior_applied,
            "applied": population_prior_applied > 0,
            "details": population_prior_usage,
        }

        projection_data = {
            "status": "ok" if has_ok else "insufficient_data",
            "engine": telemetry_engine,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "timezone_context": timezone_context,
            "outcome_definition": {
                "metric": "next_day_readiness_score",
                "horizon": "t+1 day",
                "notes": (
                    "Primary outcome is readiness-style; additional strength deltas "
                    "are estimated for aggregate and per-exercise views."
                ),
            },
            "outcome_definitions": {
                OUTCOME_READINESS: {
                    "metric": "next_day_readiness_score",
                    "horizon": "t+1 day",
                },
                OUTCOME_STRENGTH_AGGREGATE: {
                    "metric": "next_day_strength_aggregate_delta_e1rm",
                    "horizon": "t+1 day",
                },
                OUTCOME_STRENGTH_PER_EXERCISE: {
                    "metric": "next_day_strength_delta_e1rm_per_exercise",
                    "horizon": "t+1 day",
                },
            },
            "assumptions": ASSUMPTIONS,
            "interventions": intervention_results,
            "population_prior": population_prior_summary,
            "objective_context": {
                "objective_mode": objective_mode,
                "modality": objective_modality,
            },
            "estimand_registry_version": CAUSAL_ESTIMAND_REGISTRY_SCHEMA_VERSION,
            "machine_caveats": machine_caveats,
            "evidence_window": {
                "days_considered": len(daily_context),
                "windows_evaluated": windows_evaluated,
                "history_days_required": history_days_required,
                "minimum_segment_samples": segment_min_samples,
            },
            "daily_context": daily_context[-60:],
            "data_quality": {
                "events_processed": len(signal_rows),
                "observed_days": len(observed_days),
                "temporal_conflicts": temporal_conflicts,
                "treated_windows": treated_windows,
                "outcome_windows": outcome_windows,
            },
        }

        last_event_id = str(rows[-1]["id"])
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO projections (
                    user_id, projection_type, key, data, version, last_event_id, updated_at
                )
                VALUES (%s, 'causal_inference', 'overview', %s, 1, %s, NOW())
                ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                    data = EXCLUDED.data,
                    version = projections.version + 1,
                    last_event_id = EXCLUDED.last_event_id,
                    updated_at = NOW()
                """,
                (user_id, json.dumps(projection_data), last_event_id),
            )

        telemetry_status = "success" if has_ok else "skipped"
        telemetry_error_taxonomy: str | None = None
        telemetry_diagnostics = {
            "event_type": event_type,
            "events_processed": len(signal_rows),
            "observed_days": len(observed_days),
            "temporal_conflicts": temporal_conflicts,
            "windows_evaluated": windows_evaluated,
            "treated_windows": treated_windows,
            "outcome_windows": outcome_windows,
            "minimum_segment_samples": segment_min_samples,
            "insightful_interventions": sum(
                1 for payload in intervention_results.values() if payload.get("status") == "ok"
            ),
            "insightful_outcomes": insightful_outcomes,
            "objective_context": {
                "objective_mode": objective_mode,
                "modality": objective_modality,
            },
            "estimand_registry_version": CAUSAL_ESTIMAND_REGISTRY_SCHEMA_VERSION,
            "population_prior": {
                "attempted_estimands": population_prior_attempted,
                "applied_estimands": population_prior_applied,
                "applied": population_prior_applied > 0,
                "fallback_reasons": {
                    reason: sum(1 for usage in population_prior_usage if usage.get("reason") == reason)
                    for reason in sorted(
                        {
                            str(usage.get("reason"))
                            for usage in population_prior_usage
                            if usage.get("reason")
                        }
                    )
                },
            },
        }
        if not has_ok:
            telemetry_error_taxonomy = INFERENCE_ERROR_INSUFFICIENT_DATA
            telemetry_diagnostics["skip_reason"] = "insufficient_data"

        await _record(
            telemetry_status,
            telemetry_diagnostics,
            error_taxonomy=telemetry_error_taxonomy,
        )

        logger.info(
            (
                "Updated causal_inference for user=%s "
                "(days=%d, windows=%d, ok=%s, timezone=%s, assumed=%s)"
            ),
            user_id,
            len(daily_context),
            windows_evaluated,
            has_ok,
            timezone_name,
            timezone_context["assumed"],
        )
    except Exception as exc:
        await _record(
            "failed",
            {
                "event_type": event_type,
            },
            error_message=str(exc),
            error_taxonomy=classify_inference_error(exc),
        )
        raise
