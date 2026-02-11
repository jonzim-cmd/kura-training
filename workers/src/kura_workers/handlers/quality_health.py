"""Quality Health projection handler (Decision 13, Phase 1).

Evaluates invariants in read-only mode and generates inspectable repair proposals.
Every proposal is passed through a simulate bridge (contract-compatible with
`/v1/events/simulate`) before it can enter an apply-ready path.
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..semantic_catalog import EXERCISE_CATALOG
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

_REPAIR_STATE_PROPOSED = "proposed"
_REPAIR_STATE_SIMULATED_SAFE = "simulated_safe"
_REPAIR_STATE_SIMULATED_RISKY = "simulated_risky"
_REPAIR_STATE_REJECTED = "rejected"

_APPLY_ALLOWED_STATES = {_REPAIR_STATE_SIMULATED_SAFE}
_SIMULATE_ENDPOINT = "/v1/events/simulate"

_OVERVIEW_KEY_BY_PROJECTION = {
    "body_composition",
    "causal_inference",
    "nutrition",
    "quality_health",
    "readiness_inference",
    "recovery",
    "semantic_memory",
    "training_timeline",
}


def _build_exercise_lookup() -> tuple[dict[str, str], set[str]]:
    variant_to_canonical: dict[str, str] = {}
    canonical_keys: set[str] = set()
    for entry in EXERCISE_CATALOG:
        canonical = _normalize(entry.canonical_key)
        if not canonical:
            continue
        canonical_keys.add(canonical)
        variant_to_canonical[canonical] = canonical
        variant_to_canonical[_normalize(entry.canonical_label)] = canonical
        for variant in entry.variants:
            normalized = _normalize(variant)
            if normalized:
                variant_to_canonical[normalized] = canonical
    return variant_to_canonical, canonical_keys


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


_EXERCISE_VARIANT_TO_CANONICAL, _EXERCISE_CANONICAL_KEYS = _build_exercise_lookup()


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


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", _normalize(value))
    slug = slug.strip("_")
    return slug


def _handler_projection_type(handler: Any) -> str:
    return str(getattr(handler, "__module__", "")).split(".")[-1]


def _projection_key_for_event(projection_type: str, event: dict[str, Any]) -> str:
    data = event.get("data") or {}
    if projection_type == "user_profile":
        return "me"
    if projection_type in _OVERVIEW_KEY_BY_PROJECTION:
        return "overview"
    if projection_type in {"exercise_progression", "strength_inference"}:
        key = _normalize(data.get("exercise_id"))
        return key or "*"
    if projection_type == "training_plan":
        return _normalize(data.get("plan_id")) or "default"
    return "*"


def _simulate_event_batch(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic simulate bridge for worker-side repair proposals.

    The output shape mirrors `/v1/events/simulate` to keep proposal artifacts
    compatible with the API simulation contract.
    """
    warnings: list[dict[str, Any]] = []
    notes: list[str] = []
    impact_map: dict[tuple[str, str], dict[str, Any]] = {}

    for index, event in enumerate(events):
        event_type = _normalize(event.get("event_type"))
        data = event.get("data") or {}

        if event_type == "exercise.alias_created":
            alias = _normalize(data.get("alias"))
            exercise_id = _normalize(data.get("exercise_id"))
            if not alias:
                warnings.append(
                    {
                        "event_index": index,
                        "field": "data.alias",
                        "message": "exercise.alias_created is missing alias",
                        "severity": "warning",
                    }
                )
            if not exercise_id:
                warnings.append(
                    {
                        "event_index": index,
                        "field": "data.exercise_id",
                        "message": "exercise.alias_created is missing exercise_id",
                        "severity": "warning",
                    }
                )
            elif exercise_id not in _EXERCISE_CANONICAL_KEYS:
                warnings.append(
                    {
                        "event_index": index,
                        "field": "data.exercise_id",
                        "message": (
                            f"exercise_id '{exercise_id}' is not in the global exercise catalog"
                        ),
                        "severity": "warning",
                    }
                )
        elif event_type == "preference.set":
            key = _normalize(data.get("key"))
            if not key:
                warnings.append(
                    {
                        "event_index": index,
                        "field": "data.key",
                        "message": "preference.set is missing key",
                        "severity": "warning",
                    }
                )
            if key in {"timezone", "time_zone"} and _normalize(data.get("value")) == "utc":
                notes.append(
                    "Timezone proposal uses UTC assumption; confirm with user before apply."
                )

        handlers = projection_handler_registry(event_type)
        if not handlers:
            notes.append(
                f"No projection handlers matched simulated event_type '{event_type}'."
            )
            continue

        for handler in handlers:
            projection_type = _handler_projection_type(handler)
            key = _projection_key_for_event(projection_type, event)
            change = "update" if key != "*" else "unknown"
            impact_key = (projection_type, key)
            reason = (
                f"event_type '{event_type}' routes to handler '{handler.__name__}'"
            )

            if impact_key not in impact_map:
                impact_map[impact_key] = {
                    "projection_type": projection_type,
                    "key": key,
                    "change": change,
                    "current_version": None,
                    "predicted_version": None,
                    "reasons": [reason],
                }
            else:
                impact_map[impact_key]["reasons"].append(reason)
                if change == "unknown":
                    impact_map[impact_key]["change"] = "unknown"

    projection_impacts = sorted(
        impact_map.values(),
        key=lambda item: (item["projection_type"], item["key"]),
    )

    return {
        "event_count": len(events),
        "warnings": warnings,
        "projection_impacts": projection_impacts,
        "notes": notes,
        "engine": "worker_simulate_bridge_v1",
        "target_endpoint": _SIMULATE_ENDPOINT,
    }


