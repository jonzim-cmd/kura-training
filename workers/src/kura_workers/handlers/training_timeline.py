"""Training Timeline dimension handler.

Reacts to set.logged, session.logged, set.corrected, exercise.alias_created, and
external.activity_imported events and computes temporal training patterns:
- Recent training days (last 30 with activity)
- Weekly summaries (last 26 weeks)
- Training frequency (rolling averages)
- Streak tracking (consecutive weeks with training)

Full recompute on every event — idempotent by design.
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..schema_capabilities import (
    build_schema_capability_report,
    detect_relation_capabilities,
)
from ..session_block_expansion import expand_session_logged_rows
from ..set_corrections import apply_set_correction_chain
from ..training_legacy_compat import extract_backfilled_set_event_ids
from ..training_load_v2 import (
    accumulate_row_load_v2,
    finalize_session_load_v2,
    init_session_load_v2,
    summarize_timeline_load_v2,
)
from ..training_rollout_v1 import is_training_load_v2_enabled
from ..training_session_completeness import evaluate_session_completeness
from ..utils import (
    SessionBoundaryState,
    epley_1rm,
    get_alias_map,
    get_retracted_event_ids,
    load_timezone_preference,
    local_date_for_timezone,
    merge_observed_attributes,
    next_fallback_session_key,
    normalize_temporal_point,
    normalize_timezone_name,
    resolve_exercise_key,
    resolve_timezone_context,
    resolve_through_aliases,
    separate_known_unknown,
)

logger = logging.getLogger(__name__)

# Fields actively processed by this handler for set.logged events.
_KNOWN_FIELDS: set[str] = {
    "exercise", "exercise_id", "weight_kg", "weight", "reps",
    "rpe", "rir", "rest_seconds", "tempo", "set_type", "set_number",
    "load_context", "implements_type", "equipment_profile",
    "block_type", "duration_seconds", "distance_meters", "contacts",
    "capability_target", "session_block_index", "session_block_repeat",
}


def _normalize_timezone_name(value: Any) -> str | None:
    return normalize_timezone_name(value)


def _resolve_timezone_context(timezone_pref: Any) -> dict[str, Any]:
    return resolve_timezone_context(timezone_pref)


def _local_date_for_timezone(ts: datetime, timezone_name: str) -> date:
    return local_date_for_timezone(ts, timezone_name)


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
            "total_load_score": round(float(entry.get("total_load_score", 0.0) or 0.0), 2),
            "total_load_confidence": round(
                (
                    float(entry.get("load_confidence_sum", 0.0) or 0.0)
                    / float(entry.get("load_confidence_count", 1) or 1)
                )
                if float(entry.get("load_confidence_count", 0) or 0) > 0.0
                else 0.0,
                2,
            ),
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
        if entry.get("source_provider") is not None:
            session_entry["source_provider"] = entry["source_provider"]
        if entry.get("source_type") is not None:
            session_entry["source_type"] = entry["source_type"]
        if isinstance(entry.get("load_v2"), dict):
            session_entry["load_v2"] = entry["load_v2"]
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


def _disabled_load_v2_overview() -> dict[str, Any]:
    return {
        "schema_version": "training_load.v2",
        "enabled": False,
        "status": "disabled_by_feature_flag",
        "sessions_total": 0,
        "parameter_versions": {},
        "modalities": {},
        "global": {
            "load_score": 0.0,
            "confidence": 0.0,
            "confidence_band": "low",
            "analysis_tier": "log_valid",
            "signal_density": {
                "rows_total": 0,
                "objective_rows": 0,
                "rows_with_hr": 0,
                "rows_with_power": 0,
                "rows_with_pace": 0,
                "rows_with_relative_intensity": 0,
                "rows_with_relative_intensity_fallback": 0,
            },
            "modality_assignment": {},
            "unknown_distance_exercise": {
                "rows": 0,
                "exercise_ids": {},
            },
            "relative_intensity": {
                "rows_used": 0,
                "rows_fallback": 0,
                "reference_types": {},
                "sources": {},
                "reference_confidence_avg": None,
            },
        },
    }


@projection_handler(
    "set.logged",
    "session.logged",
    "set.corrected",
    "exercise.alias_created",
    "external.activity_imported",
    dimension_meta={
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
        "timezone_context": {
            "timezone": "IANA timezone used for day/week grouping (e.g. Europe/Berlin)",
            "source": "preference|assumed_default",
            "assumed": "boolean",
            "assumption_disclosure": "string|null",
        },
        "recent_days": [{
            "date": "ISO 8601 date",
            "exercises": ["string — canonical exercise names"],
            "total_sets": "integer",
            "total_volume_kg": "number",
            "total_reps": "integer",
            "total_load_score": "number",
            "total_load_confidence": "number [0,1]",
            "top_sets": {"<exercise_id>": {"weight_kg": "number", "reps": "integer", "estimated_1rm": "number"}},
        }],
        "recent_sessions": [{
            "date": "ISO 8601 date",
            "exercises": ["string"],
            "total_sets": "integer",
            "total_volume_kg": "number",
            "total_reps": "integer",
            "session_id": "string (optional)",
            "source_provider": "string (optional, for externally imported sessions)",
            "source_type": "string (optional: manual|external_import)",
            "top_sets": {"<exercise_id>": {"weight_kg": "number", "reps": "integer", "estimated_1rm": "number"}},
            "load_v2": {
                "schema_version": "string",
                "parameter_version": "string",
                "modalities": "object (strength/sprint/endurance/plyometric/mixed)",
                "global": "object (load_score + confidence + analysis_tier)",
            },
        }],
        "load_v2_overview": {
            "schema_version": "string",
            "enabled": "boolean (feature-flag state)",
            "status": "string (optional, disabled_by_feature_flag when rollout is off)",
            "sessions_total": "integer",
            "parameter_versions": "object (parameter_version -> session_count)",
            "modalities": "object (aggregated modality-specific load and confidence)",
            "global": "object (aggregated load_score + confidence + confidence_band + analysis_tier)",
        },
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
            "corrected_set_rows": "integer — number of rows with set.corrected overlays",
            "temporal_conflicts": {"<conflict_type>": "integer — number of events with that conflict"},
            "external_imported_sessions": "integer",
            "external_source_providers": {"<provider>": "integer — imported sessions"},
            "external_low_confidence_fields": "integer",
            "external_unit_conversion_fields": "integer",
            "external_unsupported_fields_total": "integer",
            "external_dedup_actions": {
                "duplicate_skipped": "integer",
                "idempotent_replay": "integer",
                "rejected": "integer",
            },
            "external_temporal_uncertainty_hints": "integer",
            "schema_capabilities": {
                "status": "string — healthy|degraded",
                "missing_relations": ["string — missing DB relations"],
                "relations": {
                    "<relation_name>": {
                        "available": "boolean",
                        "required_by": ["string"],
                        "migration": "string | null",
                        "fallback_behavior": "string | null",
                    }
                },
            },
            "feature_flags": {
                "training_load_v2": "boolean",
            },
            "legacy_backfill_deduped_set_rows": "integer",
            "load_v2_modality_assignment": {
                "<assignment_source>": "integer — block_type/exercise/heuristic routing counts",
            },
            "load_v2_unknown_distance_exercise": {
                "rows": "integer",
                "exercise_ids": {"<exercise_id>": "integer"},
            },
            "load_v2_relative_intensity": {
                "rows_used": "integer",
                "rows_fallback": "integer",
                "reference_confidence_avg": "number|null",
            },
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
    timezone_pref = await load_timezone_preference(conn, user_id, retracted_ids)
    timezone_context = _resolve_timezone_context(timezone_pref)
    timezone_name = timezone_context["timezone"]

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
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = 'session.logged'
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        session_rows = await cur.fetchall()

    # Filter retracted events
    rows = [r for r in rows if str(r["id"]) not in retracted_ids]
    session_rows = [r for r in session_rows if str(r["id"]) not in retracted_ids]
    legacy_backfill_source_ids = extract_backfilled_set_event_ids(session_rows)
    if legacy_backfill_source_ids:
        rows = [
            row
            for row in rows
            if str(row["id"]) not in legacy_backfill_source_ids
        ]
    load_v2_enabled = is_training_load_v2_enabled()
    session_completeness_by_event_id: dict[str, dict[str, Any]] = {}
    for session_row in session_rows:
        payload = session_row.get("effective_data") or session_row.get("data") or {}
        if not isinstance(payload, dict):
            continue
        completeness = evaluate_session_completeness(payload)
        session_completeness_by_event_id[str(session_row["id"])] = {
            "tier": completeness.get("tier"),
            "confidence": completeness.get("confidence"),
            "log_valid": bool(completeness.get("log_valid")),
        }
    row_ids = [str(r["id"]) for r in rows]
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
    session_expanded_rows = expand_session_logged_rows(session_rows)
    for expanded in session_expanded_rows:
        hint = session_completeness_by_event_id.get(str(expanded.get("id")))
        if hint:
            expanded["_session_completeness_tier"] = hint.get("tier")
            expanded["_session_completeness_confidence"] = hint.get("confidence")
            expanded["_session_log_valid"] = hint.get("log_valid")

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = 'external.activity_imported'
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        external_rows = await cur.fetchall()
    external_rows = [r for r in external_rows if str(r["id"]) not in retracted_ids]

    relation_capabilities = await detect_relation_capabilities(
        conn,
        ["external_import_jobs"],
    )
    schema_capabilities = build_schema_capability_report(relation_capabilities)
    import_job_rows: list[dict[str, Any]] = []
    if relation_capabilities.get("external_import_jobs", False):
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT status, error_code, receipt
                FROM external_import_jobs
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 512
                """,
                (user_id,),
            )
            import_job_rows = await cur.fetchall()
    else:
        logger.warning(
            (
                "training_timeline schema capability degraded: "
                "missing relation external_import_jobs for user=%s"
            ),
            user_id,
        )

    if not rows and not session_rows and not external_rows:
        # Clean up: delete any existing projection (all events retracted)
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM projections WHERE user_id = %s AND projection_type = 'training_timeline' AND key = 'overview'",
                (user_id,),
            )
        return

    source_rows = rows + session_rows + external_rows
    source_rows.sort(key=lambda row: (row["timestamp"], str(row["id"])))
    last_event_id = source_rows[-1]["id"]

    # Aggregate by day, week, and session
    day_data: dict[date, dict[str, Any]] = defaultdict(
        lambda: {
            "exercises": set(),
            "total_sets": 0,
            "total_volume_kg": 0.0,
            "total_reps": 0,
            "total_load_score": 0.0,
            "load_confidence_sum": 0.0,
            "load_confidence_count": 0,
            "top_sets": {},
        }
    )
    week_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"training_days": set(), "total_sets": 0, "total_volume_kg": 0.0, "exercises": set()}
    )
    # Session grouping: key = session_id or date string (fallback)
    session_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "date": None,
            "session_id": None,
            "source_provider": None,
            "source_type": None,
            "exercises": set(),
            "total_sets": 0,
            "total_volume_kg": 0.0,
            "total_reps": 0,
            "top_sets": {},
            "load_v2": init_session_load_v2() if load_v2_enabled else None,
        }
    )
    observed_attr_counts: dict[str, dict[str, int]] = {}
    corrected_rows = 0
    temporal_conflicts: dict[str, int] = {}
    external_imported_sessions = 0
    external_source_providers: dict[str, int] = {}
    external_low_confidence_fields = 0
    external_unit_conversion_fields = 0
    external_unsupported_fields_total = 0
    external_temporal_uncertainty_hints = 0
    external_dedup_actions = {
        "duplicate_skipped": 0,
        "idempotent_replay": 0,
        "rejected": 0,
    }
    fallback_session_state: SessionBoundaryState | None = None

    external_rows_for_aggregation: list[dict[str, Any]] = []
    for external_row in external_rows:
        data = external_row.get("data") or {}
        source = data.get("source") or {}
        provider = str(source.get("provider") or "unknown").strip().lower() or "unknown"
        external_imported_sessions += 1
        external_source_providers[provider] = (
            external_source_providers.get(provider, 0) + 1
        )

        provenance = data.get("provenance") or {}
        unsupported_fields = provenance.get("unsupported_fields") or []
        if isinstance(unsupported_fields, list):
            external_unsupported_fields_total += sum(
                1 for entry in unsupported_fields if isinstance(entry, str) and entry.strip()
            )

        provenance_warnings = provenance.get("warnings") or []
        if isinstance(provenance_warnings, list):
            external_temporal_uncertainty_hints += sum(
                1
                for warning in provenance_warnings
                if isinstance(warning, str)
                and ("timezone" in warning.lower() or "drift" in warning.lower())
            )

        field_provenance = provenance.get("field_provenance") or {}
        if isinstance(field_provenance, dict):
            for entry in field_provenance.values():
                if not isinstance(entry, dict):
                    continue
                confidence_raw = entry.get("confidence", 1.0)
                try:
                    confidence = float(confidence_raw)
                except (TypeError, ValueError):
                    confidence = 1.0
                status = str(entry.get("status") or "mapped").strip().lower() or "mapped"
                if status != "mapped" or confidence < 0.86:
                    external_low_confidence_fields += 1
                unit_original = entry.get("unit_original")
                unit_normalized = entry.get("unit_normalized")
                if (
                    isinstance(unit_original, str)
                    and isinstance(unit_normalized, str)
                    and unit_original.strip()
                    and unit_normalized.strip()
                    and unit_original.strip() != unit_normalized.strip()
                ):
                    external_unit_conversion_fields += 1

        session_payload = data.get("session") or {}
        source_payload = data.get("source") or {}
        workout_payload = data.get("workout") or {}
        sets_payload = data.get("sets")
        if not isinstance(sets_payload, list) or not sets_payload:
            workout_type = (
                str(workout_payload.get("workout_type") or "external_activity")
                .strip()
                .lower()
                or "external_activity"
            )
            sets_payload = [{"exercise": workout_type, "exercise_id": workout_type}]

        metadata = dict(external_row.get("metadata") or {})
        synthetic_session_id = str(
            session_payload.get("session_id")
            or source_payload.get("external_activity_id")
            or ""
        ).strip()
        if synthetic_session_id:
            metadata["session_id"] = synthetic_session_id

        for set_entry in sets_payload:
            if not isinstance(set_entry, dict):
                continue
            synthetic_data = dict(set_entry)
            for key in (
                "duration_seconds",
                "distance_meters",
                "contacts",
                "heart_rate_avg",
                "heart_rate_max",
                "power_watt",
                "pace_min_per_km",
                "session_rpe",
                "rpe",
            ):
                if key in synthetic_data:
                    continue
                raw_value = workout_payload.get(key)
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if value > 0.0:
                    synthetic_data[key] = value
            if "relative_intensity" not in synthetic_data:
                relative_intensity = workout_payload.get("relative_intensity")
                if isinstance(relative_intensity, dict):
                    value_pct = relative_intensity.get("value_pct")
                    try:
                        parsed_pct = float(value_pct)
                    except (TypeError, ValueError):
                        parsed_pct = 0.0
                    if parsed_pct > 0.0:
                        synthetic_data["relative_intensity"] = dict(relative_intensity)
            if "exercise" not in synthetic_data and "exercise_id" not in synthetic_data:
                workout_type = (
                    str(workout_payload.get("workout_type") or "external_activity")
                    .strip()
                    .lower()
                    or "external_activity"
                )
                synthetic_data["exercise"] = workout_type
                synthetic_data["exercise_id"] = workout_type
            external_rows_for_aggregation.append(
                {
                    "id": external_row["id"],
                    "timestamp": external_row["timestamp"],
                    "data": synthetic_data,
                    "metadata": metadata,
                    "_source_type": "external_import",
                    "_source_provider": provider,
                    "_source_event_type": "external.activity_imported",
                }
            )

    for import_row in import_job_rows:
        receipt = import_row.get("receipt") or {}
        if isinstance(receipt, dict):
            write = receipt.get("write") or {}
            if isinstance(write, dict):
                result = str(write.get("result") or "").strip().lower()
                if result in {"duplicate_skipped", "idempotent_replay"}:
                    external_dedup_actions[result] += 1
        if str(import_row.get("status") or "") == "failed":
            error_code = str(import_row.get("error_code") or "").strip().lower()
            if error_code in {"stale_version", "version_conflict", "partial_overlap"}:
                external_dedup_actions["rejected"] += 1

    rows_for_aggregation = rows + session_expanded_rows + external_rows_for_aggregation
    rows_for_aggregation.sort(key=lambda row: (row["timestamp"], str(row["id"])))

    for row in rows_for_aggregation:
        data = row.get("effective_data") or row["data"]
        metadata = row.get("metadata") or {}
        if row.get("correction_history"):
            corrected_rows += 1
        temporal = normalize_temporal_point(
            row["timestamp"],
            timezone_name=timezone_name,
            data=data,
            metadata=metadata,
        )
        d = temporal.local_date
        w = temporal.iso_week

        for conflict in temporal.conflicts:
            temporal_conflicts[conflict] = temporal_conflicts.get(conflict, 0) + 1

        # Session key: explicit session_id wins; fallback is day-based with
        # overnight boundary handling for cross-midnight sessions.
        raw_session_id = str(metadata.get("session_id") or "").strip()
        session_id = raw_session_id or None
        if session_id is not None:
            session_key = session_id
            fallback_session_state = None
        else:
            session_key, fallback_session_state = next_fallback_session_key(
                local_date=d,
                timestamp_utc=temporal.timestamp_utc,
                state=fallback_session_state,
            )

        # Decision 10: track unknown fields
        _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS)
        source_event_type = str(row.get("_source_event_type") or "set.logged")
        merge_observed_attributes(observed_attr_counts, source_event_type, unknown)

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
        current_session_date = session_data[session_key]["date"]
        if current_session_date is None or d.isoformat() < current_session_date:
            session_data[session_key]["date"] = d.isoformat()
        session_data[session_key]["session_id"] = session_id
        session_data[session_key]["source_type"] = (
            str(row.get("_source_type")) if row.get("_source_type") else "manual"
        )
        if row.get("_source_provider"):
            session_data[session_key]["source_provider"] = str(row["_source_provider"])
        session_data[session_key]["exercises"].add(exercise_key)
        session_data[session_key]["total_sets"] += 1
        session_data[session_key]["total_volume_kg"] += volume
        session_data[session_key]["total_reps"] += reps

        load_v2_bucket = session_data[session_key].get("load_v2")
        if load_v2_enabled and isinstance(load_v2_bucket, dict):
            accumulate_row_load_v2(
                load_v2_bucket,
                data=data if isinstance(data, dict) else {},
                source_type=str(row.get("_source_type") or "manual"),
                session_confidence_hint=(
                    float(row.get("_session_completeness_confidence"))
                    if isinstance(row.get("_session_completeness_confidence"), (int, float))
                    else None
                ),
            )

        # Top set per exercise per session
        s_top = session_data[session_key]["top_sets"].get(exercise_key)
        if s_top is None or e1rm > s_top["estimated_1rm"]:
            session_data[session_key]["top_sets"][exercise_key] = {
                "weight_kg": weight, "reps": reps, "estimated_1rm": round(e1rm, 1),
            }

    if load_v2_enabled:
        for session_entry in session_data.values():
            load_v2 = session_entry.get("load_v2")
            if isinstance(load_v2, dict):
                session_entry["load_v2"] = finalize_session_load_v2(load_v2)
                day_iso = session_entry.get("date")
                if isinstance(day_iso, str):
                    try:
                        day_key = date.fromisoformat(day_iso)
                    except ValueError:
                        continue
                    global_load = load_v2.get("global") or {}
                    load_score = float(global_load.get("load_score", 0.0) or 0.0)
                    confidence = float(global_load.get("confidence", 0.0) or 0.0)
                    day_data[day_key]["total_load_score"] += load_score
                    day_data[day_key]["load_confidence_sum"] += confidence
                    day_data[day_key]["load_confidence_count"] += 1

    # Finalize week_data: convert training_days sets to counts
    for w_entry in week_data.values():
        w_entry["training_days"] = len(w_entry["training_days"])

    training_dates = set(day_data.keys())
    reference_date = max(training_dates)
    load_v2_overview = (
        {
            **summarize_timeline_load_v2(session_data),
            "enabled": True,
        }
        if load_v2_enabled
        else _disabled_load_v2_overview()
    )
    load_v2_global = load_v2_overview.get("global") if isinstance(load_v2_overview, dict) else {}
    if not isinstance(load_v2_global, dict):
        load_v2_global = {}

    projection_data = {
        "timezone_context": timezone_context,
        "recent_days": _compute_recent_days(day_data),
        "recent_sessions": _compute_recent_sessions(session_data),
        "load_v2_overview": load_v2_overview,
        "weekly_summary": _compute_weekly_summary(week_data),
        "current_frequency": _compute_frequency(training_dates, reference_date),
        "last_training": reference_date.isoformat(),
        "total_training_days": len(training_dates),
        "streak": _compute_streak(training_dates, reference_date),
        "data_quality": {
            "observed_attributes": observed_attr_counts,
            "corrected_set_rows": corrected_rows,
            "temporal_conflicts": temporal_conflicts,
            "external_imported_sessions": external_imported_sessions,
            "external_source_providers": external_source_providers,
            "external_low_confidence_fields": external_low_confidence_fields,
            "external_unit_conversion_fields": external_unit_conversion_fields,
            "external_unsupported_fields_total": external_unsupported_fields_total,
            "external_temporal_uncertainty_hints": external_temporal_uncertainty_hints,
            "external_dedup_actions": external_dedup_actions,
            "schema_capabilities": schema_capabilities,
            "feature_flags": {
                "training_load_v2": load_v2_enabled,
            },
            "legacy_backfill_deduped_set_rows": len(legacy_backfill_source_ids),
            "load_v2_modality_assignment": load_v2_global.get("modality_assignment", {}),
            "load_v2_unknown_distance_exercise": load_v2_global.get(
                "unknown_distance_exercise",
                {"rows": 0, "exercise_ids": {}},
            ),
            "load_v2_relative_intensity": load_v2_global.get(
                "relative_intensity",
                {"rows_used": 0, "rows_fallback": 0},
            ),
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
        "Updated training_timeline for user=%s (days=%d, weeks=%d, external_sessions=%d, timezone=%s, assumed=%s, temporal_conflicts=%s)",
        user_id,
        len(training_dates),
        len(week_data),
        external_imported_sessions,
        timezone_name,
        timezone_context["assumed"],
        temporal_conflicts,
    )
