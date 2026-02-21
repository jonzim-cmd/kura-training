"""Nutrition dimension handler.

Reacts to meal.logged events and computes nutritional intake patterns:
- Daily totals (calories, macros)
- Weekly averages
- Meal frequency and timing

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
    merge_observed_attributes,
    normalize_temporal_point,
    resolve_timezone_context,
    separate_known_unknown,
)

logger = logging.getLogger(__name__)

_KNOWN_FIELDS: set[str] = {
    "calories", "protein_g", "carbs_g", "fat_g",
    "fiber_g", "added_sugar_g", "sodium_mg", "saturated_fat_g", "alcohol_units",
    "meal_type", "description",
}


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
    if data.get("total_meals"):
        result["total_meals"] = data["total_meals"]
    if data.get("tracking_days"):
        result["tracking_days"] = data["tracking_days"]
    latest = data.get("latest_date")
    if latest:
        result["latest_date"] = latest
    if data.get("target"):
        result["has_target"] = True
    return result


@projection_handler("meal.logged", "nutrition_target.set", dimension_meta={
    "name": "nutrition",
    "description": "Nutritional intake: calories, macros, meal patterns",
    "key_structure": "single overview per user",
    "projection_key": "overview",
    "granularity": ["meal", "day", "week"],
    "relates_to": {
        "training_timeline": {"join": "day", "why": "nutrition timing vs training days"},
        "body_composition": {"join": "week", "why": "intake vs weight trends"},
        "recovery": {"join": "day", "why": "nutrition impact on recovery"},
    },
    "context_seeds": [
        "nutrition_goals",
        "dietary_preferences",
        "meal_schedule",
    ],
    "output_schema": {
        "total_meals": "integer",
        "tracking_days": "integer",
        "latest_date": "ISO 8601 date or null",
        "timezone_context": {
            "timezone": "IANA timezone used for day/week grouping (e.g. Europe/Berlin)",
            "source": "preference|assumed_default",
            "assumed": "boolean",
            "assumption_disclosure": "string|null",
        },
        "daily_totals": [{
            "date": "ISO 8601 date",
            "calories": "number",
            "protein_g": "number",
            "carbs_g": "number",
            "fat_g": "number",
            "fiber_g": "number",
            "added_sugar_g": "number",
            "sodium_mg": "number",
            "saturated_fat_g": "number",
            "alcohol_units": "number",
            "meals": "integer",
        }],
        "weekly_average": [{
            "week": "ISO 8601 week",
            "avg_calories": "number",
            "avg_protein_g": "number",
            "avg_carbs_g": "number",
            "avg_fat_g": "number",
            "avg_fiber_g": "number",
            "avg_added_sugar_g": "number",
            "avg_sodium_mg": "number",
            "avg_saturated_fat_g": "number",
            "avg_alcohol_units": "number",
            "tracking_days": "integer",
            "total_meals": "integer",
        }],
        "recent_meals": [{
            "date": "ISO 8601 date",
            "calories": "number",
            "protein_g": "number (optional)",
            "carbs_g": "number (optional)",
            "fat_g": "number (optional)",
            "fiber_g": "number (optional)",
            "added_sugar_g": "number (optional)",
            "sodium_mg": "number (optional)",
            "saturated_fat_g": "number (optional)",
            "alcohol_units": "number (optional)",
            "meal_type": "string (optional)",
            "description": "string (optional)",
        }],
        "target": "object — from nutrition_target.set event data (optional)",
        "data_quality": {
            "anomalies": [{"event_id": "string", "field": "string", "value": "any", "expected_range": "[min, max]", "message": "string"}],
            "observed_attributes": {"<event_type>": {"<field>": "integer — count"}},
            "temporal_conflicts": {"<conflict_type>": "integer — number of events with that conflict"},
        },
    },
    "manifest_contribution": _manifest_contribution,
})
async def update_nutrition(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of nutrition projection."""
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)
    timezone_pref = await load_timezone_preference(conn, user_id, retracted_ids)
    timezone_context = resolve_timezone_context(timezone_pref)
    timezone_name = timezone_context["timezone"]

    # Fetch meal events
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = 'meal.logged'
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    # Filter retracted events
    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    # Fetch latest non-retracted nutrition target
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'nutrition_target.set'
            ORDER BY timestamp DESC
            """,
            (user_id,),
        )
        target_rows = await cur.fetchall()

    target: dict[str, Any] | None = None
    for tr in target_rows:
        if str(tr["id"]) not in retracted_ids:
            target = tr["data"]
            break

    if not rows and not target:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM projections WHERE user_id = %s AND projection_type = 'nutrition' AND key = 'overview'",
                (user_id,),
            )
        return

    if rows:
        last_event_id = rows[-1]["id"]
    else:
        # Only target remains
        last_event_id = next(tr["id"] for tr in target_rows if str(tr["id"]) not in retracted_ids)

    # Per-day aggregation
    day_data: dict[date, dict[str, Any]] = defaultdict(
        lambda: {
            "calories": 0.0,
            "protein_g": 0.0,
            "carbs_g": 0.0,
            "fat_g": 0.0,
            "fiber_g": 0.0,
            "added_sugar_g": 0.0,
            "sodium_mg": 0.0,
            "saturated_fat_g": 0.0,
            "alcohol_units": 0.0,
            "meals": 0,
            "meal_types": set(),
        }
    )

    # Per-week aggregation
    week_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calories": 0.0,
            "protein_g": 0.0,
            "carbs_g": 0.0,
            "fat_g": 0.0,
            "fiber_g": 0.0,
            "added_sugar_g": 0.0,
            "sodium_mg": 0.0,
            "saturated_fat_g": 0.0,
            "alcohol_units": 0.0,
            "meals": 0,
            "tracking_days": set(),
        }
    )

    all_meals: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    observed_attr_counts: dict[str, dict[str, int]] = {}
    temporal_conflicts: dict[str, int] = {}

    for row in rows:
        data = row["data"]
        temporal = normalize_temporal_point(
            row["timestamp"],
            timezone_name=timezone_name,
            data=data,
            metadata=row.get("metadata") or {},
        )
        d = temporal.local_date
        w = temporal.iso_week
        for conflict in temporal.conflicts:
            temporal_conflicts[conflict] = temporal_conflicts.get(conflict, 0) + 1

        # Decision 10: track unknown fields
        _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS)
        merge_observed_attributes(observed_attr_counts, "meal.logged", unknown)

        def _read_float(field: str) -> float:
            try:
                return float(data.get(field, 0))
            except (ValueError, TypeError):
                return 0.0

        # Extract nutritional values (all optional, tolerant parsing)
        calories = _read_float("calories")
        protein = _read_float("protein_g")
        carbs = _read_float("carbs_g")
        fat = _read_float("fat_g")
        fiber = _read_float("fiber_g")
        added_sugar = _read_float("added_sugar_g")
        sodium = _read_float("sodium_mg")
        saturated_fat = _read_float("saturated_fat_g")
        alcohol_units = _read_float("alcohol_units")

        # Anomaly detection: single meal bounds
        if calories < 0 or calories > 5000:
            anomalies.append({
                "event_id": str(row["id"]),
                "field": "calories",
                "value": calories,
                "expected_range": [0, 5000],
                "message": f"Single meal with {calories:.0f} kcal on {d.isoformat()}",
            })
        for macro_name, macro_val in [("protein_g", protein), ("carbs_g", carbs), ("fat_g", fat)]:
            if macro_val < 0 or macro_val > 500:
                anomalies.append({
                    "event_id": str(row["id"]),
                    "field": macro_name,
                    "value": macro_val,
                    "expected_range": [0, 500],
                    "message": f"Single meal with {macro_val:.0f}g {macro_name.replace('_g', '')} on {d.isoformat()}",
                })
        for field_name, field_value, expected_range, unit in [
            ("fiber_g", fiber, [0, 200], "g"),
            ("added_sugar_g", added_sugar, [0, 300], "g"),
            ("sodium_mg", sodium, [0, 10000], "mg"),
            ("saturated_fat_g", saturated_fat, [0, 200], "g"),
            ("alcohol_units", alcohol_units, [0, 20], "units"),
        ]:
            if field_value < expected_range[0] or field_value > expected_range[1]:
                anomalies.append({
                    "event_id": str(row["id"]),
                    "field": field_name,
                    "value": field_value,
                    "expected_range": expected_range,
                    "message": (
                        f"Single meal with {field_value:.0f}{unit} {field_name} "
                        f"on {d.isoformat()}"
                    ),
                })

        meal_type = data.get("meal_type", "").strip().lower()

        # Day aggregation
        day_data[d]["calories"] += calories
        day_data[d]["protein_g"] += protein
        day_data[d]["carbs_g"] += carbs
        day_data[d]["fat_g"] += fat
        day_data[d]["fiber_g"] += fiber
        day_data[d]["added_sugar_g"] += added_sugar
        day_data[d]["sodium_mg"] += sodium
        day_data[d]["saturated_fat_g"] += saturated_fat
        day_data[d]["alcohol_units"] += alcohol_units
        day_data[d]["meals"] += 1
        if meal_type:
            day_data[d]["meal_types"].add(meal_type)

        # Week aggregation
        week_data[w]["calories"] += calories
        week_data[w]["protein_g"] += protein
        week_data[w]["carbs_g"] += carbs
        week_data[w]["fat_g"] += fat
        week_data[w]["fiber_g"] += fiber
        week_data[w]["added_sugar_g"] += added_sugar
        week_data[w]["sodium_mg"] += sodium
        week_data[w]["saturated_fat_g"] += saturated_fat
        week_data[w]["alcohol_units"] += alcohol_units
        week_data[w]["meals"] += 1
        week_data[w]["tracking_days"].add(d)

        entry: dict[str, Any] = {
            "date": d.isoformat(),
            "calories": calories,
        }
        if protein:
            entry["protein_g"] = protein
        if carbs:
            entry["carbs_g"] = carbs
        if fat:
            entry["fat_g"] = fat
        if fiber:
            entry["fiber_g"] = fiber
        if added_sugar:
            entry["added_sugar_g"] = added_sugar
        if sodium:
            entry["sodium_mg"] = sodium
        if saturated_fat:
            entry["saturated_fat_g"] = saturated_fat
        if alcohol_units:
            entry["alcohol_units"] = alcohol_units
        if meal_type:
            entry["meal_type"] = meal_type
        if "description" in data:
            entry["description"] = data["description"]
        all_meals.append(entry)

    # Build daily totals (last 30 tracking days)
    sorted_days = sorted(day_data.keys(), reverse=True)[:30]
    sorted_days.reverse()
    daily_totals = [
        {
            "date": d.isoformat(),
            "calories": round(day_data[d]["calories"], 0),
            "protein_g": round(day_data[d]["protein_g"], 1),
            "carbs_g": round(day_data[d]["carbs_g"], 1),
            "fat_g": round(day_data[d]["fat_g"], 1),
            "fiber_g": round(day_data[d]["fiber_g"], 1),
            "added_sugar_g": round(day_data[d]["added_sugar_g"], 1),
            "sodium_mg": round(day_data[d]["sodium_mg"], 0),
            "saturated_fat_g": round(day_data[d]["saturated_fat_g"], 1),
            "alcohol_units": round(day_data[d]["alcohol_units"], 1),
            "meals": day_data[d]["meals"],
        }
        for d in sorted_days
    ]

    # Build weekly averages (last 26 weeks)
    sorted_weeks = sorted(week_data.keys(), reverse=True)[:26]
    sorted_weeks.reverse()
    weekly_average = []
    for wk in sorted_weeks:
        wd = week_data[wk]
        tracking_days = len(wd["tracking_days"])
        weekly_average.append({
            "week": wk,
            "avg_calories": round(wd["calories"] / tracking_days, 0) if tracking_days else 0,
            "avg_protein_g": round(wd["protein_g"] / tracking_days, 1) if tracking_days else 0,
            "avg_carbs_g": round(wd["carbs_g"] / tracking_days, 1) if tracking_days else 0,
            "avg_fat_g": round(wd["fat_g"] / tracking_days, 1) if tracking_days else 0,
            "avg_fiber_g": round(wd["fiber_g"] / tracking_days, 1) if tracking_days else 0,
            "avg_added_sugar_g": round(wd["added_sugar_g"] / tracking_days, 1) if tracking_days else 0,
            "avg_sodium_mg": round(wd["sodium_mg"] / tracking_days, 0) if tracking_days else 0,
            "avg_saturated_fat_g": round(wd["saturated_fat_g"] / tracking_days, 1) if tracking_days else 0,
            "avg_alcohol_units": round(wd["alcohol_units"] / tracking_days, 1) if tracking_days else 0,
            "tracking_days": tracking_days,
            "total_meals": wd["meals"],
        })

    # Recent meals (last 20)
    recent_meals = all_meals[-20:]

    projection_data: dict[str, Any] = {
        "total_meals": len(all_meals),
        "tracking_days": len(day_data),
        "latest_date": max(day_data.keys()).isoformat() if day_data else None,
        "timezone_context": timezone_context,
        "daily_totals": daily_totals,
        "weekly_average": weekly_average,
        "recent_meals": recent_meals,
        "data_quality": {
            "anomalies": anomalies,
            "observed_attributes": observed_attr_counts,
            "temporal_conflicts": temporal_conflicts,
        },
    }

    if target:
        projection_data["target"] = target

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'nutrition', 'overview', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), str(last_event_id)),
        )

    logger.info(
        "Updated nutrition for user=%s (meals=%d, days=%d, timezone=%s, assumed=%s)",
        user_id,
        len(all_meals),
        len(day_data),
        timezone_name,
        timezone_context["assumed"],
    )
