"""Training Plan dimension handler.

The only PRESCRIPTIVE dimension — describes what SHOULD happen,
not what DID happen. All other dimensions are descriptive.

Reacts to training_plan.created, training_plan.updated, training_plan.archived.
Computes the active plan, weekly template, and plan history.

Plan structure: weekly template with named sessions. The agent derives
concrete loads from exercise_progression at conversation time.

Full recompute on every event — idempotent by design.
"""

import copy
import json
import logging
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..utils import get_retracted_event_ids

logger = logging.getLogger(__name__)

TRAINING_PLAN_OVERVIEW_KEY = "overview"
TRAINING_PLAN_DETAILS_KEY = "details"
TRAINING_PLAN_DETAILS_SCHEMA_VERSION = "training_plan.details.v1"
TRAINING_PLAN_DETAIL_LOCATOR_SCHEMA_VERSION = "training_plan.detail_locator.v1"
_PLAN_BASE_METADATA_KEYS = {"name", "cycle_weeks", "notes"}
_PLAN_METADATA_KEYS = _PLAN_BASE_METADATA_KEYS | {"sessions"}
_PLAN_UPDATE_NON_DETAIL_KEYS = _PLAN_BASE_METADATA_KEYS | {"plan_id"}
_HEADER_EXERCISE_KEYS = {
    "exercise_id",
    "name",
    "target_rir",
    "target_rir_source",
    "target_rpe",
    "rpe",
}
_DETAIL_HINT_KEYS = {
    "sets",
    "reps",
    "rep_range",
    "target_reps",
    "rest_seconds",
    "rest_minutes",
    "duration_minutes",
    "duration_seconds",
    "distance_m",
    "tempo",
    "load_kg",
    "weight_kg",
}


def _build_event_ref(event_row: dict[str, Any]) -> dict[str, str]:
    event_id = str(event_row.get("id") or "").strip()
    event_type = str(event_row.get("event_type") or "").strip()
    timestamp = event_row.get("timestamp")
    if isinstance(timestamp, datetime):
        timestamp_iso = timestamp.isoformat()
    else:
        timestamp_iso = str(timestamp or "").strip()
    return {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": timestamp_iso,
    }


def _normalized_plan_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    payload: dict[str, Any] = {}
    for key, value in data.items():
        if key == "plan_id":
            continue
        if key == "sessions":
            payload[key] = _normalize_plan_sessions_with_rir(value)
            continue
        payload[key] = copy.deepcopy(value)
    return payload


def _merge_plan_payload(existing: Any, delta: Any) -> dict[str, Any]:
    merged = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    for key, value in _normalized_plan_payload(delta).items():
        merged[key] = value
    return merged


