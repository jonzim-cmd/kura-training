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
from ..inference_telemetry import (
    INFERENCE_ERROR_INSUFFICIENT_DATA,
    classify_inference_error,
    safe_record_inference_run,
)
from ..registry import projection_handler
from ..utils import get_retracted_event_ids

logger = logging.getLogger(__name__)


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
    for name, payload in interventions.items():
        effect = payload.get("effect") or {}
        mean_ate = _safe_float(effect.get("mean_ate"), default=0.0)
        if abs(mean_ate) > abs(strongest_value):
            strongest_name = name
            strongest_value = mean_ate
    if strongest_name is not None:
        result["strongest_signal"] = {
            "intervention": strongest_name,
            "mean_ate": round(strongest_value, 4),
        }
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
            "assumptions": [{"code": "string", "description": "string"}],
            "interventions": {
                "<intervention_name>": {
                    "status": "string",
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
                },
            },
            "machine_caveats": [{
                "intervention": "string",
                "code": "string",
                "severity": "string",
                "details": "object",
            }],
            "evidence_window": {
                "days_considered": "integer",
                "windows_evaluated": "integer",
                "history_days_required": "integer",
            },
            "daily_context": [{
                "date": "ISO 8601 date",
                "readiness_score": "number [0,1]",
                "sleep_hours": "number",
                "protein_g": "number",
                "load_volume": "number",
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
                    "program_change_event": bool(bucket["program_events"]),
                    "sleep_target_event": bool(bucket["sleep_target_events"]),
                    "nutrition_target_event": bool(bucket["nutrition_target_events"]),
                }
            )

        history_days_required = 7
        windows_evaluated = 0
        samples_by_intervention: dict[str, list[dict[str, Any]]] = {
            "program_change": [],
            "nutrition_shift": [],
            "sleep_intervention": [],
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

            sleep_shift = _safe_float(current.get("sleep_hours")) >= (baseline_sleep + 0.75)
            nutrition_shift = _safe_float(current.get("protein_g")) >= (baseline_protein + 20.0)

            common = {
                "outcome": _safe_float(next_day.get("readiness_score"), default=0.0),
                "confounders": {
                    "baseline_readiness": baseline_readiness,
                    "baseline_sleep_hours": baseline_sleep,
                    "baseline_load_volume": baseline_load,
                    "baseline_protein_g": baseline_protein,
                    "current_readiness": _safe_float(
                        current.get("readiness_score"), default=0.0
                    ),
                    "current_sleep_hours": _safe_float(current.get("sleep_hours"), default=0.0),
                    "current_load_volume": _safe_float(current.get("load_volume"), default=0.0),
                    "current_protein_g": _safe_float(current.get("protein_g"), default=0.0),
                    "current_calories": _safe_float(current.get("calories"), default=0.0),
                },
            }

            samples_by_intervention["program_change"].append(
                {
                    **common,
                    "treated": 1 if bool(current.get("program_change_event")) else 0,
                }
            )
            samples_by_intervention["nutrition_shift"].append(
                {
                    **common,
                    "treated": 1
                    if bool(current.get("nutrition_target_event")) or nutrition_shift
                    else 0,
                }
            )
            samples_by_intervention["sleep_intervention"].append(
                {
                    **common,
                    "treated": 1 if bool(current.get("sleep_target_event")) or sleep_shift else 0,
                }
            )

        min_samples = max(12, int(os.environ.get("KURA_CAUSAL_MIN_SAMPLES", "24")))
        bootstrap_samples = max(
            80, int(os.environ.get("KURA_CAUSAL_BOOTSTRAP_SAMPLES", "250"))
        )

        intervention_results: dict[str, Any] = {}
        treated_windows: dict[str, int] = {}
        machine_caveats: list[dict[str, Any]] = []
        has_ok = False

        for name, samples in samples_by_intervention.items():
            treated_windows[name] = sum(
                1
                for sample in samples
                if int(_safe_float(sample.get("treated"), default=0.0)) == 1
            )
            result = estimate_intervention_effect(
                samples,
                min_samples=min_samples,
                bootstrap_samples=bootstrap_samples,
            )
            intervention_results[name] = result
            has_ok = has_ok or result.get("status") == "ok"

            for caveat in result.get("caveats", []):
                machine_caveats.append(
                    {
                        "intervention": name,
                        "code": caveat.get("code"),
                        "severity": caveat.get("severity"),
                        "details": caveat.get("details", {}),
                    }
                )

        projection_data = {
            "status": "ok" if has_ok else "insufficient_data",
            "engine": telemetry_engine,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "outcome_definition": {
                "metric": "next_day_readiness_score",
                "horizon": "t+1 day",
                "notes": (
                    "Outcome is a readiness-style composite from sleep, energy, soreness, and load."
                ),
            },
            "assumptions": ASSUMPTIONS,
            "interventions": intervention_results,
            "machine_caveats": machine_caveats,
            "evidence_window": {
                "days_considered": len(daily_context),
                "windows_evaluated": windows_evaluated,
                "history_days_required": history_days_required,
            },
            "daily_context": daily_context[-60:],
            "data_quality": {
                "events_processed": len(rows),
                "observed_days": len(observed_days),
                "treated_windows": treated_windows,
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
            "insightful_interventions": sum(
                1 for payload in intervention_results.values() if payload.get("status") == "ok"
            ),
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
