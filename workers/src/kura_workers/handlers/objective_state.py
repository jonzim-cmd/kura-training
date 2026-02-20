"""Objective-state projection handler.

Builds the active objective context from explicit objective events and
legacy goal.set events.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..utils import get_retracted_event_ids

OBJECTIVE_STATE_SCHEMA_VERSION = "objective_state.v1"
OBJECTIVE_STATE_MODEL_VERSION = "objective_state_model.v1"
OBJECTIVE_MODES = {"journal", "collaborate", "coach"}


def _normalize_mode(value: Any, *, fallback: str = "collaborate") -> str:
    mode = str(value or "").strip().lower()
    if mode in OBJECTIVE_MODES:
        return mode
    return fallback


def _normalize_confidence(value: Any, *, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return round(max(0.0, min(1.0, parsed)), 2)


def _normalized_goal_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(dict(item))
    return out


def _normalized_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _objective_from_legacy_goal(
    goal_data: dict[str, Any],
    *,
    objective_id: str = "legacy.goal.latest",
) -> dict[str, Any]:
    goal_type = str(goal_data.get("goal_type") or "health").strip().lower() or "health"
    description = str(goal_data.get("description") or "").strip()
    target_exercise = str(goal_data.get("target_exercise") or "").strip().lower()

    primary_goal: dict[str, Any] = {
        "type": goal_type,
    }
    if description:
        primary_goal["description"] = description
    if target_exercise:
        primary_goal["target_exercise"] = target_exercise
    if goal_data.get("target_1rm_kg") is not None:
        primary_goal["target_1rm_kg"] = goal_data.get("target_1rm_kg")
    if goal_data.get("timeframe_weeks") is not None:
        primary_goal["timeframe_weeks"] = goal_data.get("timeframe_weeks")

    return {
        "objective_id": objective_id,
        "mode": "collaborate",
        "primary_goal": primary_goal,
        "secondary_goals": [],
        "anti_goals": [],
        "success_metrics": [],
        "constraint_markers": [],
        "hypothesis": None,
        "source": "legacy_goal_set",
        "confidence": 0.7,
        "archived": False,
        "version": 1,
    }


def _default_objective_from_profile(profile_data: dict[str, Any]) -> dict[str, Any]:
    training_modality = str(profile_data.get("training_modality") or "").strip().lower()
    primary_goal_type = "general_health"
    if training_modality in {"strength", "bodybuilding"}:
        primary_goal_type = "strength_base"
    elif training_modality in {"running", "cycling", "rowing", "swimming", "endurance"}:
        primary_goal_type = "endurance_base"

    return {
        "objective_id": "default.objective",
        "mode": "journal",
        "primary_goal": {
            "type": primary_goal_type,
            "description": "Default objective inferred while user objective is still open.",
        },
        "secondary_goals": [],
        "anti_goals": [],
        "success_metrics": [{"metric": "consistency_score"}],
        "constraint_markers": [],
        "hypothesis": None,
        "source": "default_inferred",
        "confidence": 0.45,
        "archived": False,
        "version": 1,
    }


def _normalize_objective_payload(
    *,
    objective_id: str,
    data: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prev = dict(previous or {})
    mode_fallback = str(prev.get("mode") or "collaborate")
    confidence_fallback = float(prev.get("confidence") or 0.65)

    primary_goal = data.get("primary_goal")
    if isinstance(primary_goal, dict):
        resolved_primary_goal = dict(primary_goal)
    else:
        resolved_primary_goal = dict(prev.get("primary_goal") or {})

    if not resolved_primary_goal:
        resolved_primary_goal = {"type": "unspecified"}

    return {
        "objective_id": objective_id,
        "mode": _normalize_mode(data.get("mode"), fallback=mode_fallback),
        "primary_goal": resolved_primary_goal,
        "secondary_goals": _normalized_goal_list(
            data.get("secondary_goals", prev.get("secondary_goals"))
        ),
        "anti_goals": _normalized_string_list(
            data.get("anti_goals", prev.get("anti_goals"))
        ),
        "success_metrics": _normalized_goal_list(
            data.get("success_metrics", prev.get("success_metrics"))
        ),
        "constraint_markers": _normalized_string_list(
            data.get("constraint_markers", prev.get("constraint_markers"))
        ),
        "hypothesis": data.get("hypothesis", prev.get("hypothesis")),
        "source": str(
            data.get("source")
            or prev.get("source")
            or "user_explicit"
        ).strip()
        or "user_explicit",
        "confidence": _normalize_confidence(
            data.get("confidence"),
            fallback=confidence_fallback,
        ),
        "archived": False,
        "version": int(prev.get("version") or 0) + 1,
    }


def _objective_sort_key(objective: dict[str, Any]) -> tuple[datetime, str]:
    raw = objective.get("updated_at")
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.min
    else:
        dt = datetime.min
    return dt, str(objective.get("objective_id") or "")


def _unresolved_fields(active_objective: dict[str, Any] | None) -> list[str]:
    if not isinstance(active_objective, dict):
        return ["active_objective"]

    unresolved: list[str] = []
    primary_goal = active_objective.get("primary_goal")
    if not isinstance(primary_goal, dict) or not primary_goal:
        unresolved.append("primary_goal")

    for field in ("success_metrics", "constraint_markers"):
        value = active_objective.get(field)
        if not isinstance(value, list):
            unresolved.append(field)
    return unresolved


async def _load_profile_data_from_projection(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data
            FROM projections
            WHERE user_id = %s
              AND projection_type = 'user_profile'
              AND key = 'me'
            LIMIT 1
            """,
            (user_id,),
        )
        row = await cur.fetchone()

    if row is None or not isinstance(row.get("data"), dict):
        return {}

    user = row["data"].get("user")
    if not isinstance(user, dict):
        return {}
    profile = user.get("profile")
    if not isinstance(profile, dict):
        return {}
    return dict(profile)


