"""Readiness inference projection handler.

Builds a probabilistic daily readiness score from recovery and training load
signals (sleep, energy, soreness, and set volume).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..inference_engine import run_readiness_inference, weekly_phase_from_date
from ..inference_telemetry import (
    INFERENCE_ERROR_INSUFFICIENT_DATA,
    classify_inference_error,
    safe_record_inference_run,
)
from ..population_priors import resolve_population_prior
from ..readiness_signals import build_readiness_daily_scores
from ..registry import projection_handler
from ..utils import (
    get_retracted_event_ids,
    load_timezone_preference,
    resolve_timezone_context,
)


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not projection_rows:
        return {}
    data = projection_rows[0]["data"]
    readiness_today = data.get("readiness_today", {})
    return {
        "state": readiness_today.get("state"),
        "readiness_mean": readiness_today.get("mean"),
    }


@projection_handler(
    "set.logged",
    "session.logged",
    "set.corrected",
    "sleep.logged",
    "soreness.logged",
    "energy.logged",
    "external.activity_imported",
    dimension_meta={
    "name": "readiness_inference",
    "description": "Bayesian day-level readiness estimate from recovery + load signals",
    "key_structure": "single overview per user",
    "projection_key": "overview",
    "granularity": ["day", "week"],
    "relates_to": {
        "recovery": {"join": "day", "why": "input signal source"},
        "training_timeline": {"join": "day", "why": "load/recovery interaction"},
        "strength_inference": {"join": "week", "why": "readiness vs progression shifts"},
    },
    "context_seeds": [
        "sleep_habits",
        "stress_factors",
        "training_frequency",
    ],
    "output_schema": {
        "timezone_context": {
            "timezone": "IANA timezone used for day/week grouping (e.g. Europe/Berlin)",
            "source": "preference|assumed_default",
            "assumed": "boolean",
            "assumption_disclosure": "string|null",
        },
        "readiness_today": {
            "mean": "number",
            "ci95": "[number, number]",
            "state": "string — high|moderate|low",
        },
        "baseline": {
            "posterior_mean": "number",
            "posterior_ci95": "[number, number]",
            "observations": "integer",
        },
        "dynamics": {
            "readiness": {
                "value": "number",
                "velocity_per_day": "number|null",
                "velocity_per_week": "number|null",
                "acceleration_per_day2": "number|null",
                "trajectory_code": "string",
                "phase": "string",
                "direction": "string",
                "momentum": "string",
                "confidence": "number [0,1]",
                "samples": "integer",
                "state": "string (optional)",
            },
        },
        "phase": {
            "projection_phase": "string",
            "weekly_cycle": {
                "day_of_week": "string|null",
                "phase": "string",
                "angle_deg": "number|null",
                "bucket_index": "integer|null",
            },
        },
        "population_prior": {
            "applied": "boolean",
            "cohort_key": "string (optional)",
            "target_key": "string (optional)",
            "participants_count": "integer (optional)",
            "sample_size": "integer (optional)",
            "blend_weight": "number (optional)",
            "computed_at": "ISO 8601 datetime (optional)",
        },
        "daily_scores": [{
            "date": "ISO 8601 date",
            "score": "number [0,1]",
            "components": {
                "sleep": "number [0,1]",
                "energy": "number [0,1]",
                "soreness_penalty": "number [0,1]",
                "load_penalty": "number [0,1]",
            },
        }],
        "diagnostics": "object",
        "data_quality": {
            "days_with_observations": "integer",
            "insufficient_data": "boolean",
            "temporal_conflicts": {"<conflict_type>": "integer"},
        },
    },
    "manifest_contribution": _manifest_contribution,
    },
)
async def update_readiness_inference(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    user_id = payload["user_id"]
    event_type = payload.get("event_type", "")
    started_at = datetime.now(timezone.utc)
    telemetry_engine = "none"

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
            projection_type="readiness_inference",
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

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, timestamp, event_type, data, metadata
                FROM events
                WHERE user_id = %s
                  AND event_type IN (
                      'set.logged',
                      'session.logged',
                      'set.corrected',
                      'sleep.logged',
                      'soreness.logged',
                      'energy.logged',
                      'external.activity_imported'
                  )
                ORDER BY timestamp ASC
                """,
                (user_id,),
            )
            rows = await cur.fetchall()

        rows = [r for r in rows if str(r["id"]) not in retracted_ids]
        if not rows:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM projections
                    WHERE user_id = %s
                      AND projection_type = 'readiness_inference'
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
            )
            return

        readiness_signals = build_readiness_daily_scores(rows, timezone_name=timezone_name)
        daily_scores = list(readiness_signals.get("daily_scores") or [])
        observations = [float(entry.get("score", 0.0)) for entry in daily_scores]
        day_offsets = [float(entry.get("day_offset", idx)) for idx, entry in enumerate(daily_scores)]
        observation_variances = [
            float(entry.get("observation_variance", 0.01))
            for entry in daily_scores
        ]

        population_prior = await resolve_population_prior(
            conn,
            user_id=user_id,
            projection_type="readiness_inference",
            target_key="overview",
            retracted_ids=retracted_ids,
        )
        inference = run_readiness_inference(
            observations,
            day_offsets=day_offsets,
            observation_variances=observation_variances,
            population_prior=population_prior,
        )
        telemetry_engine = str(inference.get("engine", "none") or "none")
        dynamics_snapshot = dict(inference.get("dynamics", {}))
        projection_phase = str(dynamics_snapshot.get("phase") or "unknown")
        latest_day = daily_scores[-1]["date"] if daily_scores else None

        projection_data: dict[str, Any] = {
            "timezone_context": timezone_context,
            "daily_scores": daily_scores[-60:],
            "dynamics": {"readiness": dynamics_snapshot},
            "phase": {
                "projection_phase": projection_phase,
                "weekly_cycle": weekly_phase_from_date(latest_day),
            },
            "engine": inference.get("engine"),
            "diagnostics": inference.get("diagnostics", {}),
            "population_prior": inference.get("population_prior", {"applied": False}),
            "data_quality": {
                "days_with_observations": len(observations),
                "insufficient_data": inference.get("status") == "insufficient_data",
                "temporal_conflicts": readiness_signals.get("temporal_conflicts", {}),
                "component_priors": readiness_signals.get("component_priors", {}),
                "load_baseline": readiness_signals.get("load_baseline"),
                "missing_signal_counts": readiness_signals.get("missing_signal_counts", {}),
                "days_with_missing_signals": sum(
                    1 for entry in daily_scores if (entry.get("missing_signals") or [])
                ),
            },
        }

        telemetry_status = "success"
        telemetry_error_taxonomy: str | None = None
        telemetry_diagnostics = dict(inference.get("diagnostics", {}))
        if isinstance(inference.get("population_prior"), dict):
            telemetry_diagnostics["population_prior"] = inference["population_prior"]
        if inference.get("status") == "insufficient_data":
            projection_data["status"] = "insufficient_data"
            projection_data["required_points"] = inference.get("required_points", 5)
            projection_data["observed_points"] = inference.get("observed_points", len(observations))
            telemetry_status = "skipped"
            telemetry_error_taxonomy = INFERENCE_ERROR_INSUFFICIENT_DATA
            telemetry_diagnostics.update(
                {
                    "skip_reason": "insufficient_data",
                    "required_points": inference.get("required_points", 5),
                    "observed_points": inference.get("observed_points", len(observations)),
                }
            )
        else:
            projection_data["readiness_today"] = inference["readiness_today"]
            projection_data["baseline"] = inference["baseline"]

        last_event_id = str(rows[-1]["id"])
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
                VALUES (%s, 'readiness_inference', 'overview', %s, 1, %s, NOW())
                ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                    data = EXCLUDED.data,
                    version = projections.version + 1,
                    last_event_id = EXCLUDED.last_event_id,
                    updated_at = NOW()
                """,
                (user_id, json.dumps(projection_data), last_event_id),
            )

        await _record(
            telemetry_status,
            {
                **telemetry_diagnostics,
                "event_type": event_type,
                "days_with_observations": len(observations),
            },
            error_taxonomy=telemetry_error_taxonomy,
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
