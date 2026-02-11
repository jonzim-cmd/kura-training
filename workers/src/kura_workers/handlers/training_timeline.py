"""Training Timeline dimension handler.

Reacts to set.logged events and computes temporal training patterns:
- Recent training days (last 30 with activity)
- Weekly summaries (last 26 weeks)
- Training frequency (rolling averages)
- Streak tracking (consecutive weeks with training)

Full recompute on every event — idempotent by design.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..utils import (
    epley_1rm,
    get_alias_map,
    get_retracted_event_ids,
    merge_observed_attributes,
    resolve_exercise_key,
    resolve_through_aliases,
    separate_known_unknown,
)

logger = logging.getLogger(__name__)

# Fields actively processed by this handler for set.logged events.
_KNOWN_FIELDS: set[str] = {
    "exercise", "exercise_id", "weight_kg", "weight", "reps",
    "rpe", "rir", "set_type", "set_number",
}


def _iso_week(d: date) -> str:
    """Return ISO week string like '2026-W06'."""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _compute_recent_days(
    day_data: dict[date, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute recent_days: last 30 training days, chronological."""
    sorted_dates = sorted(day_data.keys(), reverse=True)[:30]
    sorted_dates.reverse()

    result = []
    for d in sorted_dates:
        entry = day_data[d]
        day_entry: dict[str, Any] = {
            "date": d.isoformat(),
            "exercises": sorted(entry["exercises"]),
            "total_sets": entry["total_sets"],
            "total_volume_kg": round(entry["total_volume_kg"], 1),
            "total_reps": entry["total_reps"],
        }
        if entry.get("top_sets"):
            day_entry["top_sets"] = entry["top_sets"]
        result.append(day_entry)
    return result