def projection_handler_registry(event_type: str) -> list[Any]:
    # Late import to avoid circular import at module import time.
    from ..registry import get_projection_handlers

    return list(get_projection_handlers(event_type))


def _build_issue_proposal_base(
    issue: dict[str, Any],
    evaluated_at: str,
    tier: str,
    rationale: str,
    assumptions: list[str] | None = None,
) -> dict[str, Any]:
    issue_id = str(issue["issue_id"])
    return {
        "proposal_id": f"repair:{issue_id}",
        "issue_id": issue_id,
        "invariant_id": issue["invariant_id"],
        "issue_type": issue["type"],
        "tier": tier,
        "state": _REPAIR_STATE_PROPOSED,
        "safe_for_apply": False,
        "auto_apply_eligible": tier == "A",
        "rationale": rationale,
        "assumptions": assumptions or [],
        "proposed_at": evaluated_at,
        "proposed_event_batch": {"events": []},
        "simulate": None,
        "state_history": [
            {
                "state": _REPAIR_STATE_PROPOSED,
                "at": evaluated_at,
            }
        ],
    }


def _propose_inv001_issue(
    issue: dict[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    metrics = issue.get("metrics") or {}
    unresolved_terms: list[dict[str, Any]] = (
        metrics.get("top_unresolved_terms_with_counts") or []
    )
    events: list[dict[str, Any]] = []
    unmatched_terms: list[str] = []
    confidence_sources: list[str] = []

    for item in unresolved_terms:
        term = _normalize(item.get("term"))
        if not term or term == "<missing_exercise>":
            continue

        canonical = _EXERCISE_VARIANT_TO_CANONICAL.get(term)
        source = "catalog_variant_exact"
        if not canonical:
            slug = _slugify(term)
            if slug in _EXERCISE_CANONICAL_KEYS:
                canonical = slug
                source = "catalog_key_slug_match"

        if not canonical:
            slug = _slugify(term)
            if slug:
                canonical = slug
                source = "slug_fallback"

        if not canonical:
            unmatched_terms.append(term)
            continue

        confidence_sources.append(source)
        events.append(
            {
                "timestamp": evaluated_at,
                "event_type": "exercise.alias_created",
                "data": {
                    "alias": term,
                    "exercise_id": canonical,
                    "confidence": "inferred",
                },
                "metadata": {
                    "source": "quality_health",
                    "agent": "repair_planner",
                    "session_id": f"quality:{issue['issue_id']}",
                    "idempotency_key": (
                        f"repair-{issue['issue_id']}-{term}-{canonical}"
                    ),
                },
            }
        )

    has_fallback = any(source == "slug_fallback" for source in confidence_sources)
    tier = "B" if has_fallback else "A"
    assumptions = []
    if has_fallback:
        assumptions.append(
            "Some exercise mappings use slug fallback and need confirmation."
        )

    proposal = _build_issue_proposal_base(
        issue=issue,
        evaluated_at=evaluated_at,
        tier=tier,
        rationale=(
            "Map unresolved exercise terms to canonical exercise_id values "
            "to restore identity consistency (INV-001)."
        ),
        assumptions=assumptions,
    )
    proposal["proposed_event_batch"]["events"] = events
    if unmatched_terms:
        proposal["unmatched_terms"] = unmatched_terms
    proposal["candidate_sources"] = confidence_sources
    return proposal


def _propose_inv003_issue(issue: dict[str, Any], evaluated_at: str) -> dict[str, Any]:
    proposal = _build_issue_proposal_base(
        issue=issue,
        evaluated_at=evaluated_at,
        tier="B",
        rationale=(
            "Set explicit timezone preference to prevent schedule/date drift "
            "(INV-003)."
        ),
        assumptions=["Default timezone assumed as UTC until user confirms."],
    )
    proposal["proposed_event_batch"]["events"] = [
        {
            "timestamp": evaluated_at,
            "event_type": "preference.set",
            "data": {
                "key": "timezone",
                "value": "UTC",
            },
            "metadata": {
                "source": "quality_health",
                "agent": "repair_planner",
                "session_id": f"quality:{issue['issue_id']}",
                "idempotency_key": f"repair-{issue['issue_id']}-timezone-utc",
            },
        }
    ]
    return proposal


def _generate_repair_proposals(
    issues: list[dict[str, Any]],
    evaluated_at: str,
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for issue in issues:
        issue_type = issue.get("type")
        if issue_type == "unresolved_exercise_identity":
            proposals.append(_propose_inv001_issue(issue, evaluated_at))
        elif issue_type == "timezone_missing":
            proposals.append(_propose_inv003_issue(issue, evaluated_at))
    return proposals


def _finalize_proposal_state(
    proposal: dict[str, Any],
    simulation: dict[str, Any],
    evaluated_at: str,
) -> None:
    warnings = simulation.get("warnings") or []
    impacts = simulation.get("projection_impacts") or []
    has_unknown_impacts = any(impact.get("change") == "unknown" for impact in impacts)

    state = _REPAIR_STATE_SIMULATED_SAFE
    if not proposal.get("proposed_event_batch", {}).get("events"):
        state = _REPAIR_STATE_REJECTED
    elif warnings or has_unknown_impacts or proposal.get("tier") != "A":
        state = _REPAIR_STATE_SIMULATED_RISKY

    proposal["simulate"] = simulation
    proposal["state"] = state
    proposal["safe_for_apply"] = state in _APPLY_ALLOWED_STATES
    proposal["state_history"].append({"state": state, "at": evaluated_at})


def _simulate_repair_proposals(
    proposals: list[dict[str, Any]],
    evaluated_at: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for proposal in proposals:
        events = proposal.get("proposed_event_batch", {}).get("events") or []
        simulation = _simulate_event_batch(events)
        _finalize_proposal_state(proposal, simulation, evaluated_at)
        result.append(proposal)
    return result


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
        top_terms_with_counts = [
            {"term": term, "count": count}
            for term, count in unresolved_terms.most_common(5)
        ]
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
                    "top_unresolved_terms_with_counts": top_terms_with_counts,
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

    proposals = _simulate_repair_proposals(
        _generate_repair_proposals(sorted_issues, evaluated_at),
        evaluated_at=evaluated_at,
    )
    proposals_by_issue = {proposal["issue_id"]: proposal for proposal in proposals}
    proposals_by_state = Counter(
        proposal.get("state", _REPAIR_STATE_PROPOSED)
        for proposal in proposals
    )
    apply_ready = [p["proposal_id"] for p in proposals if p.get("safe_for_apply")]

    enriched_issues = [
        {
            **issue,
            "status": "open",
            "detected_at": evaluated_at,
            "proposal_state": (
                proposals_by_issue[issue["issue_id"]]["state"]
                if issue["issue_id"] in proposals_by_issue
                else None
            ),
        }
        for issue in sorted_issues
    ]

    return {
        "score": score,
        "status": status,
        "issues_open": len(enriched_issues),
        "issues_by_severity": issues_by_severity,
        "top_issues": top_issues,
        "issues": enriched_issues,
        "repair_proposals": proposals,
        "repair_proposals_total": len(proposals),
        "repair_proposals_by_state": {
            _REPAIR_STATE_PROPOSED: proposals_by_state.get(_REPAIR_STATE_PROPOSED, 0),
            _REPAIR_STATE_SIMULATED_SAFE: proposals_by_state.get(
                _REPAIR_STATE_SIMULATED_SAFE, 0
            ),
            _REPAIR_STATE_SIMULATED_RISKY: proposals_by_state.get(
                _REPAIR_STATE_SIMULATED_RISKY, 0
            ),
            _REPAIR_STATE_REJECTED: proposals_by_state.get(_REPAIR_STATE_REJECTED, 0),
        },
        "repair_apply_ready_ids": apply_ready,
        "repair_apply_enabled": False,
        "repair_apply_gate": (
            "tier_a_only_and_state_simulated_safe; auto-apply disabled in phase_1"
        ),
        "simulate_bridge": {
            "target_endpoint": _SIMULATE_ENDPOINT,
            "engine": "worker_simulate_bridge_v1",
            "decision_phase": "phase_1_assisted_repairs",
        },
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
        "quality_repair_apply_ready": len(data.get("repair_apply_ready_ids") or []),
    }


@projection_handler(
    *_EVENT_TYPES,
    dimension_meta={
        "name": "quality_health",
        "description": (
            "Invariant health + assisted repair proposals for Decision 13. "
            "Generates simulated repair plans without mutating canonical events."
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
                "proposal_state": "string | null",
                "detail": "string",
                "metrics": "object",
                "detected_at": "ISO 8601 datetime",
            }],
            "repair_proposals": [{
                "proposal_id": "string",
                "issue_id": "string",
                "tier": "string — A|B|C",
                "state": (
                    "string — proposed|simulated_safe|simulated_risky|rejected"
                ),
                "safe_for_apply": "boolean",
                "auto_apply_eligible": "boolean",
                "rationale": "string",
                "assumptions": ["string"],
                "proposed_event_batch": {"events": ["CreateEventRequest-like object"]},
                "simulate": {
                    "event_count": "integer",
                    "warnings": ["object"],
                    "projection_impacts": ["object"],
                    "notes": ["string"],
                },
            }],
            "repair_proposals_total": "integer",
            "repair_proposals_by_state": {
                "proposed": "integer",
                "simulated_safe": "integer",
                "simulated_risky": "integer",
                "rejected": "integer",
            },
            "repair_apply_ready_ids": ["string"],
            "repair_apply_enabled": "boolean",
            "repair_apply_gate": "string",
            "simulate_bridge": {
                "target_endpoint": "string",
                "engine": "string",
                "decision_phase": "string",
            },
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
