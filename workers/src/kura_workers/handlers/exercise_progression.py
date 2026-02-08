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
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler

logger = logging.getLogger(__name__)


def _epley_1rm(weight_kg: float, reps: int) -> float:
    """Estimate 1RM using the Epley formula. Returns 0 for invalid inputs."""
    if reps <= 0 or weight_kg <= 0:
        return 0.0
    if reps == 1:
        return weight_kg
    return weight_kg * (1 + reps / 30)


def _resolve_exercise_key(data: dict[str, Any]) -> str | None:
    """Resolve the canonical exercise key from event data.

    Prefers exercise_id (canonical) over exercise (free text).
    """
    exercise_id = data.get("exercise_id", "").strip().lower()
    if exercise_id:
        return exercise_id

    exercise = data.get("exercise", "").strip().lower()
    if exercise:
        return exercise

    return None


@projection_handler("set.logged")
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

    exercise_key = _resolve_exercise_key(row["data"])
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

    projection_data = {
        "exercise": exercise_key,
        "estimated_1rm": round(best_1rm, 1),
        "estimated_1rm_date": best_1rm_date.isoformat() if best_1rm_date else None,
        "total_sessions": len(session_dates),
        "total_sets": total_sets,
        "total_volume_kg": round(total_volume_kg, 1),
        "recent_sessions": recent_sessions,
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
