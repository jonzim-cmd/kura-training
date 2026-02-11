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
import os
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..causal_inference import ASSUMPTIONS, estimate_intervention_effect
from ..inference_engine import weekly_phase_from_date
from ..inference_telemetry import (
    INFERENCE_ERROR_INSUFFICIENT_DATA,
    classify_inference_error,
    safe_record_inference_run,
)
from ..registry import projection_handler
from ..utils import epley_1rm, get_retracted_event_ids, resolve_exercise_key

logger = logging.getLogger(__name__)

OUTCOME_READINESS = "readiness_score_t_plus_1"
OUTCOME_STRENGTH_AGGREGATE = "strength_aggregate_delta_t_plus_1"
OUTCOME_STRENGTH_PER_EXERCISE = "strength_delta_by_exercise_t_plus_1"


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


def _map_effect_strength(effect_payload: Any) -> float:
    if not isinstance(effect_payload, dict):
        return 0.0
    return _safe_float(effect_payload.get("mean_ate"), default=0.0)


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
    "program.started",
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    "meal.logged",
    "nutrition_target.set",
    "sleep.logged",
    "sleep_target.set",
    "set.logged",
    "energy.logged",
    "soreness.logged",
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
            "training_plan": {"join": "event", "why": "program-change intervention markers"},
            "strength_inference": {"join": "week", "why": "future extension target outcome"},
        },
        "context_seeds": [
            "current_program",
            "nutrition_goals",
            "sleep_habits",
            "training_frequency",
        ],
        "output_schema": {
            "status": "string — ok|insufficient_data",
            "engine": "string — propensity_ipw_bootstrap",
            "generated_at": "ISO 8601 datetime",
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
                },
            },
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
                "load_volume": "number",
                "strength_aggregate_e1rm": "number|null",
                "strength_by_exercise": {"<exercise_id>": "number"},
                "program_change_event": "boolean",
                "sleep_target_event": "boolean",
                "nutrition_target_event": "boolean",
            }],
            "data_quality": {
                "events_processed": "integer",
                "observed_days": "integer",
                "treated_windows": {
                    "program_change": "integer",
                    "nutrition_shift": "integer",
                    "sleep_intervention": "integer",
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

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, timestamp, event_type, data
                FROM events
                WHERE user_id = %s
                  AND event_type IN (
                      'program.started',
                      'training_plan.created',
                      'training_plan.updated',
                      'training_plan.archived',
                      'meal.logged',
                      'nutrition_target.set',
                      'sleep.logged',
                      'sleep_target.set',
                      'set.logged',
                      'energy.logged',
                      'soreness.logged'
                  )
                ORDER BY timestamp ASC
                """,
                (user_id,),
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

        per_day: dict[date, dict[str, Any]] = defaultdict(
            lambda: {
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
            }
        )

        for row in rows:
            day = row["timestamp"].date()
            row_event_type = row["event_type"]
            data = row["data"] or {}
            bucket = per_day[day]

            if row_event_type == "sleep.logged":
                duration = _safe_float(data.get("duration_hours"), default=0.0)
                if duration > 0.0:
                    bucket["sleep_hours_sum"] += duration
                    bucket["sleep_entries"] += 1
            elif row_event_type == "energy.logged":
                energy = _safe_float(data.get("level"), default=0.0)
                if energy > 0.0:
                    bucket["energy_sum"] += energy
                    bucket["energy_entries"] += 1
            elif row_event_type == "soreness.logged":
                soreness = _safe_float(data.get("severity"), default=0.0)
                if soreness > 0.0:
                    bucket["soreness_sum"] += soreness
                    bucket["soreness_entries"] += 1
            elif row_event_type == "set.logged":
                weight = _safe_float(data.get("weight_kg", data.get("weight")), default=0.0)
                reps = _safe_float(data.get("reps"), default=0.0)
                if weight > 0.0 and reps > 0.0:
                    bucket["load_volume"] += weight * reps
                    exercise_key = resolve_exercise_key(data)
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

        observed_days = sorted(per_day.keys())
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
                strength_state[str(exercise_id)] = _safe_float(value, default=0.0)
            strength_snapshot = dict(strength_state)
            strength_aggregate = (
                _mean(list(strength_snapshot.values()))
                if strength_snapshot
                else None
            )

            sleep_score = _clamp(sleep_hours / 8.0, 0.0, 1.2)
            energy_score = _clamp(energy / 10.0, 0.0, 1.0)
            soreness_penalty = _clamp(soreness_avg / 5.0, 0.0, 1.0)
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
            },
            "nutrition_shift": {
                "outcomes": {
                    OUTCOME_READINESS: [],
                    OUTCOME_STRENGTH_AGGREGATE: [],
                },
                "strength_by_exercise": defaultdict(list),
            },
            "sleep_intervention": {
                "outcomes": {
                    OUTCOME_READINESS: [],
                    OUTCOME_STRENGTH_AGGREGATE: [],
                },
                "strength_by_exercise": defaultdict(list),
            },
        }

        for idx in range(history_days_required, len(daily_context) - 1):
            current = daily_context[idx]
            next_day = daily_context[idx + 1]
            history = daily_context[idx - history_days_required:idx]
            windows_evaluated += 1

            baseline_readiness = _mean(
                [_safe_float(day.get("readiness_score")) for day in history],
                fallback=0.5,
            )
            baseline_sleep = _mean(
                [_safe_float(day.get("sleep_hours")) for day in history],
                fallback=6.5,
            )
            baseline_load = _mean(
                [_safe_float(day.get("load_volume")) for day in history],
                fallback=0.0,
            )
            baseline_protein = _mean(
                [_safe_float(day.get("protein_g")) for day in history],
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
                "baseline_strength_aggregate": baseline_strength_aggregate,
                "current_readiness": current_readiness,
                "current_sleep_hours": _safe_float(current.get("sleep_hours"), default=0.0),
                "current_load_volume": _safe_float(current.get("load_volume"), default=0.0),
                "current_protein_g": _safe_float(current.get("protein_g"), default=0.0),
                "current_calories": _safe_float(current.get("calories"), default=0.0),
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
                    "confounders": dict(common_confounders),
                    "subgroup": segment_subgroup,
                    "phase": segment_phase,
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

        for name, sample_payload in samples_by_intervention.items():
            outcome_samples = sample_payload["outcomes"]
            strength_by_exercise_samples = sample_payload["strength_by_exercise"]

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
            intervention_payload["outcomes"] = {
                OUTCOME_READINESS: readiness_result,
                OUTCOME_STRENGTH_AGGREGATE: strength_aggregate_result,
                OUTCOME_STRENGTH_PER_EXERCISE: strength_per_exercise_results,
            }
            intervention_payload["heterogeneous_effects"] = heterogeneous_effects
            intervention_results[name] = intervention_payload

            outcome_windows[name] = {
                OUTCOME_READINESS: len(readiness_samples),
                OUTCOME_STRENGTH_AGGREGATE: len(strength_aggregate_samples),
                OUTCOME_STRENGTH_PER_EXERCISE: strength_per_exercise_windows,
            }

        projection_data = {
            "status": "ok" if has_ok else "insufficient_data",
            "engine": telemetry_engine,
            "generated_at": datetime.now(timezone.utc).isoformat(),
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
            "events_processed": len(rows),
            "observed_days": len(observed_days),
            "windows_evaluated": windows_evaluated,
            "treated_windows": treated_windows,
            "outcome_windows": outcome_windows,
            "minimum_segment_samples": segment_min_samples,
            "insightful_interventions": sum(
                1 for payload in intervention_results.values() if payload.get("status") == "ok"
            ),
            "insightful_outcomes": insightful_outcomes,
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
            "Updated causal_inference for user=%s (days=%d, windows=%d, ok=%s)",
            user_id,
            len(daily_context),
            windows_evaluated,
            has_ok,
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
