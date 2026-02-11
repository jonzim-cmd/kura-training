"""User Profile — Dynamic Agent Context (Decision 7).

Builds the per-user context in one projection:
- user: dynamic identity, dimension coverage, actionable data quality
- agenda: proactive items the agent should address

The system layer (dimensions, event conventions, interview guide) lives in
system_config — a separate, deployment-static table written at worker startup.

Reacts to all relevant event types. Full recompute on every event — idempotent.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import get_dimension_metadata, projection_handler, registered_event_types
from ..utils import get_retracted_event_ids

logger = logging.getLogger(__name__)

_INTERNAL_NON_ORPHAN_EVENT_TYPES = {
    "learning.signal.logged",
}


# --- Pure functions (testable without DB) ---


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


def _find_orphaned_event_types(
    all_event_types: dict[str, int],
) -> list[dict[str, Any]]:
    """Find event types that no handler processes (Decision 9).

    Compares all distinct event_types a user has sent with
    the set of event_types that have registered handlers.
    """
    handled = set(registered_event_types())
    orphaned = []
    for event_type, count in sorted(all_event_types.items()):
        if event_type in _INTERNAL_NON_ORPHAN_EVENT_TYPES:
            continue
        if event_type not in handled:
            orphaned.append({"event_type": event_type, "count": count})
    return orphaned


def _escalate_priority(count: int, first_seen: str | None) -> str:
    """Compute escalated priority based on event count and age.

    Thresholds (OR-based — either criterion triggers):
    - info: default
    - low: >20 events OR >7 days old
    - medium: >50 events OR >14 days old
    - high: >100 events OR >28 days old
    """
    age_days = 0
    if first_seen:
        try:
            fs = datetime.fromisoformat(first_seen)
            if fs.tzinfo is None:
                fs = fs.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - fs).days
        except (ValueError, TypeError):
            pass

    if count > 100 or age_days > 28:
        return "high"
    if count > 50 or age_days > 14:
        return "medium"
    if count > 20 or age_days > 7:
        return "low"
    return "info"


def _build_observed_patterns(
    projection_rows: list[dict[str, Any]],
    orphaned_event_types: list[dict[str, Any]],
    orphaned_field_samples: dict[str, list[str]],
    orphaned_first_seen: dict[str, str] | None = None,
    observed_field_first_seen: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build observation landscape from projection data (Phase 2, Decision 10).

    Extracts observed_attributes from all projections and merges them into
    a unified view. No thresholds — everything is surfaced immediately.
    Frequency is metadata, not a gate. The agent decides what's relevant.
    """
    # Per dimension: aggregate observed_attributes across all rows
    # (handles multi-key projections like exercise_progression)
    per_dimension: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )

    for row in projection_rows:
        dimension = row["projection_type"]
        data = row.get("data") or {}
        dq = data.get("data_quality", {})
        observed = dq.get("observed_attributes", {})

        for event_type, fields in observed.items():
            if not isinstance(fields, dict):
                continue  # Guard against old flat format
            for field, count in fields.items():
                per_dimension[dimension][event_type][field] += count

    # Merge across dimensions: max count (avoids double-counting same events
    # processed by multiple handlers), collect all observing dimension names
    merged: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for dimension, et_fields in per_dimension.items():
        for event_type, fields in et_fields.items():
            for field, count in fields.items():
                if field not in merged[event_type]:
                    merged[event_type][field] = {"count": count, "dimensions": [dimension]}
                else:
                    existing = merged[event_type][field]
                    if count > existing["count"]:
                        existing["count"] = count
                    if dimension not in existing["dimensions"]:
                        existing["dimensions"].append(dimension)

    # Deterministic output
    observed_fields: dict[str, Any] = {}
    for event_type in sorted(merged):
        observed_fields[event_type] = {}
        for field, info in sorted(merged[event_type].items()):
            entry = {
                "count": info["count"],
                "dimensions": sorted(info["dimensions"]),
            }
            if observed_field_first_seen and event_type in observed_field_first_seen:
                fs = observed_field_first_seen[event_type].get(field)
                if fs:
                    entry["first_seen"] = fs
            observed_fields[event_type][field] = entry

    # Orphaned event types with field analysis
    orphaned_types: dict[str, Any] = {}
    for orphaned in orphaned_event_types:
        et = orphaned["event_type"]
        entry = {
            "count": orphaned["count"],
            "common_fields": orphaned_field_samples.get(et, []),
        }
        if orphaned_first_seen and et in orphaned_first_seen:
            entry["first_seen"] = orphaned_first_seen[et]
        orphaned_types[et] = entry

    result: dict[str, Any] = {}
    if observed_fields:
        result["observed_fields"] = observed_fields
    if orphaned_types:
        result["orphaned_event_types"] = orphaned_types

    return result


