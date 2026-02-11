"""Readiness inference projection handler.

Builds a probabilistic daily readiness score from recovery and training load
signals (sleep, energy, soreness, and set volume).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..inference_engine import run_readiness_inference
from ..registry import projection_handler
from ..utils import get_retracted_event_ids


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


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not projection_rows:
        return {}
    data = projection_rows[0]["data"]
    readiness_today = data.get("readiness_today", {})
    return {
        "state": readiness_today.get("state"),
        "readiness_mean": readiness_today.get("mean"),
    }


@projection_handler("set.logged", "sleep.logged", "soreness.logged", "energy.logged", dimension_meta={
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
        },
    },
    "manifest_contribution": _manifest_contribution,
})
async def update_readiness_inference(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type IN ('set.logged', 'sleep.logged', 'soreness.logged', 'energy.logged')
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
        return

    per_day: dict[str, dict[str, Any]] = defaultdict(dict)
    load_values: list[float] = []

    for row in rows:
        ts = row["timestamp"]
        d: date = ts.date()
        key = d.isoformat()
        data = row["data"] or {}
        event_type = row["event_type"]
        bucket = per_day[key]

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
            prev = bucket.get("soreness_sum", 0.0) + sev
            cnt = bucket.get("soreness_count", 0) + 1
            bucket["soreness_sum"] = prev
            bucket["soreness_count"] = cnt
        elif event_type == "set.logged":
            try:
                weight = float(data.get("weight_kg", data.get("weight", 0)))
                reps = float(data.get("reps", 0))
            except (TypeError, ValueError):
                continue
            volume = max(0.0, weight * reps)
            bucket["load_volume"] = bucket.get("load_volume", 0.0) + volume

    for values in per_day.values():
        if values.get("load_volume", 0.0) > 0.0:
            load_values.append(float(values["load_volume"]))
    load_baseline = max(1.0, _median(load_values))

    observations: list[float] = []
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
        soreness_penalty = _clamp(soreness_avg / 5.0, 0.0, 1.0)

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
        observations.append(score)

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

    inference = run_readiness_inference(observations)

    projection_data: dict[str, Any] = {
        "daily_scores": daily_scores[-60:],
        "engine": inference.get("engine"),
        "diagnostics": inference.get("diagnostics", {}),
        "data_quality": {
            "days_with_observations": len(observations),
            "insufficient_data": inference.get("status") == "insufficient_data",
        },
    }

    if inference.get("status") == "insufficient_data":
        projection_data["status"] = "insufficient_data"
        projection_data["required_points"] = inference.get("required_points", 5)
        projection_data["observed_points"] = inference.get("observed_points", len(observations))
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
