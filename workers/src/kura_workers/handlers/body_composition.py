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
from ..utils import (
    get_retracted_event_ids,
    load_timezone_preference,
    local_date_for_timezone,
    merge_observed_attributes,
    resolve_timezone_context,
    separate_known_unknown,
)

logger = logging.getLogger(__name__)

_KNOWN_FIELDS_BODYWEIGHT: set[str] = {"weight_kg", "time_of_day", "conditions"}
_KNOWN_FIELDS_MEASUREMENT: set[str] = {"type", "value_cm", "side"}


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
    if data.get("target"):
        result["has_target"] = True
    return result


@projection_handler("bodyweight.logged", "measurement.logged", "weight_target.set", dimension_meta={
    "name": "body_composition",
    "description": "Body weight and measurements over time",
    "key_structure": "single overview per user",
    "projection_key": "overview",
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
    "output_schema": {
        "current_weight_kg": "number or null",
        "total_weigh_ins": "integer",
        "timezone_context": {
            "timezone": "IANA timezone used for day/week grouping (e.g. Europe/Berlin)",
            "source": "preference|assumed_default",
            "assumed": "boolean",
            "assumption_disclosure": "string|null",
        },
        "weight_trend": {
            "recent_entries": [{"date": "ISO 8601 date", "weight_kg": "number", "time_of_day": "string (optional)", "conditions": "string (optional)"}],
            "weekly_average": [{"week": "ISO 8601 week", "avg_weight_kg": "number", "measurements": "integer"}],
            "all_time": {"min_kg": "number", "max_kg": "number", "first_date": "ISO 8601 date", "latest_date": "ISO 8601 date", "total_entries": "integer"},
        },
        "measurements": {
            "<measurement_type>": {
                "current_cm": "number",
                "latest_date": "ISO 8601 date",
                "history": [{"date": "ISO 8601 date", "value_cm": "number", "side": "string (optional)"}],
                "all_time": {"min_cm": "number", "max_cm": "number", "total_entries": "integer"},
            },
        },
        "measurement_types": ["string"],
        "target": "object — from weight_target.set event data (optional)",
        "data_quality": {
            "anomalies": [{"event_id": "string", "field": "string", "value": "any", "expected_range": "[min, max]", "message": "string"}],
            "observed_attributes": {"<event_type>": {"<field>": "integer — count"}},
        },
    },
    "manifest_contribution": _manifest_contribution,
})
async def update_body_composition(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of body_composition projection."""
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)
    timezone_pref = await load_timezone_preference(conn, user_id, retracted_ids)
    timezone_context = resolve_timezone_context(timezone_pref)
    timezone_name = timezone_context["timezone"]

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

    # Filter retracted events
    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    # Fetch latest non-retracted weight target (latest event wins)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'weight_target.set'
            ORDER BY timestamp DESC
            """,
            (user_id,),
        )
        target_rows = await cur.fetchall()

    weight_target: dict[str, Any] | None = None
    for tr in target_rows:
        if str(tr["id"]) not in retracted_ids:
            weight_target = tr["data"]
            break

    if not rows and not weight_target:
        # Clean up: delete any existing projection (all events retracted)
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM projections WHERE user_id = %s AND projection_type = 'body_composition' AND key = 'overview'",
                (user_id,),
            )
        return

    # Determine last_event_id: latest non-retracted event or target
    if rows:
        last_event_id = rows[-1]["id"]
    else:
        # Only target remains — use first non-retracted target row
        last_event_id = next(tr["id"] for tr in target_rows if str(tr["id"]) not in retracted_ids)

    # Process bodyweight entries
    weight_by_week: dict[str, list[float]] = defaultdict(list)
    all_weights: list[dict[str, Any]] = []

    # Process measurement entries
    measurements_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)

    anomalies: list[dict[str, Any]] = []
    observed_attr_counts: dict[str, dict[str, int]] = {}
    prev_weight: float | None = None
    prev_weight_date: date | None = None

    for row in rows:
        data = row["data"]
        ts = row["timestamp"]
        d = local_date_for_timezone(ts, timezone_name)
        event_type = row["event_type"]

        if event_type == "bodyweight.logged":
            _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS_BODYWEIGHT)
            merge_observed_attributes(observed_attr_counts, event_type, unknown)
            try:
                weight = float(data["weight_kg"])
            except (KeyError, ValueError, TypeError):
                logger.warning("Skipping bodyweight event %s: invalid weight_kg", row["id"])
                continue

            # Anomaly detection: absolute bounds
            if weight < 20 or weight > 300:
                anomalies.append({
                    "event_id": str(row["id"]),
                    "field": "weight_kg",
                    "value": weight,
                    "expected_range": [20, 300],
                    "message": f"Bodyweight {weight}kg outside plausible range on {d.isoformat()}",
                })

            # Anomaly detection: day-over-day change > 5kg
            if prev_weight is not None and prev_weight_date is not None:
                days_between = (d - prev_weight_date).days
                if days_between <= 2 and abs(weight - prev_weight) > 5:
                    anomalies.append({
                        "event_id": str(row["id"]),
                        "field": "weight_kg",
                        "value": weight,
                        "expected_range": [prev_weight - 5, prev_weight + 5],
                        "message": (
                            f"Weight changed {weight - prev_weight:+.1f}kg in "
                            f"{days_between} day(s) ({prev_weight}kg → {weight}kg)"
                        ),
                    })
            prev_weight = weight
            prev_weight_date = d

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
            _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS_MEASUREMENT)
            merge_observed_attributes(observed_attr_counts, event_type, unknown)
            mtype = data.get("type", "").strip().lower()
            try:
                value = float(data["value_cm"])
            except (KeyError, ValueError, TypeError):
                logger.warning("Skipping measurement event %s: invalid value_cm", row["id"])
                continue
            if not mtype:
                continue

            # Anomaly detection: measurement bounds
            if value < 1 or value > 300:
                anomalies.append({
                    "event_id": str(row["id"]),
                    "field": "value_cm",
                    "value": value,
                    "expected_range": [1, 300],
                    "message": f"Measurement {mtype} = {value}cm outside plausible range on {d.isoformat()}",
                })

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

    projection_data: dict[str, Any] = {
        "current_weight_kg": all_weights[-1]["weight_kg"] if all_weights else None,
        "total_weigh_ins": len(all_weights),
        "timezone_context": timezone_context,
        "weight_trend": weight_trend,
        "measurements": measurements,
        "measurement_types": sorted(measurements_by_type.keys()),
        "data_quality": {
            "anomalies": anomalies,
            "observed_attributes": observed_attr_counts,
        },
    }

    if weight_target:
        projection_data["target"] = weight_target

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
        (
            "Updated body_composition for user=%s "
            "(weigh_ins=%d, measurement_types=%d, timezone=%s, assumed=%s)"
        ),
        user_id,
        len(all_weights),
        len(measurements_by_type),
        timezone_name,
        timezone_context["assumed"],
    )