def _event_contains_plan_detail_delta(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if "sessions" in data:
        return True
    return any(key not in _PLAN_UPDATE_NON_DETAIL_KEYS for key in data.keys())


def _compute_plan_detail_signals(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {
            "detail_level": "none",
            "detail_available": False,
            "sessions_total": 0,
            "exercises_total": 0,
            "exercises_with_prescription_detail": 0,
            "top_level_detail_keys": [],
        }

    sessions = payload.get("sessions")
    sessions_total = 0
    exercises_total = 0
    exercises_with_detail = 0
    if isinstance(sessions, list):
        sessions_total = len(sessions)
        for session in sessions:
            if not isinstance(session, dict):
                continue
            exercises = session.get("exercises")
            if not isinstance(exercises, list):
                continue
            for exercise in exercises:
                exercises_total += 1
                if not isinstance(exercise, dict):
                    continue
                exercise_keys = {key.lower() for key in exercise.keys()}
                has_non_header_keys = bool(exercise_keys - _HEADER_EXERCISE_KEYS)
                has_detail_hint = bool(exercise_keys & _DETAIL_HINT_KEYS)
                if has_non_header_keys or has_detail_hint:
                    exercises_with_detail += 1

    top_level_detail_keys = sorted(
        key for key in payload.keys() if key not in _PLAN_METADATA_KEYS
    )
    if exercises_with_detail > 0 or top_level_detail_keys:
        detail_level = "structured"
    elif sessions_total > 0:
        detail_level = "header_only"
    else:
        detail_level = "none"

    return {
        "detail_level": detail_level,
        "detail_available": detail_level != "none",
        "sessions_total": sessions_total,
        "exercises_total": exercises_total,
        "exercises_with_prescription_detail": exercises_with_detail,
        "top_level_detail_keys": top_level_detail_keys,
    }


def _as_optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_target_rir(value: Any) -> float | None:
    parsed = _as_optional_float(value)
    if parsed is None:
        return None
    if parsed < 0:
        return 0.0
    if parsed > 10:
        return 10.0
    return round(parsed, 2)


def _infer_target_rir_from_target_rpe(exercise: dict[str, Any]) -> float | None:
    parsed_rpe = _as_optional_float(exercise.get("target_rpe"))
    if parsed_rpe is None:
        parsed_rpe = _as_optional_float(exercise.get("rpe"))
    if parsed_rpe is None:
        return None
    return _normalize_target_rir(10.0 - parsed_rpe)


def _normalize_plan_sessions_with_rir(sessions: Any) -> list[Any]:
    if not isinstance(sessions, list):
        return []

    normalized_sessions: list[Any] = []
    for session in sessions:
        if not isinstance(session, dict):
            normalized_sessions.append(session)
            continue

        normalized_session = dict(session)
        exercises = session.get("exercises")
        if not isinstance(exercises, list):
            normalized_sessions.append(normalized_session)
            continue

        normalized_exercises: list[Any] = []
        for exercise in exercises:
            if not isinstance(exercise, dict):
                normalized_exercises.append(exercise)
                continue

            normalized_exercise = dict(exercise)
            explicit_rir = _normalize_target_rir(
                exercise.get("target_rir", exercise.get("rir"))
            )
            if explicit_rir is not None:
                normalized_exercise["target_rir"] = explicit_rir
            else:
                inferred_rir = _infer_target_rir_from_target_rpe(exercise)
                if inferred_rir is not None:
                    normalized_exercise["target_rir"] = inferred_rir
                    normalized_exercise["target_rir_source"] = "inferred_from_target_rpe"

            normalized_exercises.append(normalized_exercise)

        normalized_session["exercises"] = normalized_exercises
        normalized_sessions.append(normalized_session)

    return normalized_sessions


def _compute_rir_target_summary(sessions: Any) -> dict[str, Any]:
    if not isinstance(sessions, list):
        return {
            "exercises_total": 0,
            "exercises_with_target_rir": 0,
            "inferred_target_rir": 0,
        }

    exercises_total = 0
    exercises_with_target_rir = 0
    inferred_target_rir = 0
    total_target_rir = 0.0

    for session in sessions:
        if not isinstance(session, dict):
            continue
        exercises = session.get("exercises")
        if not isinstance(exercises, list):
            continue
        for exercise in exercises:
            if not isinstance(exercise, dict):
                continue
            exercises_total += 1
            target_rir = _normalize_target_rir(exercise.get("target_rir"))
            if target_rir is None:
                continue
            exercises_with_target_rir += 1
            total_target_rir += target_rir
            if exercise.get("target_rir_source") == "inferred_from_target_rpe":
                inferred_target_rir += 1

    result = {
        "exercises_total": exercises_total,
        "exercises_with_target_rir": exercises_with_target_rir,
        "inferred_target_rir": inferred_target_rir,
    }
    if exercises_with_target_rir:
        result["average_target_rir"] = round(
            total_target_rir / exercises_with_target_rir, 2
        )
    return result


def _normalized_plan_id(value: Any) -> str:
    if isinstance(value, str):
        plan_id = value.strip()
        if plan_id:
            return plan_id
    return "default"


def _sanitize_plan_id_for_name(plan_id: str) -> str:
    sanitized = "".join(
        char for char in plan_id.lower() if char.isalnum() or char in {"-", "_"}
    ).strip("-_")
    return sanitized[:24] or "default"


def _resolve_plan_name(value: Any, *, plan_id: str, timestamp: datetime) -> str:
    if isinstance(value, str):
        name = value.strip()
        if name:
            return name
    return f"plan-{timestamp.date().isoformat()}-{_sanitize_plan_id_for_name(plan_id)}"


def _resolve_optional_plan_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    return name or None


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract summary for user_profile manifest."""
    if not projection_rows:
        return {}
    overview_row = next(
        (row for row in projection_rows if row.get("key") == TRAINING_PLAN_OVERVIEW_KEY),
        projection_rows[0],
    )
    data = overview_row["data"]
    result: dict[str, Any] = {}
    active = data.get("active_plan")
    if active:
        result["has_active_plan"] = True
        result["plan_name"] = active.get("name", "unnamed")
        sessions = active.get("sessions", [])
        result["sessions_per_week"] = len(sessions)
    else:
        result["has_active_plan"] = False
    result["total_plans"] = data.get("total_plans", 0)
    return result


async def _load_training_plan_rows(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    retracted_ids: set[str],
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type IN (
                  'training_plan.created',
                  'training_plan.updated',
                  'training_plan.archived'
              )
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
    return [row for row in rows if str(row["id"]) not in retracted_ids]


async def _delete_training_plan_projections(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            DELETE FROM projections
            WHERE user_id = %s
              AND projection_type = 'training_plan'
              AND key IN (%s, %s)
            """,
            (user_id, TRAINING_PLAN_OVERVIEW_KEY, TRAINING_PLAN_DETAILS_KEY),
        )


def _select_active_plan(plans: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not plans:
        return None
    sorted_plans = sorted(plans.values(), key=lambda plan: plan["created_at"])
    for plan in sorted_plans[:-1]:
        plan["status"] = "inactive"
    return sorted_plans[-1]


def _replay_training_plan_state(
    rows: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, str]],
    list[dict[str, Any]],
]:
    plans: dict[str, dict[str, Any]] = {}
    plan_payloads: dict[str, dict[str, Any]] = {}
    plan_detail_sources: dict[str, dict[str, str]] = {}
    archived_plans: list[dict[str, Any]] = []

    for row in rows:
        data = row["data"]
        ts = row["timestamp"]
        event_type = row["event_type"]
        plan_id = _normalized_plan_id(data.get("plan_id"))

        if event_type == "training_plan.created":
            normalized_sessions = _normalize_plan_sessions_with_rir(data.get("sessions", []))
            plans[plan_id] = {
                "plan_id": plan_id,
                "name": _resolve_plan_name(data.get("name"), plan_id=plan_id, timestamp=ts),
                "created_at": ts.isoformat(),
                "updated_at": ts.isoformat(),
                "status": "active",
                "sessions": normalized_sessions,
                "cycle_weeks": data.get("cycle_weeks"),
                "notes": data.get("notes"),
                "rir_targets": _compute_rir_target_summary(normalized_sessions),
            }
            plan_payloads[plan_id] = _merge_plan_payload(None, data)
            plan_detail_sources[plan_id] = _build_event_ref(row)
            continue

        if event_type == "training_plan.updated":
            if plan_id not in plans:
                continue
            plan = plans[plan_id]
            plan["updated_at"] = ts.isoformat()
            if "name" in data:
                normalized_name = _resolve_optional_plan_name(data.get("name"))
                if normalized_name is not None:
                    plan["name"] = normalized_name
            if "sessions" in data:
                normalized_sessions = _normalize_plan_sessions_with_rir(data["sessions"])
                plan["sessions"] = normalized_sessions
                plan["rir_targets"] = _compute_rir_target_summary(normalized_sessions)
            if "cycle_weeks" in data:
                plan["cycle_weeks"] = data["cycle_weeks"]
            if "notes" in data:
                plan["notes"] = data["notes"]
            plan_payloads[plan_id] = _merge_plan_payload(plan_payloads.get(plan_id), data)
            if _event_contains_plan_detail_delta(data):
                plan_detail_sources[plan_id] = _build_event_ref(row)
            continue

        if event_type == "training_plan.archived" and plan_id in plans:
            plan = plans.pop(plan_id)
            plan["status"] = "archived"
            plan["archived_at"] = ts.isoformat()
            if "reason" in data:
                plan["archive_reason"] = data["reason"]
            archived_plans.append(plan)
            plan_payloads.pop(plan_id, None)
            plan_detail_sources.pop(plan_id, None)

    return plans, plan_payloads, plan_detail_sources, archived_plans


