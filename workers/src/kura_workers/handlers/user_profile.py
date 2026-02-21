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
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from ..recovery_daily_checkin import normalize_daily_checkin_payload
from ..registry import get_dimension_metadata, projection_handler, registered_event_types
from ..utils import get_retracted_event_ids

logger = logging.getLogger(__name__)

_INTERNAL_NON_ORPHAN_EVENT_TYPES = {
    "learning.signal.logged",
}

_BASELINE_REQUIRED_SLOTS = (
    "age_or_date_of_birth",
    "bodyweight_kg",
)
_BASELINE_OPTIONAL_SLOTS = (
    "sex",
    "body_composition_context",
)
_BODY_COMPOSITION_PROFILE_KEYS = (
    "body_fat_pct",
    "body_fat_percentage",
    "body_composition_note",
    "body_composition_notes",
)
_SESSION_STRENGTH_BLOCK_TYPES = {"strength_set", "explosive_power"}
_SESSION_ENDURANCE_BLOCK_TYPES = {
    "continuous_endurance",
    "interval_endurance",
    "tempo_threshold",
    "speed_endurance",
}


# --- Pure functions (testable without DB) ---


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def _slot_summary(
    *,
    status: str,
    value: Any = None,
    source: str | None = None,
    optional: bool = False,
    deferred_markers: list[str] | None = None,
) -> dict[str, Any]:
    slot: dict[str, Any] = {"status": status}
    if optional:
        slot["optional"] = True
    if status == "known" and value is not None:
        slot["value"] = value
    if source:
        slot["source"] = source
    if deferred_markers:
        slot["deferred_markers"] = deferred_markers
    return slot


def _infer_modality_from_session_blocks(blocks: list[dict[str, Any]]) -> str | None:
    has_strength = False
    has_endurance = False
    for block in blocks:
        block_type = str(block.get("block_type") or "").strip().lower()
        if block_type in _SESSION_STRENGTH_BLOCK_TYPES:
            has_strength = True
        if block_type in _SESSION_ENDURANCE_BLOCK_TYPES:
            has_endurance = True
    if has_strength and has_endurance:
        return "hybrid"
    if has_strength:
        return "strength"
    if has_endurance:
        return "endurance"
    return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_session_rpe(data: dict[str, Any]) -> float | None:
    for key in ("perceived_exertion", "session_rpe", "exertion", "rpe_summary"):
        parsed = _as_float(data.get(key))
        if parsed is None:
            continue
        if 1.0 <= parsed <= 10.0:
            return parsed
    return None


def _resolve_timezone_name(preferences: dict[str, Any]) -> str:
    for key in ("timezone", "time_zone"):
        value = preferences.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "UTC"


def _local_date_for_timestamp(ts: datetime, timezone_name: str) -> date:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    return ts.astimezone(tz).date()


def _training_tag_from_session_rpe(session_rpe: float) -> str:
    if session_rpe <= 4.0:
        return "easy"
    if session_rpe <= 7.0:
        return "average"
    return "hard"


def _derive_yesterday_training_summary(
    *,
    reference_timestamp: datetime,
    timezone_name: str,
    training_activity_by_day: dict[date, int],
    session_rpe_by_day: dict[date, list[float]],
) -> dict[str, Any]:
    reference_day = _local_date_for_timestamp(reference_timestamp, timezone_name)
    yesterday = reference_day - timedelta(days=1)
    activity_count = int(training_activity_by_day.get(yesterday, 0))
    day_rpe_values = list(session_rpe_by_day.get(yesterday) or [])

    if activity_count <= 0:
        return {
            "date": yesterday.isoformat(),
            "tag": "rest",
            "source": "auto:no_training_logged",
            "training_events": 0,
        }

    if day_rpe_values:
        avg_rpe = round(sum(day_rpe_values) / len(day_rpe_values), 2)
        return {
            "date": yesterday.isoformat(),
            "tag": _training_tag_from_session_rpe(avg_rpe),
            "source": "auto:session_rpe",
            "training_events": activity_count,
            "session_rpe_avg": avg_rpe,
        }

    return {
        "date": yesterday.isoformat(),
        "tag": "average",
        "source": "auto:training_logged_without_rpe",
        "training_events": activity_count,
    }


