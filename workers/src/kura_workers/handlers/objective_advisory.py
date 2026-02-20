"""Objective advisory projection (warning-only)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..utils import get_retracted_event_ids

OBJECTIVE_ADVISORY_SCHEMA_VERSION = "objective_advisory.v1"
OBJECTIVE_ADVISORY_POLICY_ROLE = "advisory_only"
_METRIC_STALENESS_DAYS = 21
_OVERRIDE_RECENT_DAYS = 30
_OVERRIDE_RECENT_LIMIT = 25


def _as_confidence(value: Any, *, fallback: float = 0.5) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return round(max(0.0, min(1.0, parsed)), 2)


def _as_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _has_trackable_target(active_objective: dict[str, Any]) -> bool:
    primary_goal = active_objective.get("primary_goal")
    if not isinstance(primary_goal, dict):
        return False
    for field in ("target", "target_metric", "target_exercise", "value", "unit"):
        if primary_goal.get(field) not in (None, "", []):
            return True
    return False


def _warning(
    *,
    code: str,
    severity: str,
    confidence: float,
    message: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "confidence": _as_confidence(confidence),
        "message": message,
        "evidence": evidence,
        "overridable": True,
    }


@projection_handler(
    "goal.set",
    "objective.set",
    "objective.updated",
    "objective.archived",
    "advisory.override.recorded",
    "set.logged",
    "session.logged",
    "external.activity_imported",
    "profile.updated",
    "training_plan.created",
    "training_plan.updated",
    dimension_meta={
        "name": "objective_advisory",
        "description": (
            "Objective-consistency warnings, trackability gaps, and override-rationale "
            "summary for advisory-only planning."
        ),
        "key_structure": "single overview per user",
        "projection_key": "overview",
        "granularity": ["objective", "advisory", "override"],
        "output_schema": {
            "schema_version": "objective_advisory.v1",
            "policy_role": "advisory_only",
            "objective_snapshot": "object",
            "trackability": "object",
            "warnings": "list[object]",
            "warning_count": "integer",
            "override_summary": "object",
            "safety_invariants_non_overridable": "list[string]",
            "generated_at": "ISO 8601 datetime",
        },
    },
)
async def update_objective_advisory(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    user_id = str(payload["user_id"])
    retracted_ids = await get_retracted_event_ids(conn, user_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data
            FROM projections
            WHERE user_id = %s
              AND projection_type = 'objective_state'
              AND key = 'active'
            LIMIT 1
            """,
            (user_id,),
        )
        objective_row = await cur.fetchone()

    if not objective_row or not isinstance(objective_row.get("data"), dict):
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM projections
                WHERE user_id = %s
                  AND projection_type = 'objective_advisory'
                  AND key = 'overview'
                """,
                (user_id,),
            )
        return

    objective_data = dict(objective_row["data"])
    active_objective = objective_data.get("active_objective")
    if not isinstance(active_objective, dict):
        active_objective = {}

    now = datetime.now(UTC)
    recent_threshold = now - timedelta(days=_OVERRIDE_RECENT_DAYS)
    stale_threshold = now - timedelta(days=_METRIC_STALENESS_DAYS)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type IN (
                  'advisory.override.recorded',
                  'set.logged',
                  'session.logged',
                  'external.activity_imported'
              )
            ORDER BY timestamp DESC, id DESC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    rows = [row for row in rows if str(row.get("id") or "") not in retracted_ids]
    override_rows = [row for row in rows if row.get("event_type") == "advisory.override.recorded"]
    activity_rows = [row for row in rows if row.get("event_type") != "advisory.override.recorded"]

    success_metrics = active_objective.get("success_metrics")
    success_metrics_list = success_metrics if isinstance(success_metrics, list) else []
    has_trackable_target = _has_trackable_target(active_objective)

    warnings: list[dict[str, Any]] = []
    trackability_gaps: list[str] = []
    if not success_metrics_list:
        trackability_gaps.append("missing_success_metrics")
    if not has_trackable_target:
        trackability_gaps.append("missing_primary_goal_target")

    if trackability_gaps:
        warnings.append(
            _warning(
                code="objective_trackability_gap",
                severity="warning",
                confidence=0.84,
                message=(
                    "Objective lacks an observable tracking path. Add explicit metrics or a "
                    "target so progress can be evaluated."
                ),
                evidence={
                    "gaps": trackability_gaps,
                    "success_metrics_count": len(success_metrics_list),
                    "has_primary_goal_target": has_trackable_target,
                },
            )
        )

    objective_source = str(active_objective.get("source") or "unknown").strip().lower()
    objective_confidence = _as_confidence(active_objective.get("confidence"), fallback=0.45)
    if objective_source in {"default_inferred", "legacy_inferred"} or objective_confidence < 0.55:
        warnings.append(
            _warning(
                code="objective_default_inferred",
                severity="info",
                confidence=0.7,
                message=(
                    "Active objective is inferred with limited confidence. Confirm or refine it "
                    "to improve planning precision."
                ),
                evidence={
                    "source": objective_source or "unknown",
                    "objective_confidence": objective_confidence,
                },
            )
        )

    last_activity_at: datetime | None = None
    for row in activity_rows:
        ts = row.get("timestamp")
        if isinstance(ts, datetime):
            ts = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
            last_activity_at = ts
            break
    if last_activity_at is None or last_activity_at < stale_threshold:
        warnings.append(
            _warning(
                code="objective_metric_staleness",
                severity="warning",
                confidence=0.76,
                message=(
                    "Objective metrics appear stale. Recent activity is missing or too old to "
                    "support reliable objective feedback."
                ),
                evidence={
                    "last_activity_at": (
                        last_activity_at.isoformat() if last_activity_at else None
                    ),
                    "staleness_days_threshold": _METRIC_STALENESS_DAYS,
                },
            )
        )

    by_actor: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    recent_overrides: list[dict[str, Any]] = []
    review_due_count = 0

    for row in override_rows:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        actor = str(data.get("actor") or "unknown").strip().lower() or "unknown"
        scope = str(data.get("scope") or "unknown").strip().lower() or "unknown"
        by_actor[actor] = by_actor.get(actor, 0) + 1
        by_scope[scope] = by_scope.get(scope, 0) + 1

        review_point = _as_date(data.get("review_point"))
        if review_point is not None and review_point < now:
            review_due_count += 1

        ts = row.get("timestamp")
        ts_value: datetime | None = None
        if isinstance(ts, datetime):
            ts_value = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
        if (
            ts_value is not None
            and ts_value >= recent_threshold
            and len(recent_overrides) < _OVERRIDE_RECENT_LIMIT
        ):
            recent_overrides.append(
                {
                    "timestamp": ts_value.isoformat(),
                    "reason": str(data.get("reason") or "").strip(),
                    "scope": scope,
                    "actor": actor,
                    "expected_outcome": str(data.get("expected_outcome") or "").strip() or None,
                    "review_point": (
                        review_point.isoformat() if review_point is not None else None
                    ),
                }
            )

    if review_due_count > 0:
        warnings.append(
            _warning(
                code="objective_override_review_due",
                severity="info",
                confidence=0.78,
                message=(
                    "One or more override rationales passed their review point. Re-check whether "
                    "the override assumption still holds."
                ),
                evidence={
                    "review_due_count": review_due_count,
                },
            )
        )

    trackability_status = "tracked"
    if trackability_gaps:
        trackability_status = "gap"

    projection_data = {
        "schema_version": OBJECTIVE_ADVISORY_SCHEMA_VERSION,
        "policy_role": OBJECTIVE_ADVISORY_POLICY_ROLE,
        "objective_snapshot": {
            "objective_id": active_objective.get("objective_id"),
            "mode": active_objective.get("mode"),
            "source": active_objective.get("source"),
            "confidence": objective_confidence,
            "primary_goal": active_objective.get("primary_goal"),
            "secondary_goals_count": len(active_objective.get("secondary_goals") or []),
            "anti_goals_count": len(active_objective.get("anti_goals") or []),
        },
        "trackability": {
            "status": trackability_status,
            "gaps": trackability_gaps,
            "success_metrics_count": len(success_metrics_list),
            "has_primary_goal_target": has_trackable_target,
            "last_activity_at": last_activity_at.isoformat() if last_activity_at else None,
        },
        "warnings": warnings,
        "warning_count": len(warnings),
        "override_summary": {
            "total_count": len(override_rows),
            "recent_days": _OVERRIDE_RECENT_DAYS,
            "recent_count": len(recent_overrides),
            "review_due_count": review_due_count,
            "by_actor": by_actor,
            "by_scope": by_scope,
            "recent": recent_overrides,
        },
        "safety_invariants_non_overridable": [
            "consent_write_gate",
            "approval_required_high_impact_write",
        ],
        "generated_at": now.isoformat(),
    }

    last_event_id = str(payload.get("event_id") or "")
    if not last_event_id and rows:
        last_event_id = str(rows[0]["id"])
    if not last_event_id:
        last_event_id = "objective_advisory.synthetic"

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (
                user_id, projection_type, key, data, version, last_event_id, updated_at
            )
            VALUES (%s, 'objective_advisory', 'overview', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), last_event_id),
        )