def _build_training_plan_projection_payloads(
    plans: dict[str, dict[str, Any]],
    plan_payloads: dict[str, dict[str, Any]],
    plan_detail_sources: dict[str, dict[str, str]],
    archived_plans: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    active_plan = _select_active_plan(plans)
    active_plan_id = active_plan["plan_id"] if active_plan else None
    active_payload = (
        copy.deepcopy(plan_payloads.get(active_plan_id, {})) if active_plan_id else {}
    )
    detail_signals = _compute_plan_detail_signals(active_payload)
    source_event = (
        copy.deepcopy(plan_detail_sources.get(active_plan_id)) if active_plan_id else None
    )
    detail_locator = {
        "schema_version": TRAINING_PLAN_DETAIL_LOCATOR_SCHEMA_VERSION,
        "projection_type": "training_plan",
        "projection_key": TRAINING_PLAN_DETAILS_KEY,
        "detail_level": detail_signals["detail_level"],
        "detail_available": detail_signals["detail_available"],
        "source_event": source_event,
    }

    if active_plan is not None:
        active_plan["detail_presence"] = copy.deepcopy(detail_signals)

    overview_payload: dict[str, Any] = {
        "active_plan": active_plan,
        "total_plans": len(plans) + len(archived_plans),
        "plan_history": archived_plans[-5:],
        "detail_locator": detail_locator,
    }
    details_payload: dict[str, Any] = {
        "schema_version": TRAINING_PLAN_DETAILS_SCHEMA_VERSION,
        "active_plan_id": active_plan_id,
        "plan_name": active_plan.get("name") if active_plan else None,
        "detail_level": detail_signals["detail_level"],
        "detail_available": detail_signals["detail_available"],
        "detail_signals": detail_signals,
        "source_event": source_event,
        "plan_payload": active_payload if active_plan else None,
    }
    plan_name = active_plan["name"] if active_plan else "none"
    return overview_payload, details_payload, plan_name


async def _upsert_training_plan_projection(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    *,
    key: str,
    data: dict[str, Any],
    last_event_id: str,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'training_plan', %s, %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, key, json.dumps(data), last_event_id),
        )


