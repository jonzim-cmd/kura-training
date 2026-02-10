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
    check_expected_fields,
    epley_1rm,
    find_all_keys_for_canonical,
    get_alias_map,
    get_retracted_event_ids,
    merge_observed_attributes,
    resolve_exercise_key,
    resolve_through_aliases,
    separate_known_unknown,
)

logger = logging.getLogger(__name__)

# Fields that this handler actively processes for set.logged events.
# Everything else is passed through as observed_attributes (Decision 10).
_KNOWN_FIELDS: set[str] = {
    "exercise", "exercise_id", "weight_kg", "weight", "reps",
    "rpe", "set_type", "set_number",
}

# Fields we *expect* for typical strength sets. Missing = data_quality hint.
_EXPECTED_FIELDS: dict[str, str] = {
    "weight_kg": "No weight — bodyweight or assisted exercise?",
    "reps": "No reps — time-based or isometric exercise?",
}


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
    "output_schema": {
        "exercise": "string — canonical exercise name (exercise_id)",
        "estimated_1rm": "number — current best Epley 1RM in kg",
        "estimated_1rm_date": "ISO 8601 datetime — when best 1RM was achieved",
        "total_sessions": "integer — distinct training sessions",
        "total_sets": "integer",
        "total_volume_kg": "number — sum(weight_kg * reps)",
        "recent_sessions": [{
            "timestamp": "ISO 8601 datetime",
            "weight_kg": "number",
            "reps": "integer",
            "estimated_1rm": "number — Epley formula",
            "rpe": "number (optional)",
            "set_type": "string (optional)",
            "session_id": "string (optional)",
            "extra": "object — unknown fields passed through (optional)",
        }],
        "weekly_history": [{
            "week": "ISO 8601 week (e.g. 2026-W06)",
            "estimated_1rm": "number — best of week",
            "total_sets": "integer",
            "total_volume_kg": "number",
            "max_weight_kg": "number",
        }],
        "data_quality": {
            "anomalies": [{"event_id": "string", "field": "string", "value": "any", "expected_range": "[min, max]", "message": "string"}],
            "field_hints": [{"field": "string", "hint": "string"}],
            "observed_attributes": {"<event_type>": {"<field>": "integer — count"}},
        },
    },
    "manifest_contribution": _manifest_contribution,
})
async def update_exercise_progression(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of exercise_progression projection for the affected exercise."""
    user_id = payload["user_id"]
    event_id = payload["event_id"]
    event_type = payload.get("event_type", "")

    # Load retracted event IDs and alias map (retraction-aware)
    retracted_ids = await get_retracted_event_ids(conn, user_id)
    alias_map = await get_alias_map(conn, user_id, retracted_ids=retracted_ids)

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
            SELECT id, event_type, timestamp, data, metadata
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

    # Filter retracted events
    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    if not rows:
        # All events for this exercise were retracted — clean up projection
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM projections
                WHERE user_id = %s
                  AND projection_type = 'exercise_progression'
                  AND key = %s
                """,
                (user_id, canonical),
            )
        logger.info(
            "Deleted exercise_progression for user=%s exercise=%s (all events retracted)",
            user_id, canonical,
        )
        return

    # Compute statistics
    total_sets = len(rows)
    total_volume_kg = 0.0
    best_1rm = 0.0
    best_1rm_date: datetime | None = None
    session_keys: set[str] = set()  # session_id or date string (fallback)
    recent_sets: list[dict[str, Any]] = []
    last_event_id = rows[-1]["id"]

    # Weekly aggregation for weekly_history
    week_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"estimated_1rm": 0.0, "total_sets": 0, "total_volume_kg": 0.0, "max_weight_kg": 0.0}
    )

    anomalies: list[dict[str, Any]] = []
    field_hints: list[dict[str, Any]] = []
    observed_attr_counts: dict[str, dict[str, int]] = {}

    for row in rows:
        data = row["data"]
        metadata = row.get("metadata") or {}
        ts: datetime = row["timestamp"]

        # Session key: use metadata.session_id if present, fallback to date
        session_id = metadata.get("session_id")
        session_key = session_id or ts.date().isoformat()

        # Decision 10: separate known from unknown fields
        _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS)
        merge_observed_attributes(observed_attr_counts, row["event_type"], unknown)

        # Support both weight_kg (convention) and weight (legacy)
        try:
            weight = float(data.get("weight_kg", data.get("weight", 0)))
            reps = int(data.get("reps", 0))
        except (ValueError, TypeError):
            logger.warning("Skipping event %s: invalid weight/reps data", row["id"])
            continue

        # Anomaly detection: absolute bounds
        if weight < 0 or weight > 500:
            anomalies.append({
                "event_id": str(row["id"]),
                "field": "weight_kg",
                "value": weight,
                "expected_range": [0, 500],
                "message": f"Weight {weight}kg outside plausible range on {ts.date().isoformat()}",
            })
        if reps < 0 or reps > 100:
            anomalies.append({
                "event_id": str(row["id"]),
                "field": "reps",
                "value": reps,
                "expected_range": [0, 100],
                "message": f"{reps} reps in a single set on {ts.date().isoformat()}",
            })

        volume = weight * reps
        total_volume_kg += volume

        session_keys.add(session_key)

        e1rm = epley_1rm(weight, reps)

        # Anomaly detection: 1RM jump > 100% over previous best
        if best_1rm > 0 and e1rm > best_1rm * 2:
            anomalies.append({
                "event_id": str(row["id"]),
                "field": "estimated_1rm",
                "value": round(e1rm, 1),
                "expected_range": [0, round(best_1rm * 2, 1)],
                "message": (
                    f"1RM jumped from {best_1rm:.1f}kg to {e1rm:.1f}kg "
                    f"({(e1rm / best_1rm - 1) * 100:.0f}% increase) on {ts.date().isoformat()}"
                ),
            })

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
            "_session_key": session_key,  # internal, stripped before output
        }
        # Include optional known fields if present
        if "rpe" in data:
            try:
                set_entry["rpe"] = float(data["rpe"])
            except (ValueError, TypeError):
                pass
        if "set_type" in data:
            set_entry["set_type"] = data["set_type"]
        if session_id is not None:
            set_entry["session_id"] = session_id

        # Decision 10: pass through unknown fields per set
        if unknown:
            set_entry["extra"] = unknown

        recent_sets.append(set_entry)

    # Last 5 sessions worth of sets (by session_key: session_id or date)
    # Determine last 5 session keys by their latest timestamp
    session_last_ts: dict[str, str] = {}
    for s in recent_sets:
        sk = s["_session_key"]
        if sk not in session_last_ts or s["timestamp"] > session_last_ts[sk]:
            session_last_ts[sk] = s["timestamp"]
    sorted_session_keys = sorted(session_last_ts, key=lambda k: session_last_ts[k], reverse=True)[:5]
    recent_session_set = set(sorted_session_keys)
    recent_sessions = [
        {k: v for k, v in s.items() if k != "_session_key"}
        for s in recent_sets
        if s["_session_key"] in recent_session_set
    ]
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

    # Decision 10: check for missing expected fields across all events
    # We only flag once per projection rebuild, using the last event as sample
    if rows:
        field_hints = check_expected_fields(rows[-1]["data"], _EXPECTED_FIELDS)

    projection_data: dict[str, Any] = {
        "exercise": canonical,
        "estimated_1rm": round(best_1rm, 1),
        "estimated_1rm_date": best_1rm_date.isoformat() if best_1rm_date else None,
        "total_sessions": len(session_keys),
        "total_sets": total_sets,
        "total_volume_kg": round(total_volume_kg, 1),
        "recent_sessions": recent_sessions,
        "weekly_history": weekly_history,
        "data_quality": {
            "anomalies": anomalies,
            "field_hints": field_hints,
            "observed_attributes": observed_attr_counts,
        },
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
