"""Exercise Progression projection handler.

Reacts to set.logged, set.corrected, and exercise.alias_created events.
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
from ..set_corrections import apply_set_correction_chain
from ..training_core_fields import evaluate_set_context_rows
from ..utils import (
    SessionBoundaryState,
    check_expected_fields,
    epley_1rm,
    find_all_keys_for_canonical,
    get_alias_map,
    get_retracted_event_ids,
    load_timezone_preference,
    merge_observed_attributes,
    next_fallback_session_key,
    normalize_temporal_point,
    resolve_exercise_key,
    resolve_timezone_context,
    resolve_through_aliases,
    separate_known_unknown,
)

logger = logging.getLogger(__name__)

# Fields that this handler actively processes for set.logged events.
# Everything else is passed through as observed_attributes (Decision 10).
_KNOWN_FIELDS: set[str] = {
    "exercise", "exercise_id", "weight_kg", "weight", "reps",
    "rpe", "rir", "rest_seconds", "tempo", "set_type", "set_number",
    "load_context", "implements_type", "equipment_profile",
}

# Fields we *expect* for typical strength sets. Missing = data_quality hint.
_EXPECTED_FIELDS: dict[str, str] = {
    "weight_kg": "No weight — bodyweight or assisted exercise?",
    "reps": "No reps — time-based or isometric exercise?",
}


def _as_optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _normalize_rir(value: Any) -> float | None:
    parsed = _as_optional_float(value)
    if parsed is None:
        return None
    if parsed < 0:
        return 0.0
    if parsed > 10:
        return 10.0
    return round(parsed, 2)


def _infer_rir_from_rpe(rpe: float | None) -> float | None:
    if rpe is None:
        return None
    inferred = 10.0 - rpe
    return _normalize_rir(inferred)


def _resolve_set_rir(data: dict[str, Any], parsed_rpe: float | None) -> tuple[float | None, str | None]:
    explicit = _normalize_rir(data.get("rir"))
    if explicit is not None:
        return explicit, "explicit"
    inferred = _infer_rir_from_rpe(parsed_rpe)
    if inferred is not None:
        return inferred, "inferred_from_rpe"
    return None, None


def _iso_week(d) -> str:
    """Return ISO week string like '2026-W06'."""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _normalize_token(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace(" ", "_")
    if not normalized:
        return None
    return normalized


def _extract_changed_field_value(changed_fields: Any, field: str) -> str | None:
    if not isinstance(changed_fields, dict):
        return None
    candidate = changed_fields.get(field)
    if isinstance(candidate, dict):
        candidate = candidate.get("value")
    normalized = _normalize_token(candidate)
    if normalized is None:
        return None
    return normalized


def _resolve_comparability_group(data: dict[str, Any]) -> str:
    """Resolve comparability boundary for set aggregation.

    Groups prevent semantically different load contexts (e.g. smith machine
    vs free-weight barbell) from silently merging progression statistics.
    """
    load_context = data.get("load_context") if isinstance(data, dict) else None
    context_group = None
    context_impl = None
    context_equipment = None
    if isinstance(load_context, dict):
        context_group = _normalize_token(load_context.get("comparability_group"))
        context_impl = _normalize_token(load_context.get("implements_type"))
        context_equipment = _normalize_token(load_context.get("equipment_profile"))

    if context_group:
        return context_group

    exercise_id = _normalize_token(data.get("exercise_id")) if isinstance(data, dict) else None
    exercise = _normalize_token(data.get("exercise")) if isinstance(data, dict) else None
    equipment = context_equipment or _normalize_token(
        data.get("equipment_profile") if isinstance(data, dict) else None
    )
    implements_type = context_impl or _normalize_token(
        data.get("implements_type") if isinstance(data, dict) else None
    )

    hint_values = [
        value
        for value in [exercise_id, exercise, equipment, implements_type]
        if value is not None
    ]
    if any("smith" in value for value in hint_values):
        return "machine:smith"
    if any("trap_bar" in value for value in hint_values):
        return "free_weight:trap_bar"
    if any("safety_bar" in value for value in hint_values):
        return "free_weight:safety_bar"

    if implements_type in {"barbell", "dumbbell", "kettlebell", "free_weight"}:
        return "free_weight"
    if implements_type in {"bodyweight", "calisthenics"}:
        return "bodyweight"
    if implements_type in {
        "machine",
        "selectorized_machine",
        "plate_loaded_machine",
        "cable_machine",
    }:
        return f"machine:{implements_type}"
    if equipment and "machine" in equipment:
        return f"machine:{equipment}"
    if equipment in {"barbell", "dumbbell", "kettlebell"}:
        return "free_weight"
    return "unspecified"


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract summary for user_profile manifest (Decision 7)."""
    return {"exercises": [r["key"] for r in projection_rows]}


