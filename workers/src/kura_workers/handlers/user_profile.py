"""User Profile — Three-Layer Agent Entry Point (Decision 7).

Builds the agent's complete context in one projection:
- system: static capabilities from handler declarations
- user: dynamic per-user identity, dimension coverage, actionable data quality
- agenda: proactive items the agent should address

Reacts to all relevant event types. Full recompute on every event — idempotent.
"""

import json
import logging
from collections import defaultdict
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import get_dimension_metadata, projection_handler

logger = logging.getLogger(__name__)


# --- Pure functions (testable without DB) ---


def _build_system_layer(dimension_metadata: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build the system layer from registry declarations.

    Strips non-serializable fields (manifest_contribution callable).
    """
    dimensions = {}
    for name, meta in dimension_metadata.items():
        dimensions[name] = {
            "description": meta.get("description", ""),
            "key_structure": meta.get("key_structure", ""),
            "granularity": meta.get("granularity", []),
            "event_types": meta.get("event_types", []),
            "relates_to": meta.get("relates_to", {}),
        }
    return {
        "dimensions": dimensions,
        "time_conventions": {
            "week": "ISO 8601 (2026-W06)",
            "date": "ISO 8601 (2026-02-08)",
            "timestamp": "ISO 8601 with timezone",
        },
    }


def _resolve_exercises(
    exercises_logged: set[str],
    aliases: dict[str, dict[str, str]],
) -> set[str]:
    """Resolve exercises through alias map (new format with confidence)."""
    alias_lookup = {a.strip().lower(): info["target"] for a, info in aliases.items()}
    return {alias_lookup.get(ex, ex) for ex in exercises_logged}


def _build_user_dimensions(
    dimension_metadata: dict[str, dict[str, Any]],
    projection_rows: list[dict[str, Any]],
    set_logged_range: tuple[str, str] | None,
) -> dict[str, Any]:
    """Build per-user dimension status by merging declarations with observed projections."""
    projections_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in projection_rows:
        projections_by_type[row["projection_type"]].append(row)

    dimensions: dict[str, Any] = {}
    for name, meta in dimension_metadata.items():
        rows = projections_by_type.get(name, [])
        if not rows:
            dimensions[name] = {"status": "no_data"}
            continue

        entry: dict[str, Any] = {"status": "active"}

        # Freshness: max updated_at across all rows for this dimension
        freshness = max(r["updated_at"] for r in rows)
        entry["freshness"] = freshness.isoformat()

        # Coverage: use set.logged date range (all current dimensions react to set.logged)
        if set_logged_range:
            entry["coverage"] = {"from": set_logged_range[0], "to": set_logged_range[1]}

        # Call manifest_contribution if declared
        manifest_fn = meta.get("manifest_contribution")
        if manifest_fn and callable(manifest_fn):
            summary = manifest_fn(rows)
            entry.update(summary)

        dimensions[name] = entry

    return dimensions


def _find_unconfirmed_aliases(
    aliases: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    """Find aliases with non-confirmed confidence."""
    return [
        {"alias": alias, "target": info["target"], "confidence": info["confidence"]}
        for alias, info in aliases.items()
        if info.get("confidence") != "confirmed"
    ]


def _build_data_quality(
    total_set_logged: int,
    events_without_exercise_id: int,
    unresolved_exercises: list[str],
    exercise_occurrences: dict[str, int],
    unconfirmed_aliases: list[dict[str, str]],
) -> dict[str, Any]:
    """Build data_quality with actionable items."""
    actionable: list[dict[str, Any]] = []

    for ex in unresolved_exercises:
        actionable.append({
            "type": "unresolved_exercise",
            "exercise": ex,
            "occurrences": exercise_occurrences.get(ex, 0),
        })

    for alias_info in unconfirmed_aliases:
        actionable.append({
            "type": "unconfirmed_alias",
            "alias": alias_info["alias"],
            "target": alias_info["target"],
            "confidence": alias_info["confidence"],
        })

    return {
        "total_set_logged_events": total_set_logged,
        "events_without_exercise_id": events_without_exercise_id,
        "actionable": actionable,
    }


def _build_agenda(
    unresolved_exercises: list[dict[str, Any]],
    unconfirmed_aliases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build proactive agenda items for the agent.

    Phase 1: data-quality-based items.
    Future: goal_at_risk, plateau_detected (needs Bayesian engine).
    """
    agenda: list[dict[str, Any]] = []

    if unresolved_exercises:
        total = sum(item["occurrences"] for item in unresolved_exercises)
        exercises = [item["exercise"] for item in unresolved_exercises]
        if len(exercises) == 1:
            detail = f"{total} sets logged as '{exercises[0]}' — suggest canonical name"
        else:
            detail = f"{total} sets across {len(exercises)} unresolved exercises — suggest canonical names"
        agenda.append({
            "priority": "medium",
            "type": "resolve_exercises",
            "detail": detail,
            "dimensions": ["user_profile"],
        })

    for alias_info in unconfirmed_aliases:
        agenda.append({
            "priority": "low",
            "type": "confirm_alias",
            "detail": (
                f"Alias '{alias_info['alias']}' → {alias_info['target']} "
                f"is {alias_info['confidence']}, not confirmed"
            ),
            "dimensions": ["user_profile"],
        })

    return agenda


# --- Handler ---


@projection_handler(
    "set.logged",
    "exercise.alias_created",
    "preference.set",
    "goal.set",
)
async def update_user_profile(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of user_profile projection — three-layer structure."""
    user_id = payload["user_id"]

    # Fetch all relevant events for this user
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

    # --- Event loop: extract identity + data quality ---
    aliases: dict[str, dict[str, str]] = {}
    preferences: dict[str, Any] = {}
    goals: list[dict[str, Any]] = []
    exercises_logged: set[str] = set()
    total_events = 0
    total_set_logged = 0
    events_without_exercise_id = 0
    raw_exercises_without_id: set[str] = set()
    exercise_occurrences: dict[str, int] = defaultdict(int)
    first_set_logged_date: str | None = None
    last_set_logged_date: str | None = None

    first_event = rows[0]["timestamp"]
    last_event = rows[-1]["timestamp"]
    last_event_id_value = rows[-1]["id"]

    for row in rows:
        event_type = row["event_type"]
        data = row["data"]
        total_events += 1

        if event_type == "set.logged":
            total_set_logged += 1
            ts_date = row["timestamp"].date().isoformat()
            if first_set_logged_date is None:
                first_set_logged_date = ts_date
            last_set_logged_date = ts_date

            exercise_id = data.get("exercise_id", "")
            exercise = data.get("exercise", "")
            key = (exercise_id or exercise).strip().lower()
            if key:
                exercises_logged.add(key)
            if not exercise_id.strip():
                events_without_exercise_id += 1
                if key:
                    raw_exercises_without_id.add(key)
                    exercise_occurrences[key] += 1

        elif event_type == "exercise.alias_created":
            alias = data.get("alias", "").strip()
            target = data.get("exercise_id", "").strip().lower()
            confidence = data.get("confidence", "confirmed")
            if alias and target:
                aliases[alias] = {"target": target, "confidence": confidence}

        elif event_type == "preference.set":
            pref_key = data.get("key", "")
            pref_value = data.get("value")
            if pref_key:
                preferences[pref_key] = pref_value

        elif event_type == "goal.set":
            goals.append(data)

    # Resolve exercises through alias map
    resolved_exercises = _resolve_exercises(exercises_logged, aliases)

    # Compute data quality
    alias_lookup = {a.strip().lower(): info["target"] for a, info in aliases.items()}
    unresolved_exercises = sorted(
        ex for ex in raw_exercises_without_id if ex not in alias_lookup
    )
    unconfirmed_aliases = _find_unconfirmed_aliases(aliases)

    # --- Build three layers ---

    # Layer 1: system (from registry declarations)
    dimension_metadata = get_dimension_metadata()
    system_layer = _build_system_layer(dimension_metadata)

    # Layer 2: user (from events + projections)
    # Fetch all projections for this user (except user_profile) for dimension coverage
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT projection_type, key, data, updated_at
            FROM projections
            WHERE user_id = %s AND projection_type != 'user_profile'
            ORDER BY projection_type, key
            """,
            (user_id,),
        )
        projection_rows = await cur.fetchall()

    set_logged_range = None
    if first_set_logged_date and last_set_logged_date:
        set_logged_range = (first_set_logged_date, last_set_logged_date)

    user_dimensions = _build_user_dimensions(
        dimension_metadata, projection_rows, set_logged_range
    )

    data_quality = _build_data_quality(
        total_set_logged,
        events_without_exercise_id,
        unresolved_exercises,
        exercise_occurrences,
        unconfirmed_aliases,
    )

    # Layer 3: agenda (pattern matching over user data)
    unresolved_items = [
        {"exercise": ex, "occurrences": exercise_occurrences.get(ex, 0)}
        for ex in unresolved_exercises
    ]
    agenda = _build_agenda(unresolved_items, unconfirmed_aliases)

    projection_data = {
        "system": system_layer,
        "user": {
            "aliases": aliases,
            "preferences": preferences,
            "goals": goals,
            "exercises_logged": sorted(resolved_exercises),
            "total_events": total_events,
            "first_event": first_event.isoformat(),
            "last_event": last_event.isoformat(),
            "dimensions": user_dimensions,
            "data_quality": data_quality,
        },
        "agenda": agenda,
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
        "Updated user_profile for user=%s (exercises=%d, aliases=%d, agenda=%d)",
        user_id, len(resolved_exercises), len(aliases), len(agenda),
    )
