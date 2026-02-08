"""Exercise Progression projection handler.

Reacts to set.logged events and computes per-exercise statistics:
- Estimated 1RM (Epley formula)
- Total sessions, sets, volume
- Personal records
- Recent session history (last 5)

Full recompute on every event — idempotent by design.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..utils import resolve_exercise_key

logger = logging.getLogger(__name__)


def _epley_1rm(weight_kg: float, reps: int) -> float:
    """Estimate 1RM using the Epley formula. Returns 0 for invalid inputs."""
    if reps <= 0 or weight_kg <= 0:
        return 0.0
    if reps == 1:
        return weight_kg
    return weight_kg * (1 + reps / 30)


def _iso_week(d) -> str:
    """Return ISO week string like '2026-W06'."""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract summary for user_profile manifest (Decision 7)."""
    return {"exercises": [r["key"] for r in projection_rows]}


@projection_handler("set.logged", dimension_meta={
    "name": "exercise_progression",
    "description": "Strength progression per exercise over time",
    "key_structure": "one per exercise (exercise_id as key)",
    "granularity": ["set", "week"],
    "relates_to": {
        "training_timeline": {"join": "week", "why": "frequency vs progression"},
        "user_profile": {"join": "exercises_logged", "why": "which exercises to query"},
    },
    "context_seeds": [
        "exercise_vocabulary",
        "training_modality",
        "experience_level",
        "typical_rep_ranges",
    ],
    "manifest_contribution": _manifest_contribution,
})
async def update_exercise_progression(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of exercise_progression projection for the affected exercise."""
    user_id = payload["user_id"]
    event_id = payload["event_id"]

    # Get the exercise name from this specific event
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT data FROM events WHERE id = %s", (event_id,))
        row = await cur.fetchone()
        if row is None:
            logger.warning("Event %s not found, skipping", event_id)
            return

    exercise_key = resolve_exercise_key(row["data"])
    if not exercise_key:
        logger.warning("Event %s has no exercise field, skipping", event_id)
        return

    # Full recompute: fetch ALL set.logged events for this user+exercise
    # Match on both exercise_id and normalized exercise name
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'set.logged'
              AND (
                  lower(trim(data->>'exercise_id')) = %s
                  OR lower(trim(data->>'exercise')) = %s
              )
            ORDER BY timestamp ASC
            """,
            (user_id, exercise_key, exercise_key),
        )
        rows = await cur.fetchall()

    if not rows:
        return

    # Compute statistics
    total_sets = len(rows)
    total_volume_kg = 0.0
    best_1rm = 0.0
    best_1rm_date: datetime | None = None
    session_dates: set[str] = set()
    recent_sets: list[dict[str, Any]] = []
    last_event_id = rows[-1]["id"]

    # Weekly aggregation for weekly_history
    week_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"estimated_1rm": 0.0, "total_sets": 0, "total_volume_kg": 0.0, "max_weight_kg": 0.0}
    )

    for row in rows:
        data = row["data"]
        ts: datetime = row["timestamp"]

        # Support both weight_kg (convention) and weight (legacy)
        try:
            weight = float(data.get("weight_kg", data.get("weight", 0)))
            reps = int(data.get("reps", 0))
        except (ValueError, TypeError):
            logger.warning("Skipping event %s: invalid weight/reps data", row["id"])
            continue

        volume = weight * reps
        total_volume_kg += volume

        session_dates.add(ts.date().isoformat())

        e1rm = _epley_1rm(weight, reps)
        if e1rm > best_1rm:
            best_1rm = e1rm
            best_1rm_date = ts

        # Weekly aggregation
        week_key = _iso_week(ts.date())
        w = week_data[week_key]
        w["total_sets"] += 1
        w["total_volume_kg"] += volume
        if e1rm > w["estimated_1rm"]:
            w["estimated_1rm"] = e1rm
        if weight > w["max_weight_kg"]:
            w["max_weight_kg"] = weight

        set_entry: dict[str, Any] = {
            "timestamp": ts.isoformat(),
            "weight_kg": weight,
            "reps": reps,
            "estimated_1rm": round(e1rm, 1),
        }
        # Include optional fields if present
        if "rpe" in data:
            try:
                set_entry["rpe"] = float(data["rpe"])
            except (ValueError, TypeError):
                pass
        if "set_type" in data:
            set_entry["set_type"] = data["set_type"]

        recent_sets.append(set_entry)

    # Last 5 sessions worth of sets (by date)
    sorted_dates = sorted(session_dates, reverse=True)[:5]
    recent_date_set = set(sorted_dates)
    recent_sessions = [s for s in recent_sets if s["timestamp"][:10] in recent_date_set]
    recent_sessions.reverse()

    # Build weekly_history: last 26 weeks, chronological
    sorted_weeks = sorted(week_data.keys(), reverse=True)[:26]
    sorted_weeks.reverse()
    weekly_history = [
        {
            "week": wk,
            "estimated_1rm": round(week_data[wk]["estimated_1rm"], 1),
            "total_sets": week_data[wk]["total_sets"],
            "total_volume_kg": round(week_data[wk]["total_volume_kg"], 1),
            "max_weight_kg": round(week_data[wk]["max_weight_kg"], 1),
        }
        for wk in sorted_weeks
    ]

    projection_data = {
        "exercise": exercise_key,
        "estimated_1rm": round(best_1rm, 1),
        "estimated_1rm_date": best_1rm_date.isoformat() if best_1rm_date else None,
        "total_sessions": len(session_dates),
        "total_sets": total_sets,
        "total_volume_kg": round(total_volume_kg, 1),
        "recent_sessions": recent_sessions,
        "weekly_history": weekly_history,
    }

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'exercise_progression', %s, %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, exercise_key, json.dumps(projection_data), str(last_event_id)),
        )

    logger.info(
        "Updated exercise_progression for user=%s exercise=%s (sets=%d, 1rm=%.1f)",
        user_id, exercise_key, total_sets, best_1rm,
    )