@projection_handler("set.logged", "set.corrected", "exercise.alias_created", dimension_meta={
    "name": "exercise_progression",
    "description": "Strength progression per exercise over time",
    "key_structure": "one per exercise (exercise_id as key, primary comparability scope)",
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
        "timezone_context": {
            "timezone": "IANA timezone used for day/week grouping (e.g. Europe/Berlin)",
            "source": "preference|assumed_default",
            "assumed": "boolean",
            "assumption_disclosure": "string|null",
        },
        "recent_sessions": [{
            "timestamp": "ISO 8601 datetime",
            "weight_kg": "number",
            "reps": "integer",
            "estimated_1rm": "number — Epley formula",
            "comparability_group": "string — semantic load context boundary",
            "rpe": "number (optional)",
            "rir": "number (optional)",
            "rir_source": "string (optional: explicit|inferred_from_rpe|session_default)",
            "rest_seconds": "number (optional)",
            "rest_seconds_source": "string (optional: explicit|session_default)",
            "tempo": "string (optional)",
            "tempo_source": "string (optional: explicit|session_default)",
            "set_type": "string (optional)",
            "corrections": ["object (optional) — applied correction chain entries"],
            "field_provenance": "object (optional) — latest provenance per corrected field",
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
        "comparability": {
            "primary_group": "string — group used for top-level aggregation",
            "groups_total": "integer",
            "multiple_groups_detected": "boolean",
            "groups": [{
                "group": "string",
                "total_sets": "integer",
                "total_volume_kg": "number",
                "estimated_1rm": "number",
                "estimated_1rm_date": "ISO 8601 datetime | null",
            }],
        },
        "data_quality": {
            "anomalies": [{"event_id": "string", "field": "string", "value": "any", "expected_range": "[min, max]", "message": "string"}],
            "field_hints": [{"field": "string", "hint": "string"}],
            "observed_attributes": {"<event_type>": {"<field>": "integer — count"}},
            "temporal_conflicts": {"<conflict_type>": "integer — number of events with that conflict"},
        },
    },
    "manifest_contribution": _manifest_contribution,
})
async def update_exercise_progression(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of exercise_progression projection for affected exercise(s)."""
    user_id = payload["user_id"]
    event_id = payload["event_id"]
    event_type = payload.get("event_type", "")

    retracted_ids = await get_retracted_event_ids(conn, user_id)
    alias_map = await get_alias_map(conn, user_id, retracted_ids=retracted_ids)
    timezone_pref = await load_timezone_preference(conn, user_id, retracted_ids)
    timezone_context = resolve_timezone_context(timezone_pref)
    timezone_name = timezone_context["timezone"]

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT data FROM events WHERE id = %s AND user_id = %s",
            (event_id, user_id),
        )
        trigger_row = await cur.fetchone()
        if trigger_row is None:
            logger.warning("Event %s not found, skipping", event_id)
            return

    canonical_targets: set[str] = set()
    trigger_data = trigger_row.get("data") or {}
    if event_type == "exercise.alias_created":
        canonical = _normalize_token(trigger_data.get("exercise_id"))
        if not canonical:
            logger.warning("Alias event %s has no exercise_id, skipping", event_id)
            return
        canonical_targets.add(resolve_through_aliases(canonical, alias_map))
    elif event_type == "set.corrected":
        target_event_id = str(trigger_data.get("target_event_id", "")).strip()
        if not target_event_id:
            logger.warning("Correction event %s has no target_event_id, skipping", event_id)
            return
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT data
                FROM events
                WHERE user_id = %s
                  AND id = %s
                  AND event_type = 'set.logged'
                """,
                (user_id, target_event_id),
            )
            target_row = await cur.fetchone()
        if target_row is None:
            logger.warning(
                "Correction event %s references missing set.logged target %s",
                event_id,
                target_event_id,
            )
            return
        raw_key = resolve_exercise_key(target_row["data"])
        if raw_key:
            canonical_targets.add(resolve_through_aliases(raw_key, alias_map))

        changed_fields = trigger_data.get("changed_fields")
        changed_exercise = _extract_changed_field_value(changed_fields, "exercise_id")
        if changed_exercise is None:
            changed_exercise = _extract_changed_field_value(changed_fields, "exercise")
        if changed_exercise:
            canonical_targets.add(resolve_through_aliases(changed_exercise, alias_map))
    else:
        raw_key = resolve_exercise_key(trigger_data)
        if not raw_key:
            logger.warning("Event %s has no exercise field, skipping", event_id)
            return
        canonical_targets.add(resolve_through_aliases(raw_key, alias_map))

    if not canonical_targets:
        logger.warning(
            "No canonical targets resolved for event %s (%s), skipping",
            event_id,
            event_type,
        )
        return

    for canonical in sorted(canonical_targets):
        all_keys = find_all_keys_for_canonical(canonical, alias_map)
        all_keys_list = list(all_keys)

        rows: list[dict[str, Any]] = []
        if all_keys_list:
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
                    ORDER BY timestamp ASC, id ASC
                    """,
                    (user_id, all_keys_list, all_keys_list),
                )
                rows.extend(await cur.fetchall())

            # Re-key fix: include rows that were corrected INTO this canonical key.
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT DISTINCT s.id, s.event_type, s.timestamp, s.data, s.metadata
                    FROM events s
                    JOIN events c
                      ON c.user_id = s.user_id
                     AND c.event_type = 'set.corrected'
                     AND c.data->>'target_event_id' = s.id::text
                    WHERE s.user_id = %s
                      AND s.event_type = 'set.logged'
                      AND (
                          lower(trim(
                              coalesce(
                                  c.data#>>'{changed_fields,exercise_id,value}',
                                  c.data#>>'{changed_fields,exercise_id}',
                                  ''
                              )
                          )) = ANY(%s)
                          OR lower(trim(
                              coalesce(
                                  c.data#>>'{changed_fields,exercise,value}',
                                  c.data#>>'{changed_fields,exercise}',
                                  ''
                              )
                          )) = ANY(%s)
                      )
                    ORDER BY s.timestamp ASC, s.id ASC
                    """,
                    (user_id, all_keys_list, all_keys_list),
                )
                rows.extend(await cur.fetchall())

        deduped_by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            deduped_by_id[str(row["id"])] = row
        rows = sorted(
            deduped_by_id.values(),
            key=lambda row: (row["timestamp"], str(row["id"])),
        )

        rows = [row for row in rows if str(row["id"]) not in retracted_ids]
        row_ids = [str(row["id"]) for row in rows]
        correction_rows: list[dict[str, Any]] = []
        if row_ids:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id, timestamp, data
                    FROM events
                    WHERE user_id = %s
                      AND event_type = 'set.corrected'
                      AND data->>'target_event_id' = ANY(%s)
                    ORDER BY timestamp ASC, id ASC
                    """,
                    (user_id, row_ids),
                )
                correction_rows = await cur.fetchall()
        correction_rows = [
            row for row in correction_rows if str(row["id"]) not in retracted_ids
        ]
        rows = apply_set_correction_chain(rows, correction_rows)

        # Keep only rows that currently resolve to this canonical key.
        filtered_rows: list[dict[str, Any]] = []
        for row in rows:
            effective_data = row.get("effective_data") or row["data"]
            effective_key = resolve_exercise_key(effective_data)
            if not effective_key:
                continue
            if resolve_through_aliases(effective_key, alias_map) == canonical:
                filtered_rows.append(row)
        rows = filtered_rows

        context_by_event_id = {
            entry["event_id"]: entry
            for entry in evaluate_set_context_rows(rows)
            if entry.get("event_id")
        }

        if not rows:
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
                (
                    "Deleted exercise_progression for user=%s exercise=%s "
                    "(no effective rows after corrections/retractions)"
                ),
                user_id,
                canonical,
            )
            continue

        comparability_summary: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "total_sets": 0,
                "total_volume_kg": 0.0,
                "estimated_1rm": 0.0,
                "estimated_1rm_date": None,
                "latest_timestamp": None,
            }
        )
        row_comparability_group: dict[str, str] = {}
        for row in rows:
            data = row.get("effective_data") or row["data"]
            group = _resolve_comparability_group(data)
            row_id = str(row["id"])
            row_comparability_group[row_id] = group
            bucket = comparability_summary[group]
            bucket["total_sets"] += 1
            bucket["latest_timestamp"] = row["timestamp"]
            try:
                weight = float(data.get("weight_kg", data.get("weight", 0)))
                reps = int(data.get("reps", 0))
            except (TypeError, ValueError):
                continue
            volume = weight * reps
            bucket["total_volume_kg"] += volume
            e1rm = epley_1rm(weight, reps)
            if e1rm > bucket["estimated_1rm"]:
                bucket["estimated_1rm"] = e1rm
                bucket["estimated_1rm_date"] = row["timestamp"]

        latest_row_id = str(rows[-1]["id"])
        primary_group = row_comparability_group.get(latest_row_id, "unspecified")

        total_sets = 0
        total_volume_kg = 0.0
        best_1rm = 0.0
        best_1rm_date: datetime | None = None
        session_keys: set[str] = set()
        recent_sets: list[dict[str, Any]] = []
        last_event_id = rows[-1]["id"]
        week_data: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "estimated_1rm": 0.0,
                "total_sets": 0,
                "total_volume_kg": 0.0,
                "max_weight_kg": 0.0,
            }
        )
        anomalies: list[dict[str, Any]] = []
        field_hints: list[dict[str, Any]] = []
        observed_attr_counts: dict[str, dict[str, int]] = {}
        temporal_conflicts: dict[str, int] = {}
        fallback_session_state: SessionBoundaryState | None = None

        for row in rows:
            data = row.get("effective_data") or row["data"]
            metadata = row.get("metadata") or {}
            context_eval = context_by_event_id.get(str(row["id"]), {})
            effective_defaults = context_eval.get("effective_defaults") or {}
            temporal = normalize_temporal_point(
                row["timestamp"],
                timezone_name=timezone_name,
                data=data,
                metadata=metadata,
            )
            ts = temporal.timestamp_utc
            local_day = temporal.local_date
            group = row_comparability_group.get(str(row["id"]), "unspecified")

            for conflict in temporal.conflicts:
                temporal_conflicts[conflict] = temporal_conflicts.get(conflict, 0) + 1

            _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS)
            merge_observed_attributes(observed_attr_counts, row["event_type"], unknown)

            try:
                weight = float(data.get("weight_kg", data.get("weight", 0)))
                reps = int(data.get("reps", 0))
            except (ValueError, TypeError):
                logger.warning("Skipping event %s: invalid weight/reps data", row["id"])
                continue

            if weight < 0 or weight > 500:
                anomalies.append({
                    "event_id": str(row["id"]),
                    "field": "weight_kg",
                    "value": weight,
                    "expected_range": [0, 500],
                    "message": f"Weight {weight}kg outside plausible range on {local_day.isoformat()}",
                })
            if reps < 0 or reps > 100:
                anomalies.append({
                    "event_id": str(row["id"]),
                    "field": "reps",
                    "value": reps,
                    "expected_range": [0, 100],
                    "message": f"{reps} reps in a single set on {local_day.isoformat()}",
                })

            if group != primary_group:
                continue

            raw_session_id = str(metadata.get("session_id") or "").strip()
            session_id = raw_session_id or None
            if session_id is not None:
                session_key = session_id
                fallback_session_state = None
            else:
                session_key, fallback_session_state = next_fallback_session_key(
                    local_date=local_day,
                    timestamp_utc=ts,
                    state=fallback_session_state,
                )

            volume = weight * reps
            total_sets += 1
            total_volume_kg += volume
            session_keys.add(session_key)
            e1rm = epley_1rm(weight, reps)

            if best_1rm > 0 and e1rm > best_1rm * 2:
                anomalies.append({
                    "event_id": str(row["id"]),
                    "field": "estimated_1rm",
                    "value": round(e1rm, 1),
                    "expected_range": [0, round(best_1rm * 2, 1)],
                    "message": (
                        f"1RM jumped from {best_1rm:.1f}kg to {e1rm:.1f}kg "
                        f"({(e1rm / best_1rm - 1) * 100:.0f}% increase) on {local_day.isoformat()}"
                    ),
                })

            if e1rm > best_1rm:
                best_1rm = e1rm
                best_1rm_date = ts

            week_key = temporal.iso_week
            week_entry = week_data[week_key]
            week_entry["total_sets"] += 1
            week_entry["total_volume_kg"] += volume
            if e1rm > week_entry["estimated_1rm"]:
                week_entry["estimated_1rm"] = e1rm
            if weight > week_entry["max_weight_kg"]:
                week_entry["max_weight_kg"] = weight

            set_entry: dict[str, Any] = {
                "timestamp": ts.isoformat(),
                "weight_kg": weight,
                "reps": reps,
                "estimated_1rm": round(e1rm, 1),
                "comparability_group": group,
                "_session_key": session_key,
            }
            parsed_rpe: float | None = None
            if "rpe" in data:
                try:
                    parsed_rpe = float(data["rpe"])
                    set_entry["rpe"] = parsed_rpe
                except (ValueError, TypeError):
                    pass
            parsed_rir, rir_source = _resolve_set_rir(data, parsed_rpe)
            if parsed_rir is None and effective_defaults.get("rir") is not None:
                parsed_rir = _normalize_rir(effective_defaults.get("rir"))
                rir_source = "session_default"
            if parsed_rir is not None:
                set_entry["rir"] = parsed_rir
                if rir_source and rir_source != "explicit":
                    set_entry["rir_source"] = rir_source

            explicit_rest = _as_optional_float(data.get("rest_seconds"))
            if explicit_rest is not None:
                set_entry["rest_seconds"] = round(explicit_rest, 2)
            elif effective_defaults.get("rest_seconds") is not None:
                default_rest = _as_optional_float(effective_defaults.get("rest_seconds"))
                if default_rest is not None:
                    set_entry["rest_seconds"] = round(default_rest, 2)
                    set_entry["rest_seconds_source"] = "session_default"

            if isinstance(data.get("tempo"), str) and data["tempo"].strip():
                set_entry["tempo"] = data["tempo"].strip().lower()
            elif isinstance(effective_defaults.get("tempo"), str):
                default_tempo = str(effective_defaults.get("tempo")).strip().lower()
                if default_tempo:
                    set_entry["tempo"] = default_tempo
                    set_entry["tempo_source"] = "session_default"

            if "set_type" in data:
                set_entry["set_type"] = data["set_type"]
            elif effective_defaults.get("set_type") is not None:
                set_entry["set_type"] = effective_defaults.get("set_type")
            if session_id is not None:
                set_entry["session_id"] = session_id
            if unknown:
                set_entry["extra"] = unknown
            if row.get("correction_history"):
                set_entry["corrections"] = row["correction_history"]
            if row.get("field_provenance"):
                set_entry["field_provenance"] = row["field_provenance"]
            if isinstance(data.get("load_context"), dict):
                set_entry["load_context"] = data.get("load_context")

            recent_sets.append(set_entry)

        session_last_ts: dict[str, str] = {}
        for set_row in recent_sets:
            session_key = set_row["_session_key"]
            if (
                session_key not in session_last_ts
                or set_row["timestamp"] > session_last_ts[session_key]
            ):
                session_last_ts[session_key] = set_row["timestamp"]
        sorted_session_keys = sorted(
            session_last_ts,
            key=lambda key: session_last_ts[key],
            reverse=True,
        )[:5]
        recent_session_set = set(sorted_session_keys)
        recent_sessions = [
            {k: v for k, v in set_row.items() if k != "_session_key"}
            for set_row in recent_sets
            if set_row["_session_key"] in recent_session_set
        ]
        recent_sessions.reverse()

        sorted_weeks = sorted(week_data.keys(), reverse=True)[:26]
        sorted_weeks.reverse()
        weekly_history = [
            {
                "week": week_key,
                "estimated_1rm": round(week_data[week_key]["estimated_1rm"], 1),
                "total_sets": week_data[week_key]["total_sets"],
                "total_volume_kg": round(week_data[week_key]["total_volume_kg"], 1),
                "max_weight_kg": round(week_data[week_key]["max_weight_kg"], 1),
            }
            for week_key in sorted_weeks
        ]

        primary_rows = [
            row
            for row in rows
            if row_comparability_group.get(str(row["id"])) == primary_group
        ]
        if primary_rows:
            latest_data = primary_rows[-1].get("effective_data") or primary_rows[-1]["data"]
            field_hints = check_expected_fields(latest_data, _EXPECTED_FIELDS)

        comparability_groups = []
        for group, summary in sorted(
            comparability_summary.items(),
            key=lambda item: (item[1]["latest_timestamp"], item[0]),
            reverse=True,
        ):
            estimated_date = summary.get("estimated_1rm_date")
            if isinstance(estimated_date, datetime):
                estimated_date_value: str | None = estimated_date.isoformat()
            else:
                estimated_date_value = None
            comparability_groups.append({
                "group": group,
                "total_sets": int(summary.get("total_sets", 0)),
                "total_volume_kg": round(float(summary.get("total_volume_kg", 0.0)), 1),
                "estimated_1rm": round(float(summary.get("estimated_1rm", 0.0)), 1),
                "estimated_1rm_date": estimated_date_value,
            })

        projection_data: dict[str, Any] = {
            "exercise": canonical,
            "estimated_1rm": round(best_1rm, 1),
            "estimated_1rm_date": best_1rm_date.isoformat() if best_1rm_date else None,
            "total_sessions": len(session_keys),
            "total_sets": total_sets,
            "total_volume_kg": round(total_volume_kg, 1),
            "timezone_context": timezone_context,
            "recent_sessions": recent_sessions,
            "weekly_history": weekly_history,
            "comparability": {
                "primary_group": primary_group,
                "groups_total": len(comparability_groups),
                "multiple_groups_detected": len(comparability_groups) > 1,
                "groups": comparability_groups,
            },
            "data_quality": {
                "anomalies": anomalies,
                "field_hints": field_hints,
                "observed_attributes": observed_attr_counts,
                "temporal_conflicts": temporal_conflicts,
            },
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
                (user_id, canonical, json.dumps(projection_data), str(last_event_id)),
            )

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
                user_id,
                stale_keys,
                canonical,
            )

        logger.info(
            (
                "Updated exercise_progression for user=%s exercise=%s "
                "(sets=%d, 1rm=%.1f, primary_group=%s, timezone=%s, assumed=%s)"
            ),
            user_id,
            canonical,
            total_sets,
            best_1rm,
            primary_group,
            timezone_name,
            timezone_context["assumed"],
        )
