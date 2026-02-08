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

from ..registry import register

logger = logging.getLogger(__name__)


def _epley_1rm(weight: float, reps: int) -> float:
    """Estimate 1RM using the Epley formula. Returns 0 for invalid inputs."""
    if reps <= 0 or weight <= 0:
        return 0.0
    if reps == 1:
        return weight
    return weight * (1 + reps / 30)


@register("projection.update")
async def handle_projection_update(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Route projection.update jobs to the appropriate handler based on event_type."""
    event_type = payload.get("event_type", "")

    if event_type == "set.logged":
        await _update_exercise_progression(conn, payload)
    else:
        logger.debug("No projection handler for event_type=%s, skipping", event_type)


async def _update_exercise_progression(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of exercise_progression projection for the affected exercise."""
    user_id = payload["user_id"]
    event_id = payload["event_id"]

    # Get the exercise name from this specific event
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT data FROM events WHERE id = %s",
            (event_id,),
        )
        row = await cur.fetchone()
        if row is None:
            logger.warning("Event %s not found, skipping", event_id)
            return

    event_data = row["data"]
    exercise_raw = event_data.get("exercise", "")
    if not exercise_raw:
        logger.warning("Event %s has no exercise field, skipping", event_id)
        return

    # Normalize exercise name (semantic layer comes later)
    exercise_key = exercise_raw.strip().lower()

    # Full recompute: fetch ALL set.logged events for this user+exercise
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'set.logged'
              AND lower(trim(data->>'exercise')) = %s
            ORDER BY timestamp ASC
            """,
            (user_id, exercise_key),
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
        weight = float(data.get("weight", 0))
        reps = int(data.get("reps", 0))
        ts: datetime = row["timestamp"]

        volume = weight * reps
        total_volume_kg += volume

        # Track sessions by date
        session_dates.add(ts.date().isoformat())

        # Track best estimated 1RM
        e1rm = _epley_1rm(weight, reps)
        if e1rm > best_1rm:
            best_1rm = e1rm
            best_1rm_date = ts

        # Collect for recent history
        recent_sets.append({
            "timestamp": ts.isoformat(),
            "weight": weight,
            "reps": reps,
            "estimated_1rm": round(e1rm, 1),
        })

    # Last 5 sessions worth of sets (by date)
    sorted_dates = sorted(session_dates, reverse=True)[:5]
    recent_date_set = set(sorted_dates)
    recent_sessions = [s for s in recent_sets if s["timestamp"][:10] in recent_date_set]
    # Reverse so most recent is first
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

    # UPSERT projection
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
        user_id,
        exercise_key,
        total_sets,
        best_1rm,
    )