def _build_data_quality(
    total_set_logged: int,
    events_without_exercise_id: int,
    unresolved_exercises: list[str],
    exercise_occurrences: dict[str, int],
    unconfirmed_aliases: list[dict[str, str]],
    orphaned_event_types: list[dict[str, Any]] | None = None,
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

    result: dict[str, Any] = {
        "total_set_logged_events": total_set_logged,
        "events_without_exercise_id": events_without_exercise_id,
        "actionable": actionable,
    }

    if orphaned_event_types:
        result["orphaned_event_types"] = orphaned_event_types

    return result


def _compute_interview_coverage(
    aliases: dict[str, dict[str, str]],
    preferences: dict[str, Any],
    goals: list[dict[str, Any]],
    profile_data: dict[str, Any],
    injuries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute interview coverage status per area (Decision 8).

    Returns a list of {area, status, note?} dicts.
    Status: covered, uncovered, needs_depth.
    """
    from ..interview_guide import COVERAGE_AREAS

    coverage: list[dict[str, Any]] = []

    for area in COVERAGE_AREAS:
        if area == "training_background":
            has_modality = bool(profile_data.get("training_modality"))
            has_experience = bool(profile_data.get("experience_level"))
            if has_modality or has_experience:
                coverage.append({"area": area, "status": "covered"})
            else:
                coverage.append({"area": area, "status": "uncovered"})

        elif area == "goals":
            if goals:
                coverage.append({"area": area, "status": "covered"})
            else:
                coverage.append({"area": area, "status": "uncovered"})

        elif area == "exercise_vocabulary":
            alias_count = len(aliases)
            if alias_count >= 3:
                coverage.append({"area": area, "status": "covered"})
            elif alias_count > 0:
                coverage.append({
                    "area": area,
                    "status": "needs_depth",
                    "note": f"{alias_count} aliases, suggest more",
                })
            else:
                coverage.append({"area": area, "status": "uncovered"})

        elif area == "unit_preferences":
            if "unit_system" in preferences:
                coverage.append({"area": area, "status": "covered"})
            else:
                coverage.append({"area": area, "status": "uncovered"})

        elif area == "injuries":
            if injuries or profile_data.get("injuries_none"):
                coverage.append({"area": area, "status": "covered"})
            else:
                coverage.append({"area": area, "status": "uncovered"})

        elif area == "equipment":
            if profile_data.get("available_equipment"):
                coverage.append({"area": area, "status": "covered"})
            else:
                coverage.append({"area": area, "status": "uncovered"})

        elif area == "schedule":
            if profile_data.get("training_frequency_per_week") is not None:
                coverage.append({"area": area, "status": "covered"})
            else:
                coverage.append({"area": area, "status": "uncovered"})

        elif area == "nutrition_interest":
            if "nutrition_tracking" in preferences:
                coverage.append({"area": area, "status": "covered"})
            else:
                coverage.append({"area": area, "status": "uncovered"})

        elif area == "current_program":
            if profile_data.get("current_program"):
                coverage.append({"area": area, "status": "covered"})
            else:
                coverage.append({"area": area, "status": "uncovered"})

        elif area == "communication_preferences":
            if profile_data.get("communication_style"):
                coverage.append({"area": area, "status": "covered"})
            else:
                coverage.append({"area": area, "status": "uncovered"})

        else:
            coverage.append({"area": area, "status": "uncovered"})

    return coverage


def _should_suggest_onboarding(
    total_events: int,
    coverage: list[dict[str, Any]],
) -> bool:
    """Check if onboarding interview should be suggested.

    True when most coverage areas are uncovered and data is sparse.
    """
    if total_events >= 5:
        return False
    uncovered = sum(1 for c in coverage if c["status"] == "uncovered")
    return uncovered >= 5


def _should_suggest_refresh(
    total_events: int,
    coverage: list[dict[str, Any]],
    has_goals: bool,
    has_preferences: bool,
) -> bool:
    """Check if profile refresh should be suggested.

    True when user has training data but missing context.
    """
    if total_events <= 20:
        return False
    uncovered = sum(1 for c in coverage if c["status"] == "uncovered")
    if uncovered >= 3 and (not has_goals or not has_preferences):
        return True
    return False


def _build_agenda(
    unresolved_exercises: list[dict[str, Any]],
    unconfirmed_aliases: list[dict[str, Any]],
    interview_coverage: list[dict[str, Any]] | None = None,
    total_events: int = 0,
    has_goals: bool = False,
    has_preferences: bool = False,
    observed_patterns: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build proactive agenda items for the agent.

    Includes data-quality items and interview triggers (Decision 8).
    Future: goal_at_risk, plateau_detected (needs Bayesian engine).
    """
    agenda: list[dict[str, Any]] = []

    # Interview triggers (Decision 8)
    if interview_coverage is not None:
        if _should_suggest_onboarding(total_events, interview_coverage):
            agenda.append({
                "priority": "high",
                "type": "onboarding_needed",
                "detail": "New user with minimal data. Interview recommended to bootstrap profile.",
                "dimensions": ["user_profile"],
            })
        elif _should_suggest_refresh(total_events, interview_coverage, has_goals, has_preferences):
            uncovered = [c["area"] for c in interview_coverage if c["status"] == "uncovered"]
            agenda.append({
                "priority": "medium",
                "type": "profile_refresh_suggested",
                "detail": f"Missing context in {len(uncovered)} areas: {', '.join(uncovered[:3])}. Brief interview would improve analysis.",
                "dimensions": ["user_profile"],
            })

    # Data quality items
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

    # Observation landscape (Phase 2, Decision 10)
    # Priority escalates based on event count + age (aok).
    if observed_patterns:
        for event_type, fields in sorted(observed_patterns.get("observed_fields", {}).items()):
            for field, info in sorted(fields.items()):
                first_seen = info.get("first_seen")
                priority = _escalate_priority(info["count"], first_seen)
                item: dict[str, Any] = {
                    "priority": priority,
                    "type": "field_observed",
                    "event_type": event_type,
                    "field": field,
                    "count": info["count"],
                    "dimensions": info["dimensions"],
                    "detail": f"Field '{field}' observed {info['count']} times in {event_type}",
                }
                if first_seen:
                    item["first_seen"] = first_seen
                agenda.append(item)

        for event_type, info in sorted(observed_patterns.get("orphaned_event_types", {}).items()):
            fields_str = ", ".join(info["common_fields"]) if info["common_fields"] else "unknown"
            first_seen = info.get("first_seen")
            priority = _escalate_priority(info["count"], first_seen)
            item = {
                "priority": priority,
                "type": "orphaned_event_type",
                "event_type": event_type,
                "count": info["count"],
                "common_fields": info["common_fields"],
                "detail": (
                    f"{info['count']} events of type '{event_type}' (no handler). "
                    f"Common fields: {fields_str}"
                ),
            }
            if first_seen:
                item["first_seen"] = first_seen
            agenda.append(item)

    # Sort by priority (most urgent first)
    priority_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    agenda.sort(key=lambda a: priority_order.get(a["priority"], 99))

    return agenda


# --- Handler ---


@projection_handler(
    "set.logged",
    "exercise.alias_created",
    "preference.set",
    "goal.set",
    "profile.updated",
    "program.started",
    "injury.reported",
    "bodyweight.logged",
    "measurement.logged",
    "sleep.logged",
    "soreness.logged",
    "energy.logged",
    "meal.logged",
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    "nutrition_target.set",
    "sleep_target.set",
    "weight_target.set",
)
async def update_user_profile(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of user_profile projection — user + agenda."""
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)

    # Fetch all relevant events for this user
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type IN (
                  'set.logged', 'exercise.alias_created', 'preference.set',
                  'goal.set', 'profile.updated', 'program.started', 'injury.reported'
              )
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    # Filter retracted events
    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    if not rows:
        # Clean up: delete any existing projection
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM projections WHERE user_id = %s AND projection_type = 'user_profile' AND key = 'me'",
                (user_id,),
            )
        return

    # --- Event loop: extract identity + data quality ---
    aliases: dict[str, dict[str, str]] = {}
    preferences: dict[str, Any] = {}
    goals: list[dict[str, Any]] = []
    profile_data: dict[str, Any] = {}
    injuries: list[dict[str, Any]] = []
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

        elif event_type == "profile.updated":
            # Delta merge: later events overwrite earlier per field
            for field_key, field_value in data.items():
                profile_data[field_key] = field_value

        elif event_type == "program.started":
            # Keep current program in profile for interview coverage and context.
            program_name = (
                data.get("name")
                or data.get("program_name")
                or data.get("program")
                or data.get("template")
            )
            if isinstance(program_name, str) and program_name.strip():
                profile_data["current_program"] = program_name.strip()

        elif event_type == "injury.reported":
            injuries.append(data)

    # Resolve exercises through alias map
    resolved_exercises = _resolve_exercises(exercises_logged, aliases)

    # Compute data quality
    alias_lookup = {a.strip().lower(): info["target"] for a, info in aliases.items()}
    unresolved_exercises = sorted(
        ex for ex in raw_exercises_without_id if ex not in alias_lookup
    )
    unconfirmed_aliases = _find_unconfirmed_aliases(aliases)

    # Detect orphaned event types (Decision 9)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT event_type, COUNT(*) as count
            FROM events
            WHERE user_id = %s
            GROUP BY event_type
            """,
            (user_id,),
        )
        event_type_counts = {r["event_type"]: r["count"] for r in await cur.fetchall()}
    orphaned_event_types = _find_orphaned_event_types(event_type_counts)

    # Field sampling for orphaned event types (Phase 2, Decision 10)
    orphaned_field_samples: dict[str, list[str]] = {}
    for orphaned in orphaned_event_types[:10]:
        et = orphaned["event_type"]
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT DISTINCT k AS field
                FROM (SELECT data FROM events WHERE user_id = %s AND event_type = %s LIMIT 50) sub,
                jsonb_object_keys(sub.data) AS k
                """,
                (user_id, et),
            )
            orphaned_field_samples[et] = sorted(r["field"] for r in await cur.fetchall())

    # Compute interview coverage (Decision 8)
    interview_coverage = _compute_interview_coverage(
        aliases, preferences, goals, profile_data, injuries,
    )

    # --- Build user + agenda layers ---
    # System layer lives in system_config table (deployment-static, written at worker startup)

    dimension_metadata = get_dimension_metadata()

    # User layer (from events + projections)
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

    # First-seen timestamps for escalation (aok)
    orphaned_first_seen: dict[str, str] = {}
    if orphaned_event_types:
        orphaned_type_names = [o["event_type"] for o in orphaned_event_types]
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT event_type, MIN(timestamp) as first_seen
                FROM events
                WHERE user_id = %s AND event_type = ANY(%s)
                GROUP BY event_type
                """,
                (user_id, orphaned_type_names),
            )
            for r in await cur.fetchall():
                orphaned_first_seen[r["event_type"]] = r["first_seen"].isoformat()

    # Observation landscape (Phase 2, Decision 10)
    observed_patterns = _build_observed_patterns(
        projection_rows, orphaned_event_types, orphaned_field_samples,
        orphaned_first_seen=orphaned_first_seen,
    )

    # First-seen for observed unknown fields (query after patterns are built)
    observed_field_first_seen: dict[str, dict[str, str]] = {}
    if observed_patterns and "observed_fields" in observed_patterns:
        for et, fields in observed_patterns["observed_fields"].items():
            field_names = list(fields.keys())
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT k AS field, MIN(e.timestamp) AS first_seen
                    FROM events e,
                         jsonb_object_keys(e.data) AS k
                    WHERE e.user_id = %s
                      AND e.event_type = %s
                      AND k = ANY(%s)
                    GROUP BY k
                    """,
                    (user_id, et, field_names),
                )
                for r in await cur.fetchall():
                    observed_field_first_seen.setdefault(et, {})[r["field"]] = r["first_seen"].isoformat()

    # Rebuild with first_seen enrichment if we have any
    if observed_field_first_seen:
        observed_patterns = _build_observed_patterns(
            projection_rows, orphaned_event_types, orphaned_field_samples,
            orphaned_first_seen=orphaned_first_seen,
            observed_field_first_seen=observed_field_first_seen,
        )

    data_quality = _build_data_quality(
        total_set_logged,
        events_without_exercise_id,
        unresolved_exercises,
        exercise_occurrences,
        unconfirmed_aliases,
        orphaned_event_types,
    )

    # Agenda layer (pattern matching over user data)
    unresolved_items = [
        {"exercise": ex, "occurrences": exercise_occurrences.get(ex, 0)}
        for ex in unresolved_exercises
    ]
    agenda = _build_agenda(
        unresolved_items,
        unconfirmed_aliases,
        interview_coverage=interview_coverage,
        total_events=total_events,
        has_goals=bool(goals),
        has_preferences=bool(preferences),
        observed_patterns=observed_patterns,
    )

    projection_data = {
        "user": {
            "aliases": aliases,
            "preferences": preferences,
            "goals": goals,
            "profile": profile_data if profile_data else None,
            "injuries": injuries if injuries else None,
            "exercises_logged": sorted(resolved_exercises),
            "total_events": total_events,
            "first_event": first_event.isoformat(),
            "last_event": last_event.isoformat(),
            "dimensions": user_dimensions,
            "observed_patterns": observed_patterns or None,
            "data_quality": data_quality,
            "interview_coverage": interview_coverage,
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
        "Updated user_profile for user=%s (exercises=%d, aliases=%d, agenda=%d, coverage=%d/%d)",
        user_id, len(resolved_exercises), len(aliases), len(agenda),
        sum(1 for c in interview_coverage if c["status"] == "covered"),
        len(interview_coverage),
    )