def _build_session_rpe_follow_up_signal(
    *,
    latest_timestamp: datetime,
    timezone_name: str,
    training_activity_by_day: dict[date, int],
    session_feedback_by_day: dict[date, int],
    session_rpe_by_day: dict[date, list[float]],
    lookback_days: int = 3,
) -> dict[str, Any] | None:
    reference_day = _local_date_for_timestamp(latest_timestamp, timezone_name)
    for offset in range(0, lookback_days + 1):
        day = reference_day - timedelta(days=offset)
        activity_count = int(training_activity_by_day.get(day, 0))
        if activity_count <= 0:
            continue
        rpe_values = list(session_rpe_by_day.get(day) or [])
        if rpe_values:
            continue
        feedback_count = int(session_feedback_by_day.get(day, 0))
        missing_type = (
            "missing_session_completed" if feedback_count == 0 else "missing_session_rpe"
        )
        return {
            "date": day.isoformat(),
            "type": missing_type,
            "training_events": activity_count,
            "session_feedback_events": feedback_count,
        }
    return None


def _compute_baseline_profile_summary(
    profile_data: dict[str, Any],
    bodyweight_event_count: int = 0,
    latest_bodyweight_kg: Any = None,
) -> dict[str, Any]:
    """Compute baseline profile slots with known/unknown/deferred semantics."""

    age_deferred_markers = [
        marker
        for marker in ["age_deferred", "date_of_birth_deferred"]
        if bool(profile_data.get(marker))
    ]
    age_value = profile_data.get("age")
    dob_value = profile_data.get("date_of_birth")
    if _has_meaningful_value(age_value):
        age_slot = _slot_summary(
            status="known", value=age_value, source="profile.updated.age"
        )
    elif _has_meaningful_value(dob_value):
        age_slot = _slot_summary(
            status="known",
            value=dob_value,
            source="profile.updated.date_of_birth",
        )
    elif age_deferred_markers:
        age_slot = _slot_summary(
            status="deferred",
            source="profile.updated",
            deferred_markers=age_deferred_markers,
        )
    else:
        age_slot = _slot_summary(status="unknown")

    bodyweight_deferred_markers = [
        marker
        for marker in ["bodyweight_deferred", "body_composition_deferred"]
        if bool(profile_data.get(marker))
    ]
    profile_bodyweight = profile_data.get("bodyweight_kg")
    if _has_meaningful_value(profile_bodyweight):
        bodyweight_slot = _slot_summary(
            status="known",
            value=profile_bodyweight,
            source="profile.updated.bodyweight_kg",
        )
    elif _has_meaningful_value(latest_bodyweight_kg):
        bodyweight_slot = _slot_summary(
            status="known",
            value=latest_bodyweight_kg,
            source="bodyweight.logged.weight_kg",
        )
    elif bodyweight_event_count > 0:
        bodyweight_slot = _slot_summary(
            status="known",
            source="bodyweight.logged",
        )
    elif bodyweight_deferred_markers:
        bodyweight_slot = _slot_summary(
            status="deferred",
            source="profile.updated",
            deferred_markers=bodyweight_deferred_markers,
        )
    else:
        bodyweight_slot = _slot_summary(status="unknown")

    sex_deferred_markers = []
    if bool(profile_data.get("sex_deferred")):
        sex_deferred_markers.append("sex_deferred")
    sex_value = profile_data.get("sex")
    if _has_meaningful_value(sex_value):
        sex_slot = _slot_summary(
            status="known",
            value=sex_value,
            source="profile.updated.sex",
            optional=True,
        )
    elif sex_deferred_markers:
        sex_slot = _slot_summary(
            status="deferred",
            source="profile.updated",
            optional=True,
            deferred_markers=sex_deferred_markers,
        )
    else:
        sex_slot = _slot_summary(status="unknown", optional=True)

    body_comp_deferred_markers = []
    if bool(profile_data.get("body_composition_deferred")):
        body_comp_deferred_markers.append("body_composition_deferred")
    if bool(profile_data.get("body_fat_pct_deferred")):
        body_comp_deferred_markers.append("body_fat_pct_deferred")
    body_comp_values = {
        key: profile_data.get(key)
        for key in _BODY_COMPOSITION_PROFILE_KEYS
        if _has_meaningful_value(profile_data.get(key))
    }
    if body_comp_values:
        body_comp_slot = _slot_summary(
            status="known",
            value=body_comp_values,
            source="profile.updated",
            optional=True,
        )
    elif body_comp_deferred_markers:
        body_comp_slot = _slot_summary(
            status="deferred",
            source="profile.updated",
            optional=True,
            deferred_markers=body_comp_deferred_markers,
        )
    else:
        body_comp_slot = _slot_summary(status="unknown", optional=True)

    slots = {
        "age_or_date_of_birth": age_slot,
        "bodyweight_kg": bodyweight_slot,
        "sex": sex_slot,
        "body_composition_context": body_comp_slot,
    }

    counts = {"known": 0, "deferred": 0, "unknown": 0}
    known_fields: list[str] = []
    deferred_fields: list[str] = []
    for slot_name, slot_data in slots.items():
        slot_status = slot_data["status"]
        if slot_status in counts:
            counts[slot_status] += 1
        if slot_status == "known":
            known_fields.append(slot_name)
        if slot_status == "deferred":
            deferred_fields.append(slot_name)

    required_missing = [
        slot_name
        for slot_name in _BASELINE_REQUIRED_SLOTS
        if slots[slot_name]["status"] == "unknown"
    ]
    required_deferred = [
        slot_name
        for slot_name in _BASELINE_REQUIRED_SLOTS
        if slots[slot_name]["status"] == "deferred"
    ]

    if required_missing:
        status = "needs_input"
    elif required_deferred:
        status = "deferred"
    else:
        status = "complete"

    return {
        "schema_version": "baseline_profile.v1",
        "status": status,
        "slots": slots,
        "required_slots": list(_BASELINE_REQUIRED_SLOTS),
        "optional_slots": list(_BASELINE_OPTIONAL_SLOTS),
        "required_missing": required_missing,
        "required_deferred": required_deferred,
        "known_fields": known_fields,
        "deferred_fields": deferred_fields,
        "counts": counts,
        "bodyweight_event_count": bodyweight_event_count,
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
    training_activity_range: tuple[str, str] | None,
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
        if training_activity_range:
            entry["coverage"] = {
                "from": training_activity_range[0],
                "to": training_activity_range[1],
            }

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
    bodyweight_event_count: int = 0,
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

        elif area == "baseline_profile":
            baseline_summary = _compute_baseline_profile_summary(
                profile_data,
                bodyweight_event_count=bodyweight_event_count,
            )
            required_missing = baseline_summary["required_missing"]
            required_deferred = baseline_summary["required_deferred"]
            if baseline_summary["status"] == "complete":
                coverage.append({"area": area, "status": "covered"})
            elif baseline_summary["status"] == "deferred":
                coverage.append({
                    "area": area,
                    "status": "deferred",
                    "note": (
                        "Deferred baseline fields: "
                        + ", ".join(sorted(required_deferred))
                    ),
                })
            else:
                coverage.append({
                    "area": area,
                    "status": "uncovered",
                    "note": (
                        "Missing baseline fields: "
                        + ", ".join(sorted(required_missing))
                    ),
                })

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
            has_runtime_style_override = any(
                key in preferences
                for key in ("autonomy_scope", "verbosity", "confirmation_strictness")
            )
            if profile_data.get("communication_style") or has_runtime_style_override:
                coverage.append({"area": area, "status": "covered"})
            else:
                coverage.append({"area": area, "status": "uncovered"})

        else:
            coverage.append({"area": area, "status": "uncovered"})

    return coverage


def _should_suggest_onboarding(
    total_events: int,
    coverage: list[dict[str, Any]],
    *,
    onboarding_closed: bool = False,
    onboarding_aborted: bool = False,
) -> bool:
    """Check if onboarding interview should be suggested.

    True while onboarding phase is open and key coverage remains unresolved.
    """
    if onboarding_closed or onboarding_aborted:
        return False

    unresolved_status = {"uncovered", "needs_depth"}
    unresolved_areas = {
        str(entry.get("area", "")).strip()
        for entry in coverage
        if str(entry.get("status", "")).strip().lower() in unresolved_status
    }
    unresolved_areas.discard("")

    # Core onboarding closure requirements from workflow gate.
    required_areas = {"training_background", "baseline_profile", "unit_preferences"}
    if required_areas & unresolved_areas:
        return True

    # Fallback for partial/synthetic coverage maps.
    unresolved_count = sum(
        1
        for entry in coverage
        if str(entry.get("status", "")).strip().lower() in unresolved_status
    )
    return unresolved_count >= 5


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
    workflow_onboarding_closed: bool = False,
    workflow_onboarding_aborted: bool = False,
    has_goals: bool = False,
    has_preferences: bool = False,
    observed_patterns: dict[str, Any] | None = None,
    baseline_summary: dict[str, Any] | None = None,
    session_rpe_follow_up: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build proactive agenda items for the agent.

    Includes data-quality items and interview triggers (Decision 8).
    Future: goal_at_risk, plateau_detected (needs Bayesian engine).
    """
    agenda: list[dict[str, Any]] = []

    # Interview triggers (Decision 8)
    if interview_coverage is not None:
        if _should_suggest_onboarding(
            total_events,
            interview_coverage,
            onboarding_closed=workflow_onboarding_closed,
            onboarding_aborted=workflow_onboarding_aborted,
        ):
            agenda.append({
                "priority": "high",
                "type": "onboarding_needed",
                "detail": (
                    "First contact with minimal data. Briefly explain Kura and how to "
                    "use it, then offer onboarding with Quick or Deep path "
                    "(Deep recommended) to bootstrap profile."
                ),
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

    if baseline_summary:
        required_missing = baseline_summary.get("required_missing", [])
        if required_missing:
            missing_fields = ", ".join(required_missing)
            priority = "high" if total_events < 5 else "medium"
            agenda.append({
                "priority": priority,
                "type": "baseline_profile_missing",
                "detail": (
                    "Baseline profile incomplete ("
                    + missing_fields
                    + "). Capture values or mark them explicitly deferred."
                ),
                "dimensions": ["user_profile"],
            })

    if session_rpe_follow_up:
        missing_type = str(session_rpe_follow_up.get("type") or "")
        day = str(session_rpe_follow_up.get("date") or "unknown_day")
        if missing_type == "missing_session_completed":
            detail = (
                f"Training logged on {day}, but no session.completed feedback found. "
                "Ask for quick post-session feedback with session RPE (1-10)."
            )
        else:
            detail = (
                f"Session feedback exists for {day}, but perceived_exertion/session_rpe is missing. "
                "Ask one follow-up for session RPE (1-10)."
            )
        agenda.append({
            "priority": "medium",
            "type": "session_rpe_follow_up",
            "detail": detail,
            "dimensions": ["session_feedback", "user_profile"],
            "evidence": session_rpe_follow_up,
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
    "session.logged",
    "set.corrected",
    "exercise.alias_created",
    "preference.set",
    "goal.set",
    "objective.set",
    "objective.updated",
    "objective.archived",
    "profile.updated",
    "program.started",
    "injury.reported",
    "bodyweight.logged",
    "measurement.logged",
    "sleep.logged",
    "soreness.logged",
    "energy.logged",
    "recovery.daily_checkin",
    "meal.logged",
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    "nutrition_target.set",
    "sleep_target.set",
    "weight_target.set",
    "session.completed",
    "advisory.override.recorded",
    "workflow.onboarding.closed",
    "workflow.onboarding.override_granted",
    "workflow.onboarding.aborted",
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
                  'set.logged', 'session.logged', 'set.corrected', 'exercise.alias_created', 'preference.set',
                  'goal.set', 'objective.set', 'objective.updated', 'objective.archived',
                  'profile.updated', 'program.started', 'injury.reported',
                  'bodyweight.logged', 'session.completed', 'recovery.daily_checkin',
                  'advisory.override.recorded',
                  'workflow.onboarding.closed', 'workflow.onboarding.override_granted',
                  'workflow.onboarding.aborted'
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
    objective_events: list[dict[str, Any]] = []
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
    bodyweight_event_count = 0
    latest_bodyweight_kg: Any = None
    workflow_onboarding_closed = False
    workflow_onboarding_aborted = False
    workflow_override_count = 0
    advisory_override_rationale_count = 0
    workflow_last_transition_at: str | None = None
    training_activity_timestamps: list[datetime] = []
    session_feedback_timestamps: list[datetime] = []
    session_feedback_rpe_points: list[tuple[datetime, float]] = []
    latest_daily_checkin_summary: dict[str, Any] | None = None

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
            training_activity_timestamps.append(row["timestamp"])
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

        elif event_type == "session.logged":
            data_blocks = data.get("blocks")
            blocks: list[dict[str, Any]] = []
            if isinstance(data_blocks, list):
                for block in data_blocks:
                    if isinstance(block, dict):
                        blocks.append(block)

            total_set_logged += len(blocks) if blocks else 1
            ts_date = row["timestamp"].date().isoformat()
            training_activity_timestamps.append(row["timestamp"])
            if first_set_logged_date is None:
                first_set_logged_date = ts_date
            last_set_logged_date = ts_date

            for block in blocks:
                block_exercise_id = str(block.get("exercise_id") or "").strip().lower()
                block_exercise = str(block.get("exercise") or "").strip().lower()
                block_type = str(block.get("block_type") or "").strip().lower()
                key = block_exercise_id or block_exercise or block_type
                if key:
                    exercises_logged.add(key)

            inferred_modality = _infer_modality_from_session_blocks(blocks)
            if inferred_modality and not _has_meaningful_value(
                profile_data.get("training_modality")
            ):
                profile_data["training_modality"] = inferred_modality

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

        elif event_type in {"objective.set", "objective.updated", "objective.archived"}:
            objective_events.append(data)
            primary_goal = data.get("primary_goal")
            if isinstance(primary_goal, dict) and primary_goal:
                goal_like = {
                    "goal_type": str(primary_goal.get("type") or "objective").strip().lower(),
                    "description": str(
                        primary_goal.get("description") or data.get("hypothesis") or ""
                    ).strip(),
                    "source": "objective_event",
                }
                goals.append(goal_like)

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

        elif event_type == "bodyweight.logged":
            bodyweight = data.get("weight_kg")
            if bodyweight is not None:
                bodyweight_event_count += 1
                latest_bodyweight_kg = bodyweight

        elif event_type == "session.completed":
            session_feedback_timestamps.append(row["timestamp"])
            parsed_rpe = _extract_session_rpe(data if isinstance(data, dict) else {})
            if parsed_rpe is not None:
                session_feedback_rpe_points.append((row["timestamp"], parsed_rpe))

        elif event_type == "recovery.daily_checkin":
            normalized = normalize_daily_checkin_payload(data if isinstance(data, dict) else {})
            latest_daily_checkin_summary = {
                "timestamp": row["timestamp"],
                "provided_training_yesterday": normalized.get("training_yesterday"),
                "parsed_from_compact": bool(normalized.get("parsed_from_compact")),
                "compact_input_mode": normalized.get("compact_input_mode"),
                "quality_flags": list(normalized.get("quality_flags") or []),
            }

        elif event_type == "advisory.override.recorded":
            advisory_override_rationale_count += 1

        elif event_type == "workflow.onboarding.closed":
            workflow_onboarding_closed = True
            workflow_onboarding_aborted = False
            workflow_last_transition_at = row["timestamp"].isoformat()

        elif event_type == "workflow.onboarding.override_granted":
            workflow_override_count += 1
            workflow_last_transition_at = row["timestamp"].isoformat()

        elif event_type == "workflow.onboarding.aborted":
            workflow_onboarding_aborted = True
            workflow_last_transition_at = row["timestamp"].isoformat()

    # Resolve exercises through alias map
    resolved_exercises = _resolve_exercises(exercises_logged, aliases)
    baseline_profile = _compute_baseline_profile_summary(
        profile_data,
        bodyweight_event_count=bodyweight_event_count,
        latest_bodyweight_kg=latest_bodyweight_kg,
    )
    workflow_state = {
        "phase": "planning" if workflow_onboarding_closed else "onboarding",
        "onboarding_closed": workflow_onboarding_closed,
        "onboarding_aborted": workflow_onboarding_aborted,
        "override_active": (not workflow_onboarding_closed) and workflow_override_count > 0,
        "override_count": workflow_override_count,
        "advisory_override_rationale_count": advisory_override_rationale_count,
        "last_transition_at": workflow_last_transition_at,
    }
    timezone_name = _resolve_timezone_name(preferences)

    training_activity_by_day: dict[date, int] = defaultdict(int)
    for timestamp in training_activity_timestamps:
        training_activity_by_day[_local_date_for_timestamp(timestamp, timezone_name)] += 1

    session_feedback_by_day: dict[date, int] = defaultdict(int)
    for timestamp in session_feedback_timestamps:
        session_feedback_by_day[_local_date_for_timestamp(timestamp, timezone_name)] += 1

    session_rpe_by_day: dict[date, list[float]] = defaultdict(list)
    for timestamp, session_rpe in session_feedback_rpe_points:
        session_rpe_by_day[_local_date_for_timestamp(timestamp, timezone_name)].append(
            float(session_rpe)
        )

    reference_for_yesterday = last_event
    provided_training_yesterday: str | None = None
    if latest_daily_checkin_summary is not None:
        summary_ts = latest_daily_checkin_summary.get("timestamp")
        if isinstance(summary_ts, datetime):
            reference_for_yesterday = summary_ts
        provided = latest_daily_checkin_summary.get("provided_training_yesterday")
        if isinstance(provided, str) and provided.strip():
            provided_training_yesterday = provided.strip().lower()

    yesterday_training_auto = _derive_yesterday_training_summary(
        reference_timestamp=reference_for_yesterday,
        timezone_name=timezone_name,
        training_activity_by_day=training_activity_by_day,
        session_rpe_by_day=session_rpe_by_day,
    )
    training_yesterday_resolved = provided_training_yesterday or yesterday_training_auto.get("tag")
    training_yesterday_source = (
        "user_provided:recovery.daily_checkin"
        if provided_training_yesterday
        else yesterday_training_auto.get("source")
    )
    daily_checkin_defaults: dict[str, Any] = {
        "timezone": timezone_name,
        "training_yesterday": training_yesterday_resolved,
        "training_yesterday_source": training_yesterday_source,
        "training_yesterday_auto": yesterday_training_auto,
    }
    if latest_daily_checkin_summary is not None:
        daily_checkin_defaults["latest_checkin"] = {
            "parsed_from_compact": latest_daily_checkin_summary.get("parsed_from_compact"),
            "compact_input_mode": latest_daily_checkin_summary.get("compact_input_mode"),
            "quality_flags": latest_daily_checkin_summary.get("quality_flags") or [],
        }

    session_rpe_follow_up = _build_session_rpe_follow_up_signal(
        latest_timestamp=last_event,
        timezone_name=timezone_name,
        training_activity_by_day=training_activity_by_day,
        session_feedback_by_day=session_feedback_by_day,
        session_rpe_by_day=session_rpe_by_day,
    )

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
        aliases,
        preferences,
        goals,
        profile_data,
        injuries,
        bodyweight_event_count=bodyweight_event_count,
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

    training_activity_range = None
    if first_set_logged_date and last_set_logged_date:
        training_activity_range = (first_set_logged_date, last_set_logged_date)

    user_dimensions = _build_user_dimensions(
        dimension_metadata, projection_rows, training_activity_range
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
        workflow_onboarding_closed=workflow_onboarding_closed,
        workflow_onboarding_aborted=workflow_onboarding_aborted,
        has_goals=bool(goals),
        has_preferences=bool(preferences),
        observed_patterns=observed_patterns,
        baseline_summary=baseline_profile,
        session_rpe_follow_up=session_rpe_follow_up,
    )

    projection_data = {
        "user": {
            "aliases": aliases,
            "preferences": preferences,
            "goals": goals,
            "objectives": objective_events if objective_events else None,
            "profile": profile_data if profile_data else None,
            "injuries": injuries if injuries else None,
            "baseline_profile": baseline_profile,
            "workflow_state": workflow_state,
            "daily_checkin_defaults": daily_checkin_defaults,
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
