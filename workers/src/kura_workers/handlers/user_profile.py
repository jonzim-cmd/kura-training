"""User Profile projection handler.

Builds a meta-projection: "What does Kura know about this user?"
Reacts to all relevant event types and aggregates into a single profile.

Full recompute on every event — idempotent by design.
"""

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler

logger = logging.getLogger(__name__)


@projection_handler(
    "set.logged",
    "exercise.alias_created",
    "preference.set",
    "goal.set",
)
async def update_user_profile(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of user_profile projection from all user events."""
    user_id = payload["user_id"]
    event_id = payload["event_id"]

    # Fetch all relevant events for this user in one query
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type IN ('set.logged', 'exercise.alias_created', 'preference.set', 'goal.set')
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    if not rows:
        return

    # Build profile from events
    aliases: dict[str, str] = {}
    preferences: dict[str, Any] = {}
    goals: list[dict[str, Any]] = []
    exercises_logged: set[str] = set()
    total_events = 0
    total_set_logged = 0
    # Data quality tracking
    events_without_exercise_id = 0
    raw_exercises_without_id: set[str] = set()
    first_event = rows[0]["timestamp"]
    last_event = rows[-1]["timestamp"]
    last_event_id_value = rows[-1]["id"]

    for row in rows:
        event_type = row["event_type"]
        data = row["data"]
        total_events += 1

        if event_type == "set.logged":
            total_set_logged += 1
            # Track exercises (prefer exercise_id over exercise)
            exercise_id = data.get("exercise_id", "")
            exercise = data.get("exercise", "")
            key = (exercise_id or exercise).strip().lower()
            if key:
                exercises_logged.add(key)
            # Track data quality
            if not exercise_id.strip():
                events_without_exercise_id += 1
                if key:
                    raw_exercises_without_id.add(key)

        elif event_type == "exercise.alias_created":
            alias = data.get("alias", "").strip()
            target = data.get("exercise_id", "").strip().lower()
            if alias and target:
                aliases[alias] = target

        elif event_type == "preference.set":
            pref_key = data.get("key", "")
            pref_value = data.get("value")
            if pref_key:
                preferences[pref_key] = pref_value

        elif event_type == "goal.set":
            goals.append(data)

    # Resolve exercises through alias map: "kniebeuge" → "barbell_back_squat"
    alias_lookup = {a.strip().lower(): target for a, target in aliases.items()}
    resolved_exercises: set[str] = set()
    for ex in exercises_logged:
        resolved_exercises.add(alias_lookup.get(ex, ex))

    # Discover existing dimensions from projections table
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT projection_type, array_agg(key ORDER BY key) as keys,
                   max(updated_at) as last_updated, count(*) as count
            FROM projections
            WHERE user_id = %s AND projection_type != 'user_profile'
            GROUP BY projection_type
            """,
            (user_id,),
        )
        dim_rows = await cur.fetchall()

    dimensions: dict[str, Any] = {}
    for dim_row in dim_rows:
        ptype = dim_row["projection_type"]
        entry: dict[str, Any] = {
            "last_updated": dim_row["last_updated"].isoformat(),
        }
        if ptype == "exercise_progression":
            entry["exercises"] = dim_row["keys"]
        dimensions[ptype] = entry

    # Compute data quality: unresolved exercises
    unresolved_exercises = sorted(
        ex for ex in raw_exercises_without_id
        if ex not in alias_lookup
    )

    projection_data = {
        "exercises_logged": sorted(resolved_exercises),
        "aliases": aliases,
        "preferences": preferences,
        "goals": goals,
        "total_events": total_events,
        "first_event": first_event.isoformat(),
        "last_event": last_event.isoformat(),
        "dimensions": dimensions,
        "data_quality": {
            "total_set_logged_events": total_set_logged,
            "events_without_exercise_id": events_without_exercise_id,
            "unresolved_exercises": unresolved_exercises,
        },
    }

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'user_profile', 'me', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), str(last_event_id_value)),
        )

    logger.info(
        "Updated user_profile for user=%s (exercises=%d, aliases=%d, prefs=%d)",
        user_id, len(resolved_exercises), len(aliases), len(preferences),
    )
