"""Quality Health projection handler (Decision 13, Phase 0).

Read-only invariant evaluation over user events. This phase detects and
surfaces issues but does not auto-repair or mutate canonical events.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..utils import get_alias_map, get_retracted_event_ids

logger = logging.getLogger(__name__)

_EVENT_TYPES = (
    "set.logged",
    "exercise.alias_created",
    "preference.set",
    "profile.updated",
    "goal.set",
    "bodyweight.logged",
    "projection_rule.created",
    "projection_rule.archived",
)

_SEVERITY_WEIGHT = {
    "high": 0.25,
    "medium": 0.12,
    "low": 0.05,
}

_SEVERITY_ORDER = {
    "high": 0,
    "medium": 1,
    "low": 2,
    "info": 3,
}


def _issue(
    invariant_id: str,
    issue_type: str,
    severity: str,
    detail: str,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "issue_id": f"{invariant_id}:{issue_type}",
        "invariant_id": invariant_id,
        "type": issue_type,
        "severity": severity,
        "detail": detail,
        "metrics": metrics or {},
    }


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _latest_preferences(event_rows: list[dict[str, Any]]) -> dict[str, Any]:
    prefs: dict[str, Any] = {}
    for row in event_rows:
        if row["event_type"] != "preference.set":
            continue
        data = row.get("data") or {}
        key = str(data.get("key", "")).strip()
        if key:
            prefs[key] = data.get("value")
    return prefs


def _latest_profile(event_rows: list[dict[str, Any]]) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    for row in event_rows:
        if row["event_type"] != "profile.updated":
            continue
        data = row.get("data") or {}
        if isinstance(data, dict):
            for key, value in data.items():
                profile[key] = value
    return profile


def _active_custom_rule_names(event_rows: list[dict[str, Any]]) -> set[str]:
    active: set[str] = set()
    for row in event_rows:
        event_type = row["event_type"]
        if event_type not in {"projection_rule.created", "projection_rule.archived"}:
            continue
        data = row.get("data") or {}
        name = str(data.get("name", "")).strip()
        if not name:
            continue
        if event_type == "projection_rule.created":
            active.add(name)
        else:
            active.discard(name)
    return active


def _has_jump_goal(goal_data: dict[str, Any]) -> bool:
    goal_type = _normalize(goal_data.get("goal_type"))
    if "jump" in goal_type or "dunk" in goal_type:
        return True

    description = _normalize(goal_data.get("description"))
    return any(term in description for term in ("dunk", "springen", "jump", "cmj"))


def _has_jump_tracking_path(
    set_rows: list[dict[str, Any]],
    active_custom_rules: set[str],
) -> bool:
    jump_exercise_ids = {"countermovement_jump", "box_jump", "jump_squat"}
    for row in set_rows:
        data = row.get("data") or {}
        exercise_id = _normalize(data.get("exercise_id"))
        exercise = _normalize(data.get("exercise"))
        if exercise_id in jump_exercise_ids:
            return True
        if any(term in exercise for term in ("jump", "cmj", "sprung")):
            return True

    return any("jump" in _normalize(rule_name) for rule_name in active_custom_rules)


def _evaluate_read_only_invariants(
    event_rows: list[dict[str, Any]],
    alias_map: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate Phase-0 invariants from event data.

    Decision 13 references:
    - INV-001 (exercise identity resolution)
    - INV-003 (timezone explicitness)
    - INV-005 (goal trackability path)
    - INV-006 (baseline profile explicitness)
    """
    issues: list[dict[str, Any]] = []
    set_rows = [r for r in event_rows if r["event_type"] == "set.logged"]
    goal_rows = [r for r in event_rows if r["event_type"] == "goal.set"]
    prefs = _latest_preferences(event_rows)
    profile = _latest_profile(event_rows)
    active_custom_rules = _active_custom_rule_names(event_rows)

    total_set_logged = len(set_rows)
    unresolved_terms: Counter[str] = Counter()
    for row in set_rows:
        data = row.get("data") or {}
        exercise_id = _normalize(data.get("exercise_id"))
        if exercise_id:
            continue
        exercise = _normalize(data.get("exercise"))
        if not exercise:
            unresolved_terms["<missing_exercise>"] += 1
            continue
        if exercise in alias_map:
            continue
        unresolved_terms[exercise] += 1

    unresolved_set_logged = sum(unresolved_terms.values())
    unresolved_pct = (
        round((unresolved_set_logged / total_set_logged) * 100, 2)
        if total_set_logged
        else 0.0
    )

    if unresolved_set_logged > 0:
        top_terms = [t for t, _ in unresolved_terms.most_common(3)]
        issues.append(
            _issue(
                "INV-001",
                "unresolved_exercise_identity",
                "high",
                (
                    f"{unresolved_set_logged}/{total_set_logged} set.logged events "
                    "lack canonical exercise identity resolution."
                ),
                metrics={
                    "total_set_logged": total_set_logged,
                    "unresolved_set_logged": unresolved_set_logged,
                    "unresolved_pct": unresolved_pct,
                    "top_unresolved_terms": top_terms,
                },
            )
        )

    timezone_pref = prefs.get("timezone") or prefs.get("time_zone")
    if not timezone_pref:
        issues.append(
            _issue(
                "INV-003",
                "timezone_missing",
                "high",
                "No explicit timezone preference found; date/week interpretations may drift.",
            )
        )

    has_age = profile.get("age") is not None or bool(profile.get("date_of_birth"))
    age_deferred = bool(profile.get("age_deferred")) or bool(
        profile.get("date_of_birth_deferred")
    )
    if not has_age and not age_deferred:
        issues.append(
            _issue(
                "INV-006",
                "baseline_age_unknown",
                "medium",
                "Age baseline missing and not explicitly deferred.",
            )
        )

    has_bodyweight_profile = profile.get("bodyweight_kg") is not None
    has_bodyweight_events = any(
        row.get("event_type") == "bodyweight.logged"
        and (row.get("data") or {}).get("weight_kg") is not None
        for row in event_rows
    )
    bodyweight_deferred = bool(profile.get("bodyweight_deferred")) or bool(
        profile.get("body_composition_deferred")
    )
    if not (has_bodyweight_profile or has_bodyweight_events or bodyweight_deferred):
        issues.append(
            _issue(
                "INV-006",
                "baseline_bodyweight_unknown",
                "medium",
                "Bodyweight baseline missing and not explicitly deferred.",
            )
        )

    jump_goals = [r for r in goal_rows if _has_jump_goal(r.get("data") or {})]
    if jump_goals and not _has_jump_tracking_path(set_rows, active_custom_rules):
        issues.append(
            _issue(
                "INV-005",
                "goal_trackability_missing",
                "medium",
                "Jump/Dunk goal detected without an observable tracking path.",
                metrics={"jump_goal_count": len(jump_goals)},
            )
        )

    metrics = {
        "total_events": len(event_rows),
        "set_logged_total": total_set_logged,
        "set_logged_unresolved": unresolved_set_logged,
        "set_logged_unresolved_pct": unresolved_pct,
        "goal_total": len(goal_rows),
        "active_custom_rule_count": len(active_custom_rules),
        "timezone_configured": bool(timezone_pref),
    }
    return issues, metrics