@projection_handler(
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    dimension_meta={
        "name": "training_plan",
        "description": "Prescribed training: what should happen when",
        "key_structure": "overview + details per user",
        "projection_key": "overview|details",
        "granularity": ["session", "week", "cycle"],
        "relates_to": {
            "training_timeline": {"join": "day", "why": "planned vs actual training"},
            "exercise_progression": {"join": "exercise_id", "why": "load prescription context"},
        },
        "context_seeds": [
            "training_goals",
            "available_days",
            "program_preference",
        ],
        "output_schema": {
            "active_plan": {
                "plan_id": "string",
                "name": "string",
                "created_at": "ISO 8601 datetime",
                "updated_at": "ISO 8601 datetime",
                "status": "string — active|inactive",
                "sessions": [{
                    "name": "string",
                    "exercises": [{
                        "exercise_id": "string (optional)",
                        "target_rir": "number (optional, 0..10)",
                        "target_rir_source": "string (optional: inferred_from_target_rpe)",
                    }],
                }],
                "cycle_weeks": "integer or null",
                "notes": "string or null",
                "rir_targets": {
                    "exercises_total": "integer",
                    "exercises_with_target_rir": "integer",
                    "inferred_target_rir": "integer",
                    "average_target_rir": "number (optional)",
                },
            },
            "total_plans": "integer — active + archived",
            "plan_history": [{
                "plan_id": "string",
                "name": "string",
                "status": "string — archived",
                "created_at": "ISO 8601 datetime",
                "archived_at": "ISO 8601 datetime",
                "archive_reason": "string (optional)",
            }],
            "detail_locator": {
                "schema_version": "training_plan.detail_locator.v1",
                "projection_type": "training_plan",
                "projection_key": "details",
                "detail_level": "string — none|header_only|structured",
                "detail_available": "boolean",
                "source_event": {
                    "event_id": "UUID string",
                    "event_type": "string",
                    "timestamp": "ISO 8601 datetime",
                },
            },
        },
        "manifest_contribution": _manifest_contribution,
    },
)
async def update_training_plan(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of training_plan projection."""
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)

    rows = await _load_training_plan_rows(conn, user_id, retracted_ids)

    if not rows:
        await _delete_training_plan_projections(conn, user_id)
        return

    last_event_id = str(rows[-1]["id"])
    plans, plan_payloads, plan_detail_sources, archived_plans = (
        _replay_training_plan_state(rows)
    )
    projection_data, details_projection_data, plan_name = (
        _build_training_plan_projection_payloads(
            plans,
            plan_payloads,
            plan_detail_sources,
            archived_plans,
        )
    )

    await _upsert_training_plan_projection(
        conn,
        user_id,
        key=TRAINING_PLAN_OVERVIEW_KEY,
        data=projection_data,
        last_event_id=last_event_id,
    )
    await _upsert_training_plan_projection(
        conn,
        user_id,
        key=TRAINING_PLAN_DETAILS_KEY,
        data=details_projection_data,
        last_event_id=last_event_id,
    )

    logger.info(
        "Updated training_plan for user=%s (active=%s, total=%d)",
        user_id,
        plan_name,
        projection_data["total_plans"],
    )
