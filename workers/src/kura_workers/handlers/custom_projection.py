"""Custom projection handler — Agent-Mediated Evolution (Phase 3, Decision 10).

Processes projection_rule.created and projection_rule.archived events.
When a rule is created, builds a custom projection from matching events.
When archived, deletes the projection.

Also provides recompute_matching_rules() for the router to call when
regular events arrive that match active custom rules.
"""

import json
import logging
from collections import defaultdict
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..rule_models import (
    CategorizedTrackingRule,
    FieldTrackingRule,
    validate_rule,
)
from ..utils import (
    get_retracted_event_ids,
    load_timezone_preference,
    normalize_temporal_point,
    resolve_timezone_context,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule lifecycle: loading active rules from events
# ---------------------------------------------------------------------------


async def _load_active_rules(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    retracted_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Load all active projection rules for a user from events.

    Replays projection_rule.created and projection_rule.archived events
    chronologically. Last event per rule name wins:
    - created → rule is active
    - archived → rule is inactive (removed from result)

    Returns {rule_name: rule_data} for all active rules.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type IN ('projection_rule.created', 'projection_rule.archived')
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    active: dict[str, dict[str, Any]] = {}
    for row in rows:
        if retracted_ids is not None and str(row["id"]) in retracted_ids:
            continue

        data = row["data"]
        name = data.get("name")
        if not name:
            continue
        if row["event_type"] == "projection_rule.created":
            active[name] = data
        elif row["event_type"] == "projection_rule.archived":
            active.pop(name, None)

    return active


# ---------------------------------------------------------------------------
# Projection computation: field_tracking
# ---------------------------------------------------------------------------


def _iso_week(d: date) -> str:
    """Return ISO week string like '2026-W06'."""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


async def _compute_field_tracking(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    rule: FieldTrackingRule,
    retracted_ids: set[str],
) -> dict[str, Any]:
    """Build projection data for a field_tracking rule.

    Queries all matching events and computes:
    - recent_entries: last 30 per-day values
    - weekly_summary: weekly averages
    - all_time: overall stats per field
    """
    timezone_pref = await load_timezone_preference(conn, user_id, retracted_ids)
    timezone_context = resolve_timezone_context(timezone_pref)
    timezone_name = timezone_context["timezone"]

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
            ORDER BY timestamp ASC
            """,
            (user_id, rule.source_events),
        )
        rows = await cur.fetchall()

    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    if not rows:
        return {
            "rule": rule.model_dump(),
            "timezone_context": timezone_context,
            "recent_entries": [],
            "weekly_summary": [],
            "all_time": {},
            "data_quality": {
                "total_events_processed": 0,
                "fields_present": {},
                "temporal_conflicts": {},
            },
        }

    # Per-day aggregation
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    field_counts: dict[str, int] = {f: 0 for f in rule.fields}
    temporal_conflicts: dict[str, int] = {}

    for row in rows:
        data = row["data"]
        temporal = normalize_temporal_point(
            row["timestamp"],
            timezone_name=timezone_name,
            data=data,
            metadata=row.get("metadata") or {},
        )
        day_key = temporal.local_date.isoformat()
        for conflict in temporal.conflicts:
            temporal_conflicts[conflict] = temporal_conflicts.get(conflict, 0) + 1
        entry: dict[str, Any] = {}
        has_any = False
        for field in rule.fields:
            val = data.get(field)
            if val is not None:
                try:
                    entry[field] = float(val)
                    field_counts[field] += 1
                    has_any = True
                except (ValueError, TypeError):
                    entry[field] = val
                    field_counts[field] += 1
                    has_any = True
        if has_any:
            by_day[day_key].append(entry)

    # Recent entries: average per day, last 30 days
    recent_entries: list[dict[str, Any]] = []
    for day_key in sorted(by_day.keys()):
        day_entries = by_day[day_key]
        day_avg: dict[str, Any] = {"date": day_key}
        for field in rule.fields:
            values = [e[field] for e in day_entries if isinstance(e.get(field), float)]
            if values:
                day_avg[field] = round(sum(values) / len(values), 2)
        recent_entries.append(day_avg)
    recent_entries = recent_entries[-30:]

    # Weekly summary
    by_week: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for day_key, entries in by_day.items():
        d = date.fromisoformat(day_key)
        week_key = _iso_week(d)
        by_week[week_key].extend(entries)

    weekly_summary: list[dict[str, Any]] = []
    for week_key in sorted(by_week.keys()):
        week_entries = by_week[week_key]
        week_data: dict[str, Any] = {"week": week_key, "entries": len(week_entries)}
        for field in rule.fields:
            values = [e[field] for e in week_entries if isinstance(e.get(field), float)]
            if values:
                week_data[f"{field}_avg"] = round(sum(values) / len(values), 2)
        weekly_summary.append(week_data)

    # All-time stats
    all_time: dict[str, Any] = {}
    for field in rule.fields:
        all_values = []
        for entries in by_day.values():
            for e in entries:
                if isinstance(e.get(field), float):
                    all_values.append(e[field])
        if all_values:
            all_time[field] = {
                "avg": round(sum(all_values) / len(all_values), 2),
                "min": round(min(all_values), 2),
                "max": round(max(all_values), 2),
                "count": len(all_values),
            }

    return {
        "rule": rule.model_dump(),
        "timezone_context": timezone_context,
        "recent_entries": recent_entries,
        "weekly_summary": weekly_summary,
        "all_time": all_time,
        "data_quality": {
            "total_events_processed": len(rows),
            "fields_present": field_counts,
            "temporal_conflicts": temporal_conflicts,
        },
    }


# ---------------------------------------------------------------------------
# Projection computation: categorized_tracking
# ---------------------------------------------------------------------------


async def _compute_categorized_tracking(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    rule: CategorizedTrackingRule,
    retracted_ids: set[str],
) -> dict[str, Any]:
    """Build projection data for a categorized_tracking rule.

    Queries all matching events, groups by the group_by field, and computes
    per-category statistics.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
            ORDER BY timestamp ASC
            """,
            (user_id, rule.source_events),
        )
        rows = await cur.fetchall()

    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    if not rows:
        return {
            "rule": rule.model_dump(),
            "categories": {},
            "data_quality": {"total_events_processed": 0, "categories_found": 0},
        }

    # Group by category
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        data = row["data"]
        cat_value = data.get(rule.group_by)
        if cat_value is None:
            cat_value = "_unknown"
        cat_key = str(cat_value).strip().lower()
        entry = {"timestamp": row["timestamp"].isoformat()}
        for field in rule.fields:
            if field != rule.group_by:
                val = data.get(field)
                if val is not None:
                    entry[field] = val
        by_category[cat_key].append(entry)

    # Build per-category projection
    categories: dict[str, Any] = {}
    for cat_key, entries in sorted(by_category.items()):
        cat_data: dict[str, Any] = {
            "count": len(entries),
            "recent_entries": entries[-10:],  # last 10 per category
        }
        # Aggregate numeric fields
        field_stats: dict[str, Any] = {}
        for field in rule.fields:
            if field == rule.group_by:
                continue
            values = []
            for e in entries:
                val = e.get(field)
                if val is not None:
                    try:
                        values.append(float(val))
                    except (ValueError, TypeError):
                        pass
            if values:
                field_stats[field] = {
                    "avg": round(sum(values) / len(values), 2),
                    "min": round(min(values), 2),
                    "max": round(max(values), 2),
                }
        if field_stats:
            cat_data["fields"] = field_stats
        categories[cat_key] = cat_data

    return {
        "rule": rule.model_dump(),
        "categories": categories,
        "data_quality": {
            "total_events_processed": len(rows),
            "categories_found": len(categories),
        },
    }


# ---------------------------------------------------------------------------
# UPSERT + DELETE helpers
# ---------------------------------------------------------------------------


async def _upsert_custom_projection(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    rule_name: str,
    projection_data: dict[str, Any],
    last_event_id: str,
) -> None:
    """UPSERT a custom projection."""
    # last_event_id is a UUID column — use None if empty/missing
    event_id_param = last_event_id if last_event_id else None
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'custom', %s, %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = COALESCE(EXCLUDED.last_event_id, projections.last_event_id),
                updated_at = NOW()
            """,
            (user_id, rule_name, json.dumps(projection_data), event_id_param),
        )


async def _delete_custom_projection(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    rule_name: str,
) -> None:
    """Delete a custom projection."""
    async with conn.cursor() as cur:
        await cur.execute(
            "DELETE FROM projections WHERE user_id = %s AND projection_type = 'custom' AND key = %s",
            (user_id, rule_name),
        )


# ---------------------------------------------------------------------------
# Compute a single rule
# ---------------------------------------------------------------------------


async def _compute_rule(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    rule_data: dict[str, Any],
    retracted_ids: set[str],
) -> dict[str, Any] | None:
    """Validate and compute a single rule. Returns projection data or None on error."""
    try:
        rule = validate_rule(rule_data)
    except (ValueError, Exception) as e:
        logger.warning("Invalid projection rule '%s': %s", rule_data.get("name", "?"), e)
        return None

    if isinstance(rule, FieldTrackingRule):
        return await _compute_field_tracking(conn, user_id, rule, retracted_ids)
    elif isinstance(rule, CategorizedTrackingRule):
        return await _compute_categorized_tracking(conn, user_id, rule, retracted_ids)
    else:
        logger.warning("Unknown rule type: %s", type(rule).__name__)
        return None


# ---------------------------------------------------------------------------
# Handler entry point
# ---------------------------------------------------------------------------


@projection_handler("projection_rule.created", "projection_rule.archived")
async def update_custom_projections(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Process projection rule lifecycle events.

    On projection_rule.created: validate rule, compute projection, UPSERT.
    On projection_rule.archived: delete the custom projection.
    """
    user_id = payload["user_id"]
    event_type = payload["event_type"]
    event_id = payload.get("event_id", "")

    if event_type == "projection_rule.archived":
        # Get the rule name from the event data
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT data FROM events WHERE id = %s AND user_id = %s",
                (event_id, user_id),
            )
            row = await cur.fetchone()
        if not row:
            logger.warning("projection_rule.archived event %s not found", event_id)
            return
        rule_name = row["data"].get("name")
        if rule_name:
            await _delete_custom_projection(conn, user_id, rule_name)
            logger.info("Deleted custom projection '%s' for user=%s", rule_name, user_id)
        return

    retracted_ids = await get_retracted_event_ids(conn, user_id)
    # projection_rule.created — compute the new projection
    active_rules = await _load_active_rules(conn, user_id, retracted_ids=retracted_ids)

    # Find the rule that was just created (from the event)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT data FROM events WHERE id = %s AND user_id = %s",
            (event_id, user_id),
        )
        row = await cur.fetchone()

    if not row:
        logger.warning("projection_rule.created event %s not found", event_id)
        return

    rule_name = row["data"].get("name")
    if not rule_name:
        logger.warning("projection_rule.created event %s has no name", event_id)
        return

    # Check if this rule is still active (might have been archived already)
    if rule_name not in active_rules:
        logger.info("Rule '%s' was archived before processing, skipping", rule_name)
        await _delete_custom_projection(conn, user_id, rule_name)
        return

    rule_data = active_rules[rule_name]
    projection_data = await _compute_rule(conn, user_id, rule_data, retracted_ids)

    if projection_data is not None:
        await _upsert_custom_projection(conn, user_id, rule_name, projection_data, str(event_id))
        logger.info(
            "Custom projection '%s' computed for user=%s (events=%d)",
            rule_name,
            user_id,
            projection_data.get("data_quality", {}).get("total_events_processed", 0),
        )


# ---------------------------------------------------------------------------
# Router integration: recompute matching rules on regular events
# ---------------------------------------------------------------------------


async def recompute_matching_rules(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    event_type: str,
    event_id: str = "",
) -> None:
    """Recompute custom projections whose source_events include the given event_type.

    Called by the router when a regular event (e.g., sleep.logged) arrives and
    the user has active custom rules matching it.
    """
    retracted_ids = await get_retracted_event_ids(conn, user_id)
    active_rules = await _load_active_rules(conn, user_id, retracted_ids=retracted_ids)
    if not active_rules:
        return

    for rule_name, rule_data in active_rules.items():
        source_events = rule_data.get("source_events", [])
        if event_type not in source_events:
            continue

        projection_data = await _compute_rule(conn, user_id, rule_data, retracted_ids)
        if projection_data is not None:
            await _upsert_custom_projection(
                conn, user_id, rule_name, projection_data, event_id
            )
            logger.debug(
                "Recomputed custom projection '%s' for user=%s (triggered by %s)",
                rule_name, user_id, event_type,
            )


async def has_matching_custom_rules(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    event_type: str,
) -> bool:
    """Quick check: does this user have active custom rules matching the event_type?

    Uses active rule events, not existing custom projections, so rules remain
    live even if a custom projection row was deleted or not yet materialized.
    """
    retracted_ids = await get_retracted_event_ids(conn, user_id)
    active_rules = await _load_active_rules(conn, user_id, retracted_ids=retracted_ids)

    for rule_data in active_rules.values():
        source_events = rule_data.get("source_events", [])
        if event_type in source_events:
            return True

    return False