def _compute_quality_score(issues: list[dict[str, Any]]) -> float:
    penalty = 0.0
    for issue in issues:
        penalty += _SEVERITY_WEIGHT.get(issue["severity"], 0.05)
    penalty = min(penalty, 0.95)
    return round(max(0.0, 1.0 - penalty), 3)


def _status_from_score(score: float, issues: list[dict[str, Any]]) -> str:
    if any(issue["severity"] == "high" for issue in issues):
        return "degraded"
    if score >= 0.9:
        return "healthy"
    if score >= 0.75:
        return "monitor"
    return "degraded"


def _build_quality_projection_data(
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    score = _compute_quality_score(issues)
    status = _status_from_score(score, issues)
    sev_counts = Counter(issue["severity"] for issue in issues)
    issues_by_severity = {
        "high": sev_counts.get("high", 0),
        "medium": sev_counts.get("medium", 0),
        "low": sev_counts.get("low", 0),
        "info": sev_counts.get("info", 0),
    }

    sorted_issues = sorted(
        issues,
        key=lambda item: (
            _SEVERITY_ORDER.get(item["severity"], 99),
            item["invariant_id"],
            item["type"],
        ),
    )

    top_issues = [
        {
            "issue_id": issue["issue_id"],
            "type": issue["type"],
            "severity": issue["severity"],
            "invariant_id": issue["invariant_id"],
        }
        for issue in sorted_issues[:5]
    ]

    enriched_issues = [{**issue, "status": "open", "detected_at": evaluated_at} for issue in sorted_issues]

    return {
        "score": score,
        "status": status,
        "issues_open": len(enriched_issues),
        "issues_by_severity": issues_by_severity,
        "top_issues": top_issues,
        "issues": enriched_issues,
        "invariant_mode": "read_only",
        "invariants_evaluated": ["INV-001", "INV-003", "INV-005", "INV-006"],
        "metrics": metrics,
        "last_evaluated_at": evaluated_at,
        "decision_ref": "docs/design/013-self-healing-agentic-data-plane.md",
    }


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not projection_rows:
        return {}
    data = projection_rows[0]["data"]
    return {
        "quality_status": data.get("status"),
        "quality_score": data.get("score"),
        "quality_open_issues": data.get("issues_open"),
    }


@projection_handler(
    *_EVENT_TYPES,
    dimension_meta={
        "name": "quality_health",
        "description": (
            "Read-only invariant health surface for Decision 13. "
            "Detects backend quality drift without mutating canonical events."
        ),
        "key_structure": "single overview per user",
        "projection_key": "overview",
        "granularity": ["snapshot"],
        "relates_to": {
            "user_profile": {
                "join": "overview",
                "why": "Agent can prioritize housekeeping from quality issues",
            },
            "training_timeline": {
                "join": "set.logged",
                "why": "Unresolved exercise identities degrade timeline/progression quality",
            },
        },
        "context_seeds": [
            "data_quality",
            "timezone_preference",
            "goal_trackability",
        ],
        "output_schema": {
            "score": "number (0..1)",
            "status": "string — healthy|monitor|degraded",
            "issues_open": "integer",
            "issues_by_severity": {"high": "integer", "medium": "integer", "low": "integer", "info": "integer"},
            "top_issues": [{"issue_id": "string", "type": "string", "severity": "string", "invariant_id": "string"}],
            "issues": [{
                "issue_id": "string",
                "invariant_id": "string",
                "type": "string",
                "severity": "string",
                "status": "string",
                "detail": "string",
                "metrics": "object",
                "detected_at": "ISO 8601 datetime",
            }],
            "invariant_mode": "string — read_only",
            "invariants_evaluated": ["string"],
            "metrics": "object",
            "last_evaluated_at": "ISO 8601 datetime",
            "decision_ref": "string",
        },
        "manifest_contribution": _manifest_contribution,
    },
)
async def update_quality_health(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)
    alias_map = await get_alias_map(conn, user_id, retracted_ids=retracted_ids)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id, list(_EVENT_TYPES)),
        )
        rows = await cur.fetchall()

    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    if not rows:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM projections WHERE user_id = %s AND projection_type = 'quality_health' AND key = 'overview'",
                (user_id,),
            )
        return

    issues, metrics = _evaluate_read_only_invariants(rows, alias_map)
    now_iso = datetime.now(timezone.utc).isoformat()
    projection_data = _build_quality_projection_data(issues, metrics, now_iso)
    last_event_id = str(rows[-1]["id"])

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'quality_health', 'overview', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), last_event_id),
        )

    logger.info(
        "Updated quality_health for user=%s (status=%s score=%.3f issues=%d)",
        user_id,
        projection_data["status"],
        projection_data["score"],
        projection_data["issues_open"],
    )

