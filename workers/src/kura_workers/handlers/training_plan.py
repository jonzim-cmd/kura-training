"""Training Plan dimension handler.

The only PRESCRIPTIVE dimension — describes what SHOULD happen,
not what DID happen. All other dimensions are descriptive.

Reacts to training_plan.created, training_plan.updated, training_plan.archived.
Computes the active plan, weekly template, and plan history.

Plan structure: weekly template with named sessions. The agent derives
concrete loads from exercise_progression at conversation time.

Full recompute on every event — idempotent by design.
"""

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..utils import get_retracted_event_ids

logger = logging.getLogger(__name__)


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract summary for user_profile manifest."""
    if not projection_rows:
        return {}
    data = projection_rows[0]["data"]
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


@projection_handler(
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    dimension_meta={
        "name": "training_plan",
        "description": "Prescribed training: what should happen when",
        "key_structure": "single overview per user",
        "projection_key": "overview",
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
                "sessions": [{"name": "string", "exercises": ["object — plan-specific"]}],
                "cycle_weeks": "integer or null",
                "notes": "string or null",
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

    # Filter retracted events
    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    if not rows:
        # Clean up: delete any existing projection (all events retracted)
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM projections WHERE user_id = %s AND projection_type = 'training_plan' AND key = 'overview'",
                (user_id,),
            )
        return

    last_event_id = rows[-1]["id"]

    # Replay plan events to reconstruct current state
    # Plans are identified by plan_id. Latest created plan becomes active.
    plans: dict[str, dict[str, Any]] = {}
    archived_plans: list[dict[str, Any]] = []

    for row in rows:
        data = row["data"]
        ts = row["timestamp"]
        event_type = row["event_type"]
        plan_id = data.get("plan_id", "default")

        if event_type == "training_plan.created":
            plans[plan_id] = {
                "plan_id": plan_id,
                "name": data.get("name", "unnamed"),
                "created_at": ts.isoformat(),
                "updated_at": ts.isoformat(),
                "status": "active",
                "sessions": data.get("sessions", []),
                "cycle_weeks": data.get("cycle_weeks"),
                "notes": data.get("notes"),
            }

        elif event_type == "training_plan.updated":
            if plan_id in plans:
                plan = plans[plan_id]
                plan["updated_at"] = ts.isoformat()
                # Delta merge: update provided fields
                if "name" in data:
                    plan["name"] = data["name"]
                if "sessions" in data:
                    plan["sessions"] = data["sessions"]
                if "cycle_weeks" in data:
                    plan["cycle_weeks"] = data["cycle_weeks"]
                if "notes" in data:
                    plan["notes"] = data["notes"]

        elif event_type == "training_plan.archived":
            if plan_id in plans:
                plan = plans.pop(plan_id)
                plan["status"] = "archived"
                plan["archived_at"] = ts.isoformat()
                if "reason" in data:
                    plan["archive_reason"] = data["reason"]
                archived_plans.append(plan)

    # The most recently created non-archived plan is active
    active_plan = None
    if plans:
        # Sort by created_at, take the latest
        sorted_plans = sorted(plans.values(), key=lambda p: p["created_at"])
        # Mark all as inactive except the latest
        for plan in sorted_plans[:-1]:
            plan["status"] = "inactive"
        active_plan = sorted_plans[-1]

    projection_data: dict[str, Any] = {
        "active_plan": active_plan,
        "total_plans": len(plans) + len(archived_plans),
        "plan_history": archived_plans[-5:],  # Last 5 archived plans
    }

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'training_plan', 'overview', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), str(last_event_id)),
        )

    plan_name = active_plan["name"] if active_plan else "none"
    logger.info(
        "Updated training_plan for user=%s (active=%s, total=%d)",
        user_id, plan_name, projection_data["total_plans"],
    )
