"""Body Composition dimension handler.

Reacts to bodyweight.logged and measurement.logged events.
Computes weight trends, measurement history, and all-time stats.

Full recompute on every event — idempotent by design.
"""

import json
import logging
from collections import defaultdict
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler

logger = logging.getLogger(__name__)


def _iso_week(d: date) -> str:
    """Return ISO week string like '2026-W06'."""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract summary for user_profile manifest."""
    if not projection_rows:
        return {}
    data = projection_rows[0]["data"]
    result: dict[str, Any] = {}
    if data.get("current_weight_kg") is not None:
        result["current_weight_kg"] = data["current_weight_kg"]
    if data.get("total_weigh_ins"):
        result["total_weigh_ins"] = data["total_weigh_ins"]
    measurement_types = data.get("measurement_types", [])
    if measurement_types:
        result["measurement_types"] = measurement_types
    return result


@projection_handler("bodyweight.logged", "measurement.logged", dimension_meta={
    "name": "body_composition",
    "description": "Body weight and measurements over time",
    "key_structure": "single overview per user",
    "granularity": ["day", "week", "all_time"],
    "relates_to": {
        "training_timeline": {"join": "week", "why": "weight changes vs training volume"},
        "recovery": {"join": "day", "why": "weight fluctuations vs sleep/recovery"},
    },
    "context_seeds": [
        "body_composition_goals",
        "weigh_in_habits",
        "measurement_preferences",
    ],
    "manifest_contribution": _manifest_contribution,
})
async def update_body_composition(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of body_composition projection."""
    user_id = payload["user_id"]

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type IN ('bodyweight.logged', 'measurement.logged')
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    if not rows:
        return

    last_event_id = rows[-1]["id"]

    # Process bodyweight entries
    weight_by_week: dict[str, list[float]] = defaultdict(list)
    all_weights: list[dict[str, Any]] = []

    # Process measurement entries
    measurements_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        data = row["data"]
        ts = row["timestamp"]
        d = ts.date()
        event_type = row["event_type"]

        if event_type == "bodyweight.logged":
            try:
                weight = float(data["weight_kg"])
            except (KeyError, ValueError, TypeError):
                logger.warning("Skipping bodyweight event %s: invalid weight_kg", row["id"])
                continue

            weight_by_week[_iso_week(d)].append(weight)
            entry: dict[str, Any] = {
                "date": d.isoformat(),
                "weight_kg": weight,
            }
            if "time_of_day" in data:
                entry["time_of_day"] = data["time_of_day"]
            if "conditions" in data:
                entry["conditions"] = data["conditions"]
            all_weights.append(entry)

        elif event_type == "measurement.logged":
            mtype = data.get("type", "").strip().lower()
            try:
                value = float(data["value_cm"])
            except (KeyError, ValueError, TypeError):
                logger.warning("Skipping measurement event %s: invalid value_cm", row["id"])
                continue
            if not mtype:
                continue

            mentry: dict[str, Any] = {
                "date": d.isoformat(),
                "value_cm": value,
            }
            if "side" in data:
                mentry["side"] = data["side"]
            measurements_by_type[mtype].append(mentry)

    # Build weight trend
    weight_trend: dict[str, Any] = {}

    if all_weights:
        # Recent entries: last 30
        weight_trend["recent_entries"] = all_weights[-30:]

        # Weekly averages: last 26 weeks
        sorted_weeks = sorted(weight_by_week.keys(), reverse=True)[:26]
        sorted_weeks.reverse()
        weight_trend["weekly_average"] = [
            {
                "week": wk,
                "avg_weight_kg": round(sum(weight_by_week[wk]) / len(weight_by_week[wk]), 1),
                "measurements": len(weight_by_week[wk]),
            }
            for wk in sorted_weeks
        ]

        # All-time stats
        all_weight_values = [w["weight_kg"] for w in all_weights]
        weight_trend["all_time"] = {
            "min_kg": round(min(all_weight_values), 1),
            "max_kg": round(max(all_weight_values), 1),
            "first_date": all_weights[0]["date"],
            "latest_date": all_weights[-1]["date"],
            "total_entries": len(all_weights),
        }

    # Build measurements
    measurements: dict[str, Any] = {}
    for mtype, entries in measurements_by_type.items():
        sorted_entries = sorted(entries, key=lambda e: e["date"])
        values = [e["value_cm"] for e in sorted_entries]
        measurements[mtype] = {
            "current_cm": sorted_entries[-1]["value_cm"],
            "latest_date": sorted_entries[-1]["date"],
            "history": sorted_entries[-20:],
            "all_time": {
                "min_cm": round(min(values), 1),
                "max_cm": round(max(values), 1),
                "total_entries": len(sorted_entries),
            },
        }

    projection_data = {
        "current_weight_kg": all_weights[-1]["weight_kg"] if all_weights else None,
        "total_weigh_ins": len(all_weights),
        "weight_trend": weight_trend,
        "measurements": measurements,
        "measurement_types": sorted(measurements_by_type.keys()),
    }

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'body_composition', 'overview', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), str(last_event_id)),
        )

    logger.info(
        "Updated body_composition for user=%s (weigh_ins=%d, measurement_types=%d)",
        user_id, len(all_weights), len(measurements_by_type),
    )
