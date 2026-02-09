"""Exercise Progression projection handler.

Reacts to set.logged and exercise.alias_created events.
Computes per-exercise statistics:
- Estimated 1RM (Epley formula)
- Total sessions, sets, volume
- Personal records
- Recent session history (last 5)

Alias-aware: resolves through alias map, consolidates fragmented
projections when aliases are created, DELETEs stale alias-named projections.

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
from ..utils import (
    find_all_keys_for_canonical,
    get_alias_map,
    resolve_exercise_key,
    resolve_through_aliases,
)

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


@projection_handler("set.logged", "exercise.alias_created", dimension_meta={
    "name": "exercise_progression",
    "description": "Strength progression per exercise over time",
    "key_structure": "one per exercise (exercise_id as key)",
    "projection_key": "<exercise_id>",
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
    event_type = payload.get("event_type", "")

    # Load alias map for this user
    alias_map = await get_alias_map(conn, user_id)

    # Determine which canonical exercise to recompute
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT data FROM events WHERE id = %s", (event_id,))
        row = await cur.fetchone()
        if row is None:
            logger.warning("Event %s not found, skipping", event_id)
            return

    if event_type == "exercise.alias_created":
        # The canonical target is what we recompute
        canonical = row["data"].get("exercise_id", "").strip().lower()
        if not canonical:
            logger.warning("Alias event %s has no exercise_id, skipping", event_id)
            return
    else:
        # set.logged: resolve the exercise key through aliases
        raw_key = resolve_exercise_key(row["data"])
        if not raw_key:
            logger.warning("Event %s has no exercise field, skipping", event_id)
            return
        canonical = resolve_through_aliases(raw_key, alias_map)

    # Find all keys (canonical + aliases) that map to this canonical exercise
    all_keys = find_all_keys_for_canonical(canonical, alias_map)

    # Full recompute: fetch ALL set.logged events matching ANY of these keys
    all_keys_list = list(all_keys)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'set.logged'
              AND (
                  lower(trim(data->>'exercise_id')) = ANY(%s)
                  OR lower(trim(data->>'exercise')) = ANY(%s)
              )
            ORDER BY timestamp ASC
            """,
            (user_id, all_keys_list, all_keys_list),
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
        "exercise": canonical,
        "estimated_1rm": round(best_1rm, 1),
        "estimated_1rm_date": best_1rm_date.isoformat() if best_1rm_date else None,
        "total_sessions": len(session_dates),
        "total_sets": total_sets,
        "total_volume_kg": round(total_volume_kg, 1),
        "recent_sessions": recent_sessions,
        "weekly_history": weekly_history,
    }

    # UPSERT canonical projection
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
            (user_id, canonical, json.dumps(projection_data), str(last_event_id)),
        )

    # DELETE stale projections for non-canonical keys (alias consolidation)
    stale_keys = list(all_keys - {canonical})
    if stale_keys:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM projections
                WHERE user_id = %s
                  AND projection_type = 'exercise_progression'
                  AND key = ANY(%s)
                """,
                (user_id, stale_keys),
            )
        logger.info(
            "Deleted stale exercise_progression projections for user=%s keys=%s (consolidated into %s)",
            user_id, stale_keys, canonical,
        )

    logger.info(
        "Updated exercise_progression for user=%s exercise=%s (sets=%d, 1rm=%.1f)",
        user_id, canonical, total_sets, best_1rm,
    )