def _compute_recent_sessions(
    session_data: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute recent_sessions: last 30 training sessions, chronological."""
    # Sort by date, then session_key for stable ordering
    sorted_keys = sorted(
        session_data.keys(),
        key=lambda k: (session_data[k]["date"], k),
        reverse=True,
    )[:30]
    sorted_keys.reverse()

    result = []
    for key in sorted_keys:
        entry = session_data[key]
        session_entry: dict[str, Any] = {
            "date": entry["date"],
            "exercises": sorted(entry["exercises"]),
            "total_sets": entry["total_sets"],
            "total_volume_kg": round(entry["total_volume_kg"], 1),
            "total_reps": entry["total_reps"],
        }
        if entry["session_id"] is not None:
            session_entry["session_id"] = entry["session_id"]
        if entry.get("top_sets"):
            session_entry["top_sets"] = entry["top_sets"]
        result.append(session_entry)
    return result


def _compute_weekly_summary(
    week_data: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute weekly_summary: last 26 weeks, chronological."""
    sorted_weeks = sorted(week_data.keys(), reverse=True)[:26]
    sorted_weeks.reverse()

    result = []
    for w in sorted_weeks:
        entry = week_data[w]
        result.append({
            "week": w,
            "training_days": entry["training_days"],
            "total_sets": entry["total_sets"],
            "total_volume_kg": round(entry["total_volume_kg"], 1),
            "exercises": sorted(entry["exercises"]),
        })
    return result


def _compute_frequency(
    training_dates: set[date],
    reference_date: date,
) -> dict[str, float]:
    """Compute rolling average training days per week."""
    def _avg_for_weeks(n_weeks: int) -> float:
        cutoff = reference_date - timedelta(weeks=n_weeks)
        days_in_range = sum(1 for d in training_dates if d >= cutoff)
        return round(days_in_range / n_weeks, 2)

    return {
        "last_4_weeks": _avg_for_weeks(4),
        "last_12_weeks": _avg_for_weeks(12),
    }


def _compute_streak(
    training_dates: set[date],
    reference_date: date,
) -> dict[str, int]:
    """Compute consecutive-week streaks.

    A week counts as active if it has at least one training day.
    """
    if not training_dates:
        return {"current_weeks": 0, "longest_weeks": 0}

    # Build set of all active weeks
    active_weeks: set[tuple[int, int]] = set()
    for d in training_dates:
        iso = d.isocalendar()
        active_weeks.add((iso.year, iso.week))

    # Walk backwards from reference_date's week to find current streak
    ref_iso = reference_date.isocalendar()
    current_streak = 0
    year, week = ref_iso.year, ref_iso.week

    while (year, week) in active_weeks:
        current_streak += 1
        # Move to previous week
        prev_day = date.fromisocalendar(year, week, 1) - timedelta(days=7)
        prev_iso = prev_day.isocalendar()
        year, week = prev_iso.year, prev_iso.week

    # Find longest streak by sorting all active weeks and scanning
    sorted_weeks = sorted(active_weeks)
    longest_streak = 0
    current_run = 0

    for i, (y, w) in enumerate(sorted_weeks):
        if i == 0:
            current_run = 1
        else:
            prev_y, prev_w = sorted_weeks[i - 1]
            # Check if this week is consecutive to the previous
            prev_monday = date.fromisocalendar(prev_y, prev_w, 1)
            this_monday = date.fromisocalendar(y, w, 1)
            if (this_monday - prev_monday).days == 7:
                current_run += 1
            else:
                current_run = 1
        longest_streak = max(longest_streak, current_run)

    return {
        "current_weeks": current_streak,
        "longest_weeks": longest_streak,
    }


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract summary for user_profile manifest (Decision 7)."""
    if not projection_rows:
        return {}
    data = projection_rows[0]["data"]
    return {
        "last_training": data.get("last_training"),
        "total_training_days": data.get("total_training_days"),
        "current_frequency": data.get("current_frequency"),
        "streak": data.get("streak"),
    }


@projection_handler("set.logged", "exercise.alias_created", dimension_meta={
    "name": "training_timeline",
    "description": "Training patterns: when, what, how much",
    "key_structure": "single overview per user",
    "projection_key": "overview",
    "granularity": ["day", "week"],
    "relates_to": {
        "exercise_progression": {"join": "week", "why": "volume breakdown per exercise"},
    },
    "context_seeds": [
        "training_frequency",
        "current_program",
        "training_schedule",
    ],
    "output_schema": {
        "recent_days": [{
            "date": "ISO 8601 date",
            "exercises": ["string — canonical exercise names"],
            "total_sets": "integer",
            "total_volume_kg": "number",
            "total_reps": "integer",
            "top_sets": {"<exercise_id>": {"weight_kg": "number", "reps": "integer", "estimated_1rm": "number"}},
        }],
        "recent_sessions": [{
            "date": "ISO 8601 date",
            "exercises": ["string"],
            "total_sets": "integer",
            "total_volume_kg": "number",
            "total_reps": "integer",
            "session_id": "string (optional)",
            "top_sets": {"<exercise_id>": {"weight_kg": "number", "reps": "integer", "estimated_1rm": "number"}},
        }],
        "weekly_summary": [{
            "week": "ISO 8601 week (e.g. 2026-W06)",
            "training_days": "integer",
            "total_sets": "integer",
            "total_volume_kg": "number",
            "exercises": ["string"],
        }],
        "current_frequency": {
            "last_4_weeks": "number — avg training days/week",
            "last_12_weeks": "number — avg training days/week",
        },
        "last_training": "ISO 8601 date",
        "total_training_days": "integer",
        "streak": {
            "current_weeks": "integer — consecutive weeks with training",
            "longest_weeks": "integer",
        },
        "data_quality": {
            "observed_attributes": {"<event_type>": {"<field>": "integer — count"}},
        },
    },
    "manifest_contribution": _manifest_contribution,
})
async def update_training_timeline(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of training_timeline projection."""
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)

    # Load alias map for resolving exercise names (retraction-aware)
    alias_map = await get_alias_map(conn, user_id, retracted_ids=retracted_ids)

    # Fetch ALL set.logged events for this user (including metadata for session_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = 'set.logged'
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    # Filter retracted events
    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    if not rows:
        # Clean up: delete any existing projection (all events retracted)
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM projections WHERE user_id = %s AND projection_type = 'training_timeline' AND key = 'overview'",
                (user_id,),
            )
        return

    last_event_id = rows[-1]["id"]

    # Aggregate by day, week, and session
    day_data: dict[date, dict[str, Any]] = defaultdict(
        lambda: {"exercises": set(), "total_sets": 0, "total_volume_kg": 0.0, "total_reps": 0, "top_sets": {}}
    )
    week_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"training_days": set(), "total_sets": 0, "total_volume_kg": 0.0, "exercises": set()}
    )
    # Session grouping: key = session_id or date string (fallback)
    session_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"date": None, "session_id": None, "exercises": set(), "total_sets": 0, "total_volume_kg": 0.0, "total_reps": 0, "top_sets": {}}
    )
    observed_attr_counts: dict[str, dict[str, int]] = {}

    for row in rows:
        data = row["data"]
        metadata = row.get("metadata") or {}
        ts: datetime = row["timestamp"]
        d = ts.date()
        w = _iso_week(d)

        # Session key: use metadata.session_id if present, fallback to date
        session_id = metadata.get("session_id")
        session_key = session_id or d.isoformat()

        # Decision 10: track unknown fields
        _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS)
        merge_observed_attributes(observed_attr_counts, "set.logged", unknown)

        raw_key = resolve_exercise_key(data) or "unknown"
        exercise_key = resolve_through_aliases(raw_key, alias_map)

        try:
            weight = float(data.get("weight_kg", data.get("weight", 0)))
            reps = int(data.get("reps", 0))
        except (ValueError, TypeError):
            weight = 0.0
            reps = 0

        volume = weight * reps
        e1rm = epley_1rm(weight, reps)

        # Day aggregation
        day_data[d]["exercises"].add(exercise_key)
        day_data[d]["total_sets"] += 1
        day_data[d]["total_volume_kg"] += volume
        day_data[d]["total_reps"] += reps

        # Top set per exercise per day (best estimated 1RM wins)
        current_top = day_data[d]["top_sets"].get(exercise_key)
        if current_top is None or e1rm > current_top["estimated_1rm"]:
            day_data[d]["top_sets"][exercise_key] = {
                "weight_kg": weight, "reps": reps, "estimated_1rm": round(e1rm, 1),
            }

        # Week aggregation
        week_data[w]["training_days"].add(d)
        week_data[w]["total_sets"] += 1
        week_data[w]["total_volume_kg"] += volume
        week_data[w]["exercises"].add(exercise_key)

        # Session aggregation
        session_data[session_key]["date"] = d.isoformat()
        session_data[session_key]["session_id"] = session_id
        session_data[session_key]["exercises"].add(exercise_key)
        session_data[session_key]["total_sets"] += 1
        session_data[session_key]["total_volume_kg"] += volume
        session_data[session_key]["total_reps"] += reps

        # Top set per exercise per session
        s_top = session_data[session_key]["top_sets"].get(exercise_key)
        if s_top is None or e1rm > s_top["estimated_1rm"]:
            session_data[session_key]["top_sets"][exercise_key] = {
                "weight_kg": weight, "reps": reps, "estimated_1rm": round(e1rm, 1),
            }

    # Finalize week_data: convert training_days sets to counts
    for w_entry in week_data.values():
        w_entry["training_days"] = len(w_entry["training_days"])

    training_dates = set(day_data.keys())
    reference_date = max(training_dates)

    projection_data = {
        "recent_days": _compute_recent_days(day_data),
        "recent_sessions": _compute_recent_sessions(session_data),
        "weekly_summary": _compute_weekly_summary(week_data),
        "current_frequency": _compute_frequency(training_dates, reference_date),
        "last_training": reference_date.isoformat(),
        "total_training_days": len(training_dates),
        "streak": _compute_streak(training_dates, reference_date),
        "data_quality": {
            "observed_attributes": observed_attr_counts,
        },
    }

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'training_timeline', 'overview', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), str(last_event_id)),
        )

    logger.info(
        "Updated training_timeline for user=%s (days=%d, weeks=%d)",
        user_id, len(training_dates), len(week_data),
    )