async def _upsert_objective_state_projection(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_data: dict[str, Any],
    last_event_id: str | None,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'objective_state', 'active', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), last_event_id),
        )


@projection_handler(
    "goal.set",
    "objective.set",
    "objective.updated",
    "objective.archived",
    "profile.updated",
    "advisory.override.recorded",
    dimension_meta={
        "name": "objective_state",
        "description": (
            "Active objective context from explicit objective events, legacy goal events, "
            "and transparent defaults."
        ),
        "key_structure": "single overview per user",
        "projection_key": "active",
        "granularity": ["event_replay", "objective_state"],
        "output_schema": {
            "schema_version": "objective_state.v1",
            "active_objective": "object|null",
            "objective_history": "list[object]",
            "active_constraints": "list[string]",
            "unresolved_fields": "list[string]",
            "inferred_confidence": "number [0,1]",
            "source_summary": {
                "explicit_objective_events": "integer",
                "legacy_goal_events": "integer",
                "override_rationale_events": "integer",
                "default_inferred": "boolean",
            },
            "model_version": "string",
        },
    },
)
async def update_objective_state(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type IN (
                  'goal.set',
                  'objective.set',
                  'objective.updated',
                  'objective.archived',
                  'profile.updated',
                  'advisory.override.recorded'
              )
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    rows = [row for row in rows if str(row.get("id") or "") not in retracted_ids]
    if not rows:
        profile_data = await _load_profile_data_from_projection(conn, user_id)
        active_objective = _default_objective_from_profile(profile_data)
        inferred_confidence = _normalize_confidence(
            active_objective.get("confidence"),
            fallback=0.45,
        )
        active_objective["confidence"] = inferred_confidence

        projection_data = {
            "schema_version": OBJECTIVE_STATE_SCHEMA_VERSION,
            "active_objective": active_objective,
            "objective_history": [],
            "active_constraints": list(active_objective.get("constraint_markers") or []),
            "unresolved_fields": _unresolved_fields(active_objective),
            "inferred_confidence": inferred_confidence,
            "source_summary": {
                "explicit_objective_events": 0,
                "legacy_goal_events": 0,
                "override_rationale_events": 0,
                "default_inferred": True,
            },
            "model_version": OBJECTIVE_STATE_MODEL_VERSION,
        }
        await _upsert_objective_state_projection(
            conn,
            user_id=str(user_id),
            projection_data=projection_data,
            last_event_id=None,
        )
        return

    objectives: dict[str, dict[str, Any]] = {}
    objective_history: list[dict[str, Any]] = []
    legacy_goals: list[dict[str, Any]] = []
    profile_data: dict[str, Any] = {}
    explicit_objective_events = 0
    override_rationale_events = 0

    for row in rows:
        event_type = str(row.get("event_type") or "")
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        ts = row["timestamp"].isoformat()

        if event_type == "profile.updated":
            for key, value in data.items():
                profile_data[key] = value
            continue

        if event_type == "advisory.override.recorded":
            override_rationale_events += 1
            continue

        if event_type == "goal.set":
            legacy_goal = _objective_from_legacy_goal(data)
            legacy_goal["updated_at"] = ts
            legacy_goals.append(legacy_goal)
            objective_history.append(
                {
                    "event_type": event_type,
                    "objective_id": legacy_goal["objective_id"],
                    "timestamp": ts,
                    "source": "legacy_goal_set",
                }
            )
            continue

        if event_type in {"objective.set", "objective.updated"}:
            explicit_objective_events += 1
            objective_id = str(data.get("objective_id") or "").strip() or "objective.default"
            previous = objectives.get(objective_id)
            objective = _normalize_objective_payload(
                objective_id=objective_id,
                data=data,
                previous=previous,
            )
            objective["updated_at"] = ts
            objectives[objective_id] = objective
            objective_history.append(
                {
                    "event_type": event_type,
                    "objective_id": objective_id,
                    "timestamp": ts,
                    "source": objective.get("source"),
                    "mode": objective.get("mode"),
                }
            )
            continue

        if event_type == "objective.archived":
            objective_id = str(data.get("objective_id") or "").strip() or "objective.default"
            existing = objectives.get(objective_id) or {
                "objective_id": objective_id,
                "mode": "collaborate",
                "primary_goal": {"type": "unspecified"},
                "secondary_goals": [],
                "anti_goals": [],
                "success_metrics": [],
                "constraint_markers": [],
                "hypothesis": None,
                "source": "user_explicit",
                "confidence": 0.6,
                "version": 1,
            }
            existing["archived"] = True
            existing["updated_at"] = ts
            objectives[objective_id] = existing
            objective_history.append(
                {
                    "event_type": event_type,
                    "objective_id": objective_id,
                    "timestamp": ts,
                    "source": existing.get("source"),
                }
            )

    explicit_candidates = [
        objective
        for objective in objectives.values()
        if not bool(objective.get("archived"))
    ]
    explicit_candidates.sort(key=_objective_sort_key)

    active_objective: dict[str, Any]
    if explicit_candidates:
        active_objective = dict(explicit_candidates[-1])
    elif legacy_goals:
        active_objective = dict(legacy_goals[-1])
    else:
        active_objective = _default_objective_from_profile(profile_data)

    inferred_confidence = _normalize_confidence(
        active_objective.get("confidence"),
        fallback=0.45,
    )
    active_objective["confidence"] = inferred_confidence

    unresolved_fields = _unresolved_fields(active_objective)
    source_summary = {
        "explicit_objective_events": explicit_objective_events,
        "legacy_goal_events": len(legacy_goals),
        "override_rationale_events": override_rationale_events,
        "default_inferred": str(active_objective.get("source")) == "default_inferred",
    }
    projection_data = {
        "schema_version": OBJECTIVE_STATE_SCHEMA_VERSION,
        "active_objective": active_objective,
        "objective_history": objective_history[-120:],
        "active_constraints": list(active_objective.get("constraint_markers") or []),
        "unresolved_fields": unresolved_fields,
        "inferred_confidence": inferred_confidence,
        "source_summary": source_summary,
        "model_version": OBJECTIVE_STATE_MODEL_VERSION,
    }
    last_event_id = str(rows[-1]["id"])
    await _upsert_objective_state_projection(
        conn,
        user_id=str(user_id),
        projection_data=projection_data,
        last_event_id=last_event_id,
    )
