"""Quality Health projection handler (Decision 13, Phase 2).

Evaluates invariants, generates inspectable repair proposals, and auto-applies
only deterministic low-risk (Tier A) proposals behind strict policy gates.
Every proposal is passed through a simulate bridge (contract-compatible with
`/v1/events/simulate`) before it can enter an apply-ready path.
"""

import hashlib
import json
import logging
import re
import statistics
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..external_import_error_taxonomy import (
    classify_import_error_code,
    is_import_parse_quality_failure,
)
from ..extraction_calibration import resolve_extraction_calibration_status
from ..learning_telemetry import build_learning_signal_event
from ..repair_provenance import build_repair_provenance, summarize_repair_provenance
from ..registry import projection_handler
from ..schema_capabilities import (
    build_schema_capability_report,
    detect_relation_capabilities,
)
from ..semantic_catalog import EXERCISE_CATALOG
from ..set_corrections import apply_set_correction_chain
from ..training_core_fields import evaluate_set_context_rows
from ..training_rollout_v1 import confidence_band
from ..training_session_completeness import (
    MISSING_ANCHOR_ERROR_CODES,
    evaluate_session_completeness,
)
from ..utils import get_alias_map, get_retracted_event_ids

logger = logging.getLogger(__name__)

_INVARIANT_SOURCE_EVENT_TYPES = (
    "set.logged",
    "session.logged",
    "set.corrected",
    "exercise.alias_created",
    "preference.set",
    "profile.updated",
    "goal.set",
    "bodyweight.logged",
    "projection_rule.created",
    "projection_rule.archived",
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    "weight_target.set",
    "sleep_target.set",
    "nutrition_target.set",
    "workflow.onboarding.closed",
    "workflow.onboarding.override_granted",
    "external.activity_imported",
)

_QUALITY_SIGNAL_EVENT_TYPES = (
    "quality.save_claim.checked",
    "quality.fix.applied",
    "quality.fix.rejected",
    "quality.issue.closed",
    "external.import.job",
)

_EVENT_TYPES = _INVARIANT_SOURCE_EVENT_TYPES + _QUALITY_SIGNAL_EVENT_TYPES

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
_REPAIR_STATE_APPLIED = "applied"
_REPAIR_STATE_AUTO_APPLY_REJECTED = "auto_apply_rejected"
_REPAIR_STATE_VERIFIED_CLOSED = "verified_closed"

_APPLY_ALLOWED_STATES = {_REPAIR_STATE_SIMULATED_SAFE}
_SIMULATE_ENDPOINT = "/v1/events/simulate"
_AUTO_APPLY_POLICY_GATE = (
    "tier_a_only_and_state_simulated_safe_and_no_warnings_and_no_unknown_impacts_and_deterministic_source"
)
_AUTO_APPLY_POLICY_VERSION = "phase_2_tier_a_v1"
_AUTONOMY_POLICY_VERSION = "phase_3_integrity_slo_v1"
_QUALITY_EVENT_FIX_APPLIED = "quality.fix.applied"
_QUALITY_EVENT_FIX_REJECTED = "quality.fix.rejected"
_QUALITY_EVENT_ISSUE_CLOSED = "quality.issue.closed"
_QUALITY_EVENT_SAVE_CLAIM_CHECKED = "quality.save_claim.checked"
_DETERMINISTIC_PROPOSAL_SOURCES = {
    "catalog_variant_exact",
    "catalog_key_slug_match",
}
_SLO_LOOKBACK_DAYS = 7
_SLO_UNRESOLVED_SET_PCT_HEALTHY_MAX = 2.0
_SLO_UNRESOLVED_SET_PCT_MONITOR_MAX = 5.0
_SLO_SAVE_CLAIM_MISMATCH_PCT_HEALTHY_MAX = 0.0
_SLO_SAVE_CLAIM_MISMATCH_PCT_MONITOR_MAX = 1.0
_SLO_REPAIR_LATENCY_HOURS_HEALTHY_MAX = 24.0
_SLO_REPAIR_LATENCY_HOURS_MONITOR_MAX = 48.0
_STATUS_ORDER = {"healthy": 0, "monitor": 1, "degraded": 2}

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
        data = row.get("effective_data") or row.get("data") or {}
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
    repair_provenance: dict[str, Any] | None = None,
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
        "repair_provenance": repair_provenance
        or {
            "entries": [],
            "summary": summarize_repair_provenance([]),
        },
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
    provenance_entries: list[dict[str, Any]] = []

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
        if source == "catalog_variant_exact":
            provenance = build_repair_provenance(
                source_type="inferred",
                confidence=0.95,
                applies_scope="exercise_session",
                reason="Catalog variant exact match for unresolved alias.",
            )
        elif source == "catalog_key_slug_match":
            provenance = build_repair_provenance(
                source_type="inferred",
                confidence=0.9,
                applies_scope="exercise_session",
                reason="Catalog key slug match for unresolved alias.",
            )
        else:
            provenance = build_repair_provenance(
                source_type="estimated",
                confidence=0.55,
                applies_scope="exercise_session",
                reason="Slug fallback guess for unresolved alias.",
            )
        provenance_entries.append(provenance)
        events.append(
            {
                "timestamp": evaluated_at,
                "event_type": "exercise.alias_created",
                "data": {
                    "alias": term,
                    "exercise_id": canonical,
                    "confidence": "inferred",
                    "repair_provenance": provenance,
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
        repair_provenance={
            "entries": provenance_entries,
            "summary": summarize_repair_provenance(provenance_entries),
        },
    )
    proposal["proposed_event_batch"]["events"] = events
    if unmatched_terms:
        proposal["unmatched_terms"] = unmatched_terms
    proposal["candidate_sources"] = confidence_sources
    return proposal


def _propose_inv003_issue(issue: dict[str, Any], evaluated_at: str) -> dict[str, Any]:
    provenance = build_repair_provenance(
        source_type="estimated",
        confidence=0.45,
        applies_scope="session",
        reason="Timezone fallback requires confirmation from user.",
    )
    proposal = _build_issue_proposal_base(
        issue=issue,
        evaluated_at=evaluated_at,
        tier="B",
        rationale=(
            "Set explicit timezone preference to prevent schedule/date drift "
            "(INV-003)."
        ),
        assumptions=["Default timezone assumed as UTC until user confirms."],
        repair_provenance={
            "entries": [provenance],
            "summary": summarize_repair_provenance([provenance]),
        },
    )
    proposal["proposed_event_batch"]["events"] = [
        {
            "timestamp": evaluated_at,
            "event_type": "preference.set",
            "data": {
                "key": "timezone",
                "value": "UTC",
                "repair_provenance": provenance,
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


def _sort_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        issues,
        key=lambda item: (
            _SEVERITY_ORDER.get(item["severity"], 99),
            item["invariant_id"],
            item["type"],
        ),
    )


def _build_simulated_repair_proposals(
    issues: list[dict[str, Any]],
    evaluated_at: str,
) -> list[dict[str, Any]]:
    sorted_issues = _sort_issues(issues)
    return _simulate_repair_proposals(
        _generate_repair_proposals(sorted_issues, evaluated_at),
        evaluated_at=evaluated_at,
    )


def _has_unknown_projection_impacts(simulation: dict[str, Any]) -> bool:
    impacts = simulation.get("projection_impacts") or []
    return any(_normalize(impact.get("change")) == "unknown" for impact in impacts)


def _proposal_has_deterministic_source(proposal: dict[str, Any]) -> bool:
    if proposal.get("issue_type") != "unresolved_exercise_identity":
        return False
    sources = proposal.get("candidate_sources") or []
    return bool(sources) and all(
        source in _DETERMINISTIC_PROPOSAL_SOURCES for source in sources
    )


def _proposal_confidence_band(proposal: dict[str, Any]) -> str:
    summary = (proposal.get("repair_provenance") or {}).get("summary") or {}
    by_band = summary.get("by_confidence_band") or {}
    if by_band.get("low", 0) > 0:
        return "low"
    if by_band.get("medium", 0) > 0:
        return "medium"
    if by_band.get("high", 0) > 0:
        return "high"
    return "unknown"


def _auto_apply_decision(
    proposal: dict[str, Any],
    *,
    allow_tier_a_auto_apply: bool = True,
) -> tuple[bool, str]:
    if not allow_tier_a_auto_apply:
        return False, "autonomy_throttled"
    if proposal.get("tier") != "A":
        return False, "tier_not_a"
    if proposal.get("state") != _REPAIR_STATE_SIMULATED_SAFE:
        return False, "state_not_simulated_safe"
    simulation = proposal.get("simulate") or {}
    if simulation.get("warnings"):
        return False, "warnings_present"
    if _has_unknown_projection_impacts(simulation):
        return False, "unknown_projection_impacts"
    if not _proposal_has_deterministic_source(proposal):
        return False, "non_deterministic_source"
    if _proposal_confidence_band(proposal) == "low":
        return False, "low_confidence_repair"
    events = proposal.get("proposed_event_batch", {}).get("events") or []
    if not events:
        return False, "empty_event_batch"
    return True, "policy_pass"


def _event_idempotency_key(event: dict[str, Any]) -> str:
    metadata = event.get("metadata") or {}
    return str(metadata.get("idempotency_key", "")).strip()


def _stable_idempotency_suffix(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


def _build_quality_fix_applied_event(
    proposal: dict[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    repair_events = proposal.get("proposed_event_batch", {}).get("events") or []
    repair_idempotency_keys = [
        key for event in repair_events if (key := _event_idempotency_key(event))
    ]
    idempotency_key = (
        f"quality-fix-applied-{_stable_idempotency_suffix(str(proposal['proposal_id']))}"
    )
    provenance_summary = (
        (proposal.get("repair_provenance") or {}).get("summary") or {}
    )
    return {
        "timestamp": evaluated_at,
        "event_type": _QUALITY_EVENT_FIX_APPLIED,
        "data": {
            "proposal_id": proposal["proposal_id"],
            "issue_id": proposal["issue_id"],
            "invariant_id": proposal["invariant_id"],
            "issue_type": proposal["issue_type"],
            "tier": proposal["tier"],
            "policy_gate": _AUTO_APPLY_POLICY_GATE,
            "policy_version": _AUTO_APPLY_POLICY_VERSION,
            "repair_event_count": len(repair_idempotency_keys),
            "repair_event_idempotency_keys": repair_idempotency_keys,
            "repair_provenance_summary": provenance_summary,
        },
        "metadata": {
            "source": "quality_health",
            "agent": "repair_autopilot",
            "session_id": f"quality:{proposal['issue_id']}",
            "idempotency_key": idempotency_key,
        },
    }


def _build_quality_fix_rejected_event(
    proposal: dict[str, Any],
    evaluated_at: str,
    reason_code: str,
) -> dict[str, Any]:
    simulation = proposal.get("simulate") or {}
    idempotency_seed = f"{proposal['proposal_id']}:{reason_code}"
    idempotency_key = (
        f"quality-fix-rejected-{_stable_idempotency_suffix(idempotency_seed)}"
    )
    provenance_summary = (
        (proposal.get("repair_provenance") or {}).get("summary") or {}
    )
    return {
        "timestamp": evaluated_at,
        "event_type": _QUALITY_EVENT_FIX_REJECTED,
        "data": {
            "proposal_id": proposal["proposal_id"],
            "issue_id": proposal["issue_id"],
            "invariant_id": proposal["invariant_id"],
            "issue_type": proposal["issue_type"],
            "tier": proposal["tier"],
            "proposal_state": proposal.get("state"),
            "reason_code": reason_code,
            "warnings_count": len(simulation.get("warnings") or []),
            "unknown_projection_impacts": _has_unknown_projection_impacts(simulation),
            "policy_gate": _AUTO_APPLY_POLICY_GATE,
            "policy_version": _AUTO_APPLY_POLICY_VERSION,
            "repair_provenance_summary": provenance_summary,
        },
        "metadata": {
            "source": "quality_health",
            "agent": "repair_autopilot",
            "session_id": f"quality:{proposal['issue_id']}",
            "idempotency_key": idempotency_key,
        },
    }


def _build_quality_issue_closed_event(
    result: dict[str, Any],
    verified_at: str,
) -> dict[str, Any]:
    idempotency_key = (
        f"quality-issue-closed-{_stable_idempotency_suffix(str(result['proposal_id']))}"
    )
    return {
        "timestamp": verified_at,
        "event_type": _QUALITY_EVENT_ISSUE_CLOSED,
        "data": {
            "proposal_id": result["proposal_id"],
            "issue_id": result["issue_id"],
            "invariant_id": result["invariant_id"],
            "issue_type": result["issue_type"],
            "closed_by": "auto_apply_verification",
            "policy_version": _AUTO_APPLY_POLICY_VERSION,
        },
        "metadata": {
            "source": "quality_health",
            "agent": "repair_autopilot",
            "session_id": f"quality:{result['issue_id']}",
            "idempotency_key": idempotency_key,
        },
    }


def _build_detection_learning_signal_events(
    user_id: str,
    issues: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    evaluated_at: str,
    source_anchor: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for issue in issues:
        issue_id = str(issue["issue_id"])
        invariant_id = str(issue.get("invariant_id") or "none")
        issue_type = str(issue.get("type") or "none")
        events.append(
            build_learning_signal_event(
                user_id=str(user_id),
                signal_type="quality_issue_detected",
                workflow_phase="quality_health_evaluation",
                source="quality_health",
                agent="repair_planner",
                modality="chat",
                confidence="high" if issue.get("severity") == "high" else "medium",
                issue_type=issue_type,
                invariant_id=invariant_id,
                attributes={
                    "issue_id": issue_id,
                    "severity": issue.get("severity"),
                },
                session_id=f"quality:{issue_id}",
                timestamp=evaluated_at,
                idempotency_seed=f"{source_anchor}:{issue_id}:quality_issue_detected",
                agent_version="worker_quality_health_v1",
            )
        )

    for proposal in proposals:
        proposal_id = str(proposal.get("proposal_id", ""))
        issue_id = str(proposal.get("issue_id", ""))
        invariant_id = str(proposal.get("invariant_id") or "none")
        issue_type = str(proposal.get("issue_type") or "none")
        tier = str(proposal.get("tier") or "unknown")
        state = str(proposal.get("state") or _REPAIR_STATE_PROPOSED)
        confidence = "high" if tier == "A" else "medium"

        events.append(
            build_learning_signal_event(
                user_id=str(user_id),
                signal_type="repair_proposed",
                workflow_phase="quality_health_repair_planning",
                source="quality_health",
                agent="repair_planner",
                modality="chat",
                confidence=confidence,
                issue_type=issue_type,
                invariant_id=invariant_id,
                attributes={
                    "proposal_id": proposal_id,
                    "issue_id": issue_id,
                    "tier": tier,
                    "state": state,
                    "safe_for_apply": bool(proposal.get("safe_for_apply")),
                },
                session_id=f"quality:{issue_id or proposal_id}",
                timestamp=evaluated_at,
                idempotency_seed=f"{source_anchor}:{proposal_id}:repair_proposed",
                agent_version="worker_quality_health_v1",
            )
        )

        state_signal = {
            _REPAIR_STATE_SIMULATED_SAFE: "repair_simulated_safe",
            _REPAIR_STATE_SIMULATED_RISKY: "repair_simulated_risky",
        }.get(state)
        if not state_signal:
            continue
        events.append(
            build_learning_signal_event(
                user_id=str(user_id),
                signal_type=state_signal,
                workflow_phase="quality_health_simulation",
                source="quality_health",
                agent="repair_planner",
                modality="chat",
                confidence=confidence,
                issue_type=issue_type,
                invariant_id=invariant_id,
                attributes={
                    "proposal_id": proposal_id,
                    "issue_id": issue_id,
                    "tier": tier,
                    "state": state,
                    "safe_for_apply": bool(proposal.get("safe_for_apply")),
                },
                session_id=f"quality:{issue_id or proposal_id}",
                timestamp=evaluated_at,
                idempotency_seed=f"{source_anchor}:{proposal_id}:{state_signal}",
                agent_version="worker_quality_health_v1",
            )
        )

    return events


async def _insert_events_with_idempotency_guard(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    if not events:
        return {
            "inserted_event_ids": [],
            "inserted_keys": set(),
            "preexisting_keys": set(),
        }

    unique_events: list[dict[str, Any]] = []
    event_keys: list[str] = []
    seen_keys: set[str] = set()
    for event in events:
        key = _event_idempotency_key(event)
        if not key:
            raise ValueError(
                f"quality repair event '{event.get('event_type', '')}' missing metadata.idempotency_key"
            )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_events.append(event)
        event_keys.append(key)

    await conn.execute("SET LOCAL ROLE app_writer")
    await conn.execute(
        "SELECT set_config('kura.current_user_id', %s, true)",
        (str(user_id),),
    )

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT metadata->>'idempotency_key' AS idempotency_key
            FROM events
            WHERE user_id = %s
              AND metadata->>'idempotency_key' = ANY(%s)
            """,
            (str(user_id), event_keys),
        )
        existing_rows = await cur.fetchall()

    preexisting_keys = {
        str(row["idempotency_key"])
        for row in existing_rows
        if row.get("idempotency_key")
    }
    inserted_keys: set[str] = set()
    inserted_event_ids: list[str] = []

    async with conn.cursor(row_factory=dict_row) as cur:
        for event in unique_events:
            key = _event_idempotency_key(event)
            if key in preexisting_keys:
                continue
            event_type = str(event.get("event_type", "")).strip()
            if not event_type:
                raise ValueError("quality repair event is missing event_type")

            await cur.execute(
                """
                INSERT INTO events (id, user_id, timestamp, event_type, data, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    str(uuid.uuid4()),
                    str(user_id),
                    event.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                    event_type,
                    Json(event.get("data") or {}),
                    Json(event.get("metadata") or {}),
                ),
            )
            row = await cur.fetchone()
            inserted_event_ids.append(str(row["id"]))
            inserted_keys.add(key)

    await conn.execute("SET LOCAL ROLE app_worker")

    return {
        "inserted_event_ids": inserted_event_ids,
        "inserted_keys": inserted_keys,
        "preexisting_keys": preexisting_keys,
    }


async def _auto_apply_tier_a_repairs(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    proposals: list[dict[str, Any]],
    evaluated_at: str,
    *,
    allow_tier_a_auto_apply: bool = True,
) -> dict[str, Any]:
    events_to_write: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for proposal in proposals:
        state_before = str(proposal.get("state", _REPAIR_STATE_PROPOSED))
        allowed, reason_code = _auto_apply_decision(
            proposal,
            allow_tier_a_auto_apply=allow_tier_a_auto_apply,
        )
        if not allowed:
            proposal["state"] = _REPAIR_STATE_AUTO_APPLY_REJECTED
            proposal["state_history"].append(
                {
                    "state": _REPAIR_STATE_AUTO_APPLY_REJECTED,
                    "at": evaluated_at,
                    "reason_code": reason_code,
                }
            )
            rejected_event = _build_quality_fix_rejected_event(
                proposal,
                evaluated_at,
                reason_code,
            )
            learning_signal = build_learning_signal_event(
                user_id=str(user_id),
                signal_type="repair_auto_rejected",
                workflow_phase="quality_health_auto_apply",
                source="quality_health",
                agent="repair_autopilot",
                modality="chat",
                confidence="medium",
                issue_type=str(proposal.get("issue_type") or "none"),
                invariant_id=str(proposal.get("invariant_id") or "none"),
                attributes={
                    "proposal_id": proposal["proposal_id"],
                    "issue_id": proposal["issue_id"],
                    "reason_code": reason_code,
                    "proposal_state_before": state_before,
                },
                session_id=f"quality:{proposal['issue_id']}",
                timestamp=evaluated_at,
                idempotency_seed=(
                    f"{proposal['proposal_id']}:{reason_code}:repair_auto_rejected"
                ),
                agent_version="worker_quality_health_v1",
            )
            events_to_write.append(rejected_event)
            events_to_write.append(learning_signal)
            results.append(
                {
                    "proposal_id": proposal["proposal_id"],
                    "issue_id": proposal["issue_id"],
                    "invariant_id": proposal["invariant_id"],
                    "issue_type": proposal["issue_type"],
                    "decision": "rejected",
                    "reason_code": reason_code,
                    "proposal_state_before": state_before,
                    "proposal_state_after": _REPAIR_STATE_AUTO_APPLY_REJECTED,
                    "repair_event_keys": [],
                    "audit_event_key": _event_idempotency_key(rejected_event),
                }
            )
            continue

        proposal["state"] = _REPAIR_STATE_APPLIED
        proposal["state_history"].append({"state": _REPAIR_STATE_APPLIED, "at": evaluated_at})
        repair_events = proposal.get("proposed_event_batch", {}).get("events") or []
        applied_event = _build_quality_fix_applied_event(proposal, evaluated_at)
        learning_signal = build_learning_signal_event(
            user_id=str(user_id),
            signal_type="repair_auto_applied",
            workflow_phase="quality_health_auto_apply",
            source="quality_health",
            agent="repair_autopilot",
            modality="chat",
            confidence="high",
            issue_type=str(proposal.get("issue_type") or "none"),
            invariant_id=str(proposal.get("invariant_id") or "none"),
            attributes={
                "proposal_id": proposal["proposal_id"],
                "issue_id": proposal["issue_id"],
                "reason_code": reason_code,
                "proposal_state_before": state_before,
                "repair_event_count": len(repair_events),
            },
            session_id=f"quality:{proposal['issue_id']}",
            timestamp=evaluated_at,
            idempotency_seed=f"{proposal['proposal_id']}:repair_auto_applied",
            agent_version="worker_quality_health_v1",
        )
        events_to_write.extend(repair_events)
        events_to_write.append(applied_event)
        events_to_write.append(learning_signal)
        results.append(
            {
                "proposal_id": proposal["proposal_id"],
                "issue_id": proposal["issue_id"],
                "invariant_id": proposal["invariant_id"],
                "issue_type": proposal["issue_type"],
                "decision": "applied",
                "reason_code": reason_code,
                "proposal_state_before": state_before,
                "proposal_state_after": _REPAIR_STATE_APPLIED,
                "repair_event_keys": [
                    key for event in repair_events if (key := _event_idempotency_key(event))
                ],
                "audit_event_key": _event_idempotency_key(applied_event),
            }
        )

    write_summary = await _insert_events_with_idempotency_guard(conn, user_id, events_to_write)
    inserted_keys = write_summary["inserted_keys"]
    preexisting_keys = write_summary["preexisting_keys"]

    for result in results:
        repair_keys = result.get("repair_event_keys") or []
        result["repair_events_inserted"] = sum(
            1 for key in repair_keys if key in inserted_keys
        )
        result["repair_events_preexisting"] = sum(
            1 for key in repair_keys if key in preexisting_keys
        )
        audit_key = str(result.get("audit_event_key", ""))
        result["audit_event_inserted"] = audit_key in inserted_keys
        result["audit_event_preexisting"] = audit_key in preexisting_keys

    return {"results": results, "write_summary": write_summary}


async def _verify_applied_repairs(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    apply_results: list[dict[str, Any]],
    open_issue_ids: set[str],
    verified_at: str,
) -> dict[str, Any]:
    close_events: list[dict[str, Any]] = []
    learning_signal_events: list[dict[str, Any]] = []
    for result in apply_results:
        if result.get("decision") != "applied":
            continue
        issue_closed = str(result["issue_id"]) not in open_issue_ids
        result["issue_closed_after_verify"] = issue_closed
        result["verified_at"] = verified_at
        if issue_closed:
            result["proposal_state_after_verify"] = _REPAIR_STATE_VERIFIED_CLOSED
            close_event = _build_quality_issue_closed_event(result, verified_at)
            result["close_event_key"] = _event_idempotency_key(close_event)
            close_events.append(close_event)
            learning_signal_events.append(
                build_learning_signal_event(
                    user_id=str(user_id),
                    signal_type="repair_verified_closed",
                    workflow_phase="quality_health_verify",
                    source="quality_health",
                    agent="repair_autopilot",
                    modality="chat",
                    confidence="high",
                    issue_type=str(result.get("issue_type") or "none"),
                    invariant_id=str(result.get("invariant_id") or "none"),
                    attributes={
                        "proposal_id": result["proposal_id"],
                        "issue_id": result["issue_id"],
                    },
                    session_id=f"quality:{result['issue_id']}",
                    timestamp=verified_at,
                    idempotency_seed=(
                        f"{result['proposal_id']}:repair_verified_closed"
                    ),
                    agent_version="worker_quality_health_v1",
                )
            )
        else:
            result["proposal_state_after_verify"] = _REPAIR_STATE_APPLIED
            result["close_event_key"] = None

    close_summary = await _insert_events_with_idempotency_guard(
        conn, user_id, close_events + learning_signal_events
    )
    inserted_keys = close_summary["inserted_keys"]
    preexisting_keys = close_summary["preexisting_keys"]
    for result in apply_results:
        close_key = result.get("close_event_key")
        if not close_key:
            continue
        result["close_event_inserted"] = close_key in inserted_keys
        result["close_event_preexisting"] = close_key in preexisting_keys

    return {"close_summary": close_summary}


async def _load_quality_source_rows(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id, list(_EVENT_TYPES)),
        )
        return await cur.fetchall()


async def _load_external_import_job_rows(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT status, error_code, receipt, created_at
            FROM external_import_jobs
            WHERE user_id = %s
            ORDER BY created_at ASC
            """,
            (user_id,),
        )
        return await cur.fetchall()


def _metric_status(
    value: float,
    healthy_max: float,
    monitor_max: float,
) -> str:
    if value <= healthy_max:
        return "healthy"
    if value <= monitor_max:
        return "monitor"
    return "degraded"


def _worst_status(*statuses: str) -> str:
    valid = [status for status in statuses if status in _STATUS_ORDER]
    if not valid:
        return "healthy"
    return max(valid, key=lambda status: _STATUS_ORDER[status])


def _severity_weight_from_event(data: dict[str, Any]) -> tuple[str, float]:
    """Extract mismatch severity and weight from a quality.save_claim.checked event.

    Legacy events (without mismatch_severity/mismatch_weight) fall back to
    binary classification: mismatch_detected=true → critical/1.0,
    mismatch_detected=false → none/0.0.
    """
    severity = data.get("mismatch_severity")
    weight = data.get("mismatch_weight")
    if severity is not None and weight is not None:
        try:
            parsed_weight = float(weight)
        except (TypeError, ValueError):
            parsed_weight = 0.0
        return str(severity), max(0.0, min(1.0, parsed_weight))
    # Legacy fallback: binary mismatch → critical or none
    mismatch_detected = data.get("mismatch_detected")
    if mismatch_detected is None:
        mismatch_detected = not bool(data.get("allow_saved_claim", False))
    if bool(mismatch_detected):
        return "critical", 1.0
    return "none", 0.0


def _compute_save_claim_slo(
    event_rows: list[dict[str, Any]],
    window_start: datetime,
) -> dict[str, Any]:
    sampled = [
        row
        for row in event_rows
        if row.get("event_type") == _QUALITY_EVENT_SAVE_CLAIM_CHECKED
        and isinstance(row.get("timestamp"), datetime)
        and row["timestamp"] >= window_start
    ]
    total_checks = len(sampled)
    weighted_sum = 0.0
    binary_mismatches = 0
    severity_breakdown: dict[str, int] = {
        "critical": 0,
        "warning": 0,
        "info": 0,
        "none": 0,
    }
    for row in sampled:
        data = row.get("data") or {}
        severity, weight = _severity_weight_from_event(data)
        weighted_sum += weight
        if severity in severity_breakdown:
            severity_breakdown[severity] += 1
        if weight > 0:
            binary_mismatches += 1

    weighted_mismatch_pct = (
        round((weighted_sum / total_checks) * 100, 2) if total_checks else 0.0
    )
    # Legacy binary rate kept for backward compatibility
    binary_mismatch_pct = (
        round((binary_mismatches / total_checks) * 100, 2) if total_checks else 0.0
    )
    status = _metric_status(
        weighted_mismatch_pct,
        _SLO_SAVE_CLAIM_MISMATCH_PCT_HEALTHY_MAX,
        _SLO_SAVE_CLAIM_MISMATCH_PCT_MONITOR_MAX,
    )

    return {
        "metric": "save_claim_mismatch_rate_pct",
        "value": weighted_mismatch_pct,
        "unit": "percent",
        "status": status,
        "window_days": _SLO_LOOKBACK_DAYS,
        "target": {"healthy_max": 0.0, "monitor_max": 1.0},
        "sample_count": total_checks,
        "mismatch_count": binary_mismatches,
        "binary_mismatch_rate_pct": binary_mismatch_pct,
        "weighted_mismatch_sum": round(weighted_sum, 2),
        "weighted_mismatch_rate_pct": weighted_mismatch_pct,
        "severity_breakdown": severity_breakdown,
    }


def _compute_repair_latency_slo(
    event_rows: list[dict[str, Any]],
    window_start: datetime,
) -> dict[str, Any]:
    applied_by_proposal: dict[str, datetime] = {}
    latency_hours: list[float] = []

    for row in event_rows:
        timestamp = row.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue
        data = row.get("data") or {}
        proposal_id = str(data.get("proposal_id", "")).strip()
        if not proposal_id:
            continue
        event_type = str(row.get("event_type", "")).strip()

        if event_type == _QUALITY_EVENT_FIX_APPLIED:
            applied_by_proposal[proposal_id] = timestamp
            continue

        if (
            event_type == _QUALITY_EVENT_ISSUE_CLOSED
            and timestamp >= window_start
            and proposal_id in applied_by_proposal
        ):
            applied_at = applied_by_proposal[proposal_id]
            if timestamp >= applied_at:
                latency_hours.append(
                    (timestamp - applied_at).total_seconds() / 3600.0
                )

    p50_latency = round(statistics.median(latency_hours), 3) if latency_hours else 0.0
    status = _metric_status(
        p50_latency,
        _SLO_REPAIR_LATENCY_HOURS_HEALTHY_MAX,
        _SLO_REPAIR_LATENCY_HOURS_MONITOR_MAX,
    )

    return {
        "metric": "repair_latency_hours_p50",
        "value": p50_latency,
        "unit": "hours",
        "status": status,
        "window_days": _SLO_LOOKBACK_DAYS,
        "target": {"healthy_max": 24.0, "monitor_max": 48.0},
        "sample_count": len(latency_hours),
    }


def _compute_integrity_slos(
    event_rows: list[dict[str, Any]],
    metrics: dict[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    evaluated_dt = datetime.fromisoformat(evaluated_at)
    window_start = evaluated_dt - timedelta(days=_SLO_LOOKBACK_DAYS)

    unresolved_pct = float(metrics.get("set_logged_unresolved_pct", 0.0) or 0.0)
    unresolved_status = _metric_status(
        unresolved_pct,
        _SLO_UNRESOLVED_SET_PCT_HEALTHY_MAX,
        _SLO_UNRESOLVED_SET_PCT_MONITOR_MAX,
    )
    unresolved_metric = {
        "metric": "unresolved_set_logged_pct",
        "value": round(unresolved_pct, 2),
        "unit": "percent",
        "status": unresolved_status,
        "window_days": _SLO_LOOKBACK_DAYS,
        "target": {"healthy_max": 2.0, "monitor_max": 5.0},
        "sample_count": int(metrics.get("set_logged_total", 0) or 0),
    }

    save_claim_metric = _compute_save_claim_slo(event_rows, window_start)
    repair_latency_metric = _compute_repair_latency_slo(event_rows, window_start)
    overall_status = _worst_status(
        unresolved_metric["status"],
        save_claim_metric["status"],
        repair_latency_metric["status"],
    )
    regressions = [
        metric["metric"]
        for metric in [unresolved_metric, save_claim_metric, repair_latency_metric]
        if metric["status"] != "healthy"
    ]

    return {
        "status": overall_status,
        "window_days": _SLO_LOOKBACK_DAYS,
        "evaluated_at": evaluated_at,
        "metrics": {
            unresolved_metric["metric"]: unresolved_metric,
            save_claim_metric["metric"]: save_claim_metric,
            repair_latency_metric["metric"]: repair_latency_metric,
        },
        "regressions": regressions,
    }


def _autonomy_policy_from_slos(
    integrity_slos: dict[str, Any],
    *,
    calibration_status: str = "healthy",
) -> dict[str, Any]:
    def confirmation_templates(status: str) -> dict[str, str]:
        if status == "degraded":
            return {
                "non_trivial_action": (
                    "Datenintegrität ist aktuell eingeschränkt. Soll ich fortfahren? "
                    "Bitte antworte mit JA, um diese Aktion explizit zu bestätigen."
                ),
                "plan_update": (
                    "Integritätsstatus ist degradiert. Planänderungen brauchen eine "
                    "explizite Bestätigung. Soll ich den Plan jetzt ändern?"
                ),
                "repair_action": (
                    "Automatische Reparaturen sind pausiert. Soll ich diese Reparatur "
                    "manuell mit deiner Bestätigung anwenden?"
                ),
                "post_save_followup": (
                    "Speichern ist verifiziert. Wegen degradiertem Integritätsstatus "
                    "frage ich vor weiteren nicht-trivialen Schritten immer explizit nach."
                ),
            }
        if status == "monitor":
            return {
                "non_trivial_action": (
                    "Integritätsstatus ist im Monitor-Bereich. Soll ich mit diesem "
                    "nächsten Schritt fortfahren?"
                ),
                "plan_update": (
                    "Monitor-Status aktiv: Bitte kurz bestätigen, dass ich die "
                    "Plananpassung durchführen soll."
                ),
                "repair_action": (
                    "Diese Reparatur ist als risikoarm eingestuft. Soll ich sie anwenden?"
                ),
                "post_save_followup": (
                    "Speichern ist verifiziert. Im Monitor-Status frage ich "
                    "nicht-triviale Folgeaktionen kurz nach."
                ),
            }
        return {
            "non_trivial_action": (
                "Wenn du willst, kann ich als nächsten Schritt direkt fortfahren."
            ),
            "plan_update": (
                "Wenn du willst, passe ich den Plan jetzt entsprechend an."
            ),
            "repair_action": (
                "Eine risikoarme Reparatur ist möglich. Soll ich sie ausführen?"
            ),
            "post_save_followup": "Speichern ist verifiziert.",
        }

    slo_status = str(integrity_slos.get("status", "healthy"))
    calibration_state = str(calibration_status or "healthy")
    effective_status = _worst_status(slo_status, calibration_state)

    if effective_status == "degraded":
        reason = (
            "Autonomy throttled: integrity/calibration status is degraded "
            f"(integrity={slo_status}, calibration={calibration_state})."
        )
        return {
            "policy_version": _AUTONOMY_POLICY_VERSION,
            "slo_status": slo_status,
            "calibration_status": calibration_state,
            "throttle_active": True,
            "max_scope_level": "strict",
            "require_confirmation_for_non_trivial_actions": True,
            "require_confirmation_for_plan_updates": True,
            "require_confirmation_for_repairs": True,
            "repair_auto_apply_enabled": False,
            "reason": reason,
            "confirmation_templates": confirmation_templates("degraded"),
        }

    if effective_status == "monitor":
        calibration_guard = calibration_state == "monitor"
        require_repair_confirmation = calibration_guard
        repair_auto_apply_enabled = not calibration_guard
        reason = (
            "Autonomy in monitor mode due to integrity/calibration signals "
            f"(integrity={slo_status}, calibration={calibration_state})."
        )
        return {
            "policy_version": _AUTONOMY_POLICY_VERSION,
            "slo_status": slo_status,
            "calibration_status": calibration_state,
            "throttle_active": True,
            "max_scope_level": "strict",
            "require_confirmation_for_non_trivial_actions": True,
            "require_confirmation_for_plan_updates": True,
            "require_confirmation_for_repairs": require_repair_confirmation,
            "repair_auto_apply_enabled": repair_auto_apply_enabled,
            "reason": reason,
            "confirmation_templates": confirmation_templates("monitor"),
        }

    return {
        "policy_version": _AUTONOMY_POLICY_VERSION,
        "slo_status": slo_status,
        "calibration_status": calibration_state,
        "throttle_active": False,
        "max_scope_level": "moderate",
        "require_confirmation_for_non_trivial_actions": False,
        "require_confirmation_for_plan_updates": False,
        "require_confirmation_for_repairs": False,
        "repair_auto_apply_enabled": True,
        "reason": (
            "Integrity and extraction calibration are healthy; autonomous repairs remain enabled."
        ),
        "confirmation_templates": confirmation_templates("healthy"),
    }


def _evaluate_read_only_invariants(
    event_rows: list[dict[str, Any]],
    alias_map: dict[str, str],
    import_job_rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate Phase-0 invariants from event data.

    Decision 13 references:
    - INV-001 (exercise identity resolution)
    - INV-003 (timezone explicitness)
    - INV-005 (goal trackability path)
    - INV-006 (baseline profile explicitness)
    - INV-009 (external import quality + dedup integrity)
    - INV-010 (session block logging completeness drift)
    """
    issues: list[dict[str, Any]] = []
    raw_set_rows = [r for r in event_rows if r["event_type"] == "set.logged"]
    set_correction_rows = [
        r for r in event_rows if r["event_type"] == "set.corrected"
    ]
    set_rows = apply_set_correction_chain(raw_set_rows, set_correction_rows)
    set_rows = [
        {**row, "data": row.get("effective_data") or row.get("data") or {}}
        for row in set_rows
    ]
    goal_rows = [r for r in event_rows if r["event_type"] == "goal.set"]
    prefs = _latest_preferences(event_rows)
    profile = _latest_profile(event_rows)
    active_custom_rules = _active_custom_rule_names(event_rows)
    mention_eval = evaluate_set_context_rows(set_rows)

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

    planning_event_types = {
        "training_plan.created",
        "training_plan.updated",
        "training_plan.archived",
        "projection_rule.created",
        "projection_rule.archived",
        "weight_target.set",
        "sleep_target.set",
        "nutrition_target.set",
    }
    planning_rows = [
        row for row in event_rows if row.get("event_type") in planning_event_types
    ]
    onboarding_closed = any(
        row.get("event_type") == "workflow.onboarding.closed" for row in event_rows
    )
    onboarding_override = any(
        row.get("event_type") == "workflow.onboarding.override_granted"
        for row in event_rows
    )
    if planning_rows and not onboarding_closed and not onboarding_override:
        sample_types = sorted(
            {str(row.get("event_type") or "") for row in planning_rows if row.get("event_type")}
        )
        issues.append(
            _issue(
                "INV-004",
                "onboarding_phase_violation",
                "medium",
                "Planning/coaching events were recorded before onboarding close without explicit override.",
                metrics={
                    "planning_event_count": len(planning_rows),
                    "sample_planning_event_types": sample_types,
                    "onboarding_closed": onboarding_closed,
                    "override_present": onboarding_override,
                },
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

    mention_missing_rows = [
        row for row in mention_eval if row.get("missing_fields")
    ]
    if mention_missing_rows:
        sample = mention_missing_rows[:3]
        hints = sorted(
            {
                hint
                for row in sample
                for hint in (row.get("hint_messages") or [])
            }
        )
        issues.append(
            _issue(
                "INV-008",
                "mention_field_missing",
                "medium",
                (
                    f"{len(mention_missing_rows)} set.logged rows contain mention-bound "
                    "context that was not persisted into structured fields."
                ),
                metrics={
                    "affected_rows": len(mention_missing_rows),
                    "sample_event_ids": [
                        row.get("event_id")
                        for row in sample
                        if row.get("event_id")
                    ],
                    "sample_missing_fields": sorted(
                        {
                            field
                            for row in sample
                            for field in (row.get("missing_fields") or [])
                        }
                    ),
                    "remediation_hints": hints,
                },
            )
        )

    session_rows = [r for r in event_rows if r.get("event_type") == "session.logged"]
    session_logged_total = len(session_rows)
    session_logged_invalid_total = 0
    session_missing_anchor_total = 0
    session_confidence_distribution = {"low": 0, "medium": 0, "high": 0}
    session_error_code_counts: dict[str, int] = {}
    for row in session_rows:
        payload = row.get("effective_data") or row.get("data") or {}
        if not isinstance(payload, dict):
            session_logged_invalid_total += 1
            session_confidence_distribution["low"] += 1
            continue
        completeness = evaluate_session_completeness(payload)
        confidence_value = float(completeness.get("confidence", 0.0) or 0.0)
        session_confidence_distribution[confidence_band(confidence_value)] += 1
        error_details = completeness.get("error_details") or []
        error_codes: set[str] = set()
        if isinstance(error_details, list):
            for entry in error_details:
                if not isinstance(entry, dict):
                    continue
                error_code = str(entry.get("error_code") or "").strip()
                if not error_code:
                    continue
                error_codes.add(error_code)
                session_error_code_counts[error_code] = (
                    session_error_code_counts.get(error_code, 0) + 1
                )
        if not bool(completeness.get("log_valid")):
            session_logged_invalid_total += 1
            if MISSING_ANCHOR_ERROR_CODES & error_codes:
                session_missing_anchor_total += 1

    session_missing_anchor_rate_pct = (
        round((session_missing_anchor_total / session_logged_total) * 100, 2)
        if session_logged_total
        else 0.0
    )
    if session_missing_anchor_total > 0:
        issues.append(
            _issue(
                "INV-010",
                "session_missing_anchor_rate",
                "medium",
                (
                    f"{session_missing_anchor_total}/{session_logged_total} session.logged events "
                    "failed anchor policy validation."
                ),
                metrics={
                    "session_logged_total": session_logged_total,
                    "session_missing_anchor_total": session_missing_anchor_total,
                    "session_missing_anchor_rate_pct": session_missing_anchor_rate_pct,
                },
            )
        )

    external_rows = [
        row for row in event_rows if row.get("event_type") == "external.activity_imported"
    ]
    external_imported_total = len(external_rows)
    external_low_confidence_fields = 0
    external_unsupported_fields_total = 0
    external_temporal_uncertainty_total = 0
    external_unit_conversion_fields = 0
    for row in external_rows:
        data = row.get("data") or {}
        provenance = data.get("provenance") or {}

        unsupported = provenance.get("unsupported_fields") or []
        if isinstance(unsupported, list):
            external_unsupported_fields_total += sum(
                1 for item in unsupported if isinstance(item, str) and item.strip()
            )

        warnings = provenance.get("warnings") or []
        if isinstance(warnings, list):
            external_temporal_uncertainty_total += sum(
                1
                for warning in warnings
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

    import_rows = import_job_rows or []
    external_import_job_total = len(import_rows)
    external_import_failed_total = sum(
        1 for row in import_rows if str(row.get("status") or "") == "failed"
    )
    external_import_error_class_counts: dict[str, int] = {}
    for row in import_rows:
        error_class = classify_import_error_code(row.get("error_code"))
        external_import_error_class_counts[error_class] = (
            external_import_error_class_counts.get(error_class, 0) + 1
        )
    external_import_parse_fail_total = sum(
        1
        for row in import_rows
        if str(row.get("status") or "") == "failed"
        and is_import_parse_quality_failure(row.get("error_code"))
    )
    external_import_parse_fail_rate_pct = (
        round((external_import_parse_fail_total / external_import_job_total) * 100, 2)
        if external_import_job_total
        else 0.0
    )
    external_dedup_skipped_total = 0
    external_dedup_rejected_total = 0
    for row in import_rows:
        receipt = row.get("receipt") or {}
        if isinstance(receipt, dict):
            write = receipt.get("write") or {}
            if isinstance(write, dict):
                write_result = str(write.get("result") or "").strip().lower()
                if write_result in {"duplicate_skipped", "idempotent_replay"}:
                    external_dedup_skipped_total += 1
        if str(row.get("status") or "") == "failed":
            error_code = str(row.get("error_code") or "").strip().lower()
            if error_code in {"stale_version", "version_conflict", "partial_overlap"}:
                external_dedup_rejected_total += 1

    if external_unsupported_fields_total > 0:
        issues.append(
            _issue(
                "INV-009",
                "external_unsupported_fields",
                "medium",
                (
                    f"External imports contain {external_unsupported_fields_total} unsupported "
                    "source fields that are excluded from canonical certainty."
                ),
                metrics={
                    "external_imported_total": external_imported_total,
                    "unsupported_fields_total": external_unsupported_fields_total,
                },
            )
        )

    if external_low_confidence_fields > 0:
        issues.append(
            _issue(
                "INV-009",
                "external_low_confidence_fields",
                "medium",
                (
                    f"{external_low_confidence_fields} external mapped fields are low-confidence "
                    "or explicitly non-mapped."
                ),
                metrics={
                    "external_imported_total": external_imported_total,
                    "external_low_confidence_fields": external_low_confidence_fields,
                },
            )
        )

    if external_temporal_uncertainty_total > 0:
        issues.append(
            _issue(
                "INV-009",
                "external_temporal_uncertainty",
                "low",
                (
                    f"External imports reported {external_temporal_uncertainty_total} temporal "
                    "uncertainty hints (timezone/drift)."
                ),
                metrics={
                    "external_temporal_uncertainty_total": external_temporal_uncertainty_total,
                },
            )
        )

    if external_dedup_rejected_total > 0:
        issues.append(
            _issue(
                "INV-009",
                "external_dedup_rejected",
                "medium",
                (
                    f"{external_dedup_rejected_total} import jobs were rejected by dedup policy "
                    "(stale/conflict/partial overlap)."
                ),
                metrics={
                    "external_dedup_rejected_total": external_dedup_rejected_total,
                    "external_import_failed_total": external_import_failed_total,
                },
            )
        )
    if external_import_parse_fail_total > 0:
        issues.append(
            _issue(
                "INV-009",
                "external_parse_fail_rate",
                "medium",
                (
                    f"{external_import_parse_fail_total}/{external_import_job_total} import jobs "
                    "failed in parse/validation/mapping stage."
                ),
                metrics={
                    "external_import_job_total": external_import_job_total,
                    "external_import_parse_fail_total": external_import_parse_fail_total,
                    "external_import_parse_fail_rate_pct": external_import_parse_fail_rate_pct,
                },
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
        "onboarding_closed": onboarding_closed,
        "onboarding_override_present": onboarding_override,
        "planning_event_total": len(planning_rows),
        "mention_field_missing_total": len(mention_missing_rows),
        "session_logged_total": session_logged_total,
        "session_logged_invalid_total": session_logged_invalid_total,
        "session_missing_anchor_total": session_missing_anchor_total,
        "session_missing_anchor_rate_pct": session_missing_anchor_rate_pct,
        "session_confidence_distribution": session_confidence_distribution,
        "session_error_code_counts": session_error_code_counts,
        "external_imported_total": external_imported_total,
        "external_import_job_total": external_import_job_total,
        "external_import_failed_total": external_import_failed_total,
        "external_import_parse_fail_total": external_import_parse_fail_total,
        "external_import_parse_fail_rate_pct": external_import_parse_fail_rate_pct,
        "external_import_error_class_counts": external_import_error_class_counts,
        "external_dedup_skipped_total": external_dedup_skipped_total,
        "external_dedup_rejected_total": external_dedup_rejected_total,
        "external_low_confidence_fields": external_low_confidence_fields,
        "external_unsupported_fields_total": external_unsupported_fields_total,
        "external_temporal_uncertainty_total": external_temporal_uncertainty_total,
        "external_unit_conversion_fields": external_unit_conversion_fields,
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
    proposals: list[dict[str, Any]] | None = None,
    *,
    repair_apply_enabled: bool = False,
    repair_apply_gate: str | None = None,
    repair_apply_results: list[dict[str, Any]] | None = None,
    decision_phase: str = "phase_1_assisted_repairs",
    last_repair_at: str | None = None,
    integrity_slos: dict[str, Any] | None = None,
    autonomy_policy: dict[str, Any] | None = None,
    extraction_calibration: dict[str, Any] | None = None,
    schema_capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    score = _compute_quality_score(issues)
    quality_status = _status_from_score(score, issues)
    integrity_status = str((integrity_slos or {}).get("status", "healthy"))
    status = _worst_status(quality_status, integrity_status)
    sev_counts = Counter(issue["severity"] for issue in issues)
    issues_by_severity = {
        "high": sev_counts.get("high", 0),
        "medium": sev_counts.get("medium", 0),
        "low": sev_counts.get("low", 0),
        "info": sev_counts.get("info", 0),
    }

    sorted_issues = _sort_issues(issues)

    top_issues = [
        {
            "issue_id": issue["issue_id"],
            "type": issue["type"],
            "severity": issue["severity"],
            "invariant_id": issue["invariant_id"],
        }
        for issue in sorted_issues[:5]
    ]

    if proposals is None:
        proposals = _build_simulated_repair_proposals(sorted_issues, evaluated_at)
    proposals_by_issue = {proposal["issue_id"]: proposal for proposal in proposals}
    proposals_by_state = Counter(
        proposal.get("state", _REPAIR_STATE_PROPOSED)
        for proposal in proposals
    )
    apply_ready = [p["proposal_id"] for p in proposals if p.get("safe_for_apply")]
    confidence_bands = {
        proposal["proposal_id"]: _proposal_confidence_band(proposal)
        for proposal in proposals
    }
    high_confidence_ids = [
        proposal_id
        for proposal_id, band in confidence_bands.items()
        if band == "high"
    ]
    low_confidence_ids = [
        proposal_id
        for proposal_id, band in confidence_bands.items()
        if band == "low"
    ]

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
        "quality_status": quality_status,
        "integrity_slo_status": integrity_status,
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
            _REPAIR_STATE_APPLIED: proposals_by_state.get(_REPAIR_STATE_APPLIED, 0),
            _REPAIR_STATE_AUTO_APPLY_REJECTED: proposals_by_state.get(
                _REPAIR_STATE_AUTO_APPLY_REJECTED, 0
            ),
            _REPAIR_STATE_VERIFIED_CLOSED: proposals_by_state.get(
                _REPAIR_STATE_VERIFIED_CLOSED, 0
            ),
        },
        "repair_apply_ready_ids": apply_ready,
        "repair_confidence_filters": {
            "high_confidence_ids": high_confidence_ids,
            "low_confidence_ids": low_confidence_ids,
        },
        "repair_apply_enabled": repair_apply_enabled,
        "repair_apply_gate": repair_apply_gate
        or "tier_a_only_and_state_simulated_safe; auto-apply disabled in phase_1",
        "simulate_bridge": {
            "target_endpoint": _SIMULATE_ENDPOINT,
            "engine": "worker_simulate_bridge_v1",
            "decision_phase": decision_phase,
        },
        "invariant_mode": (
            "policy_gated_auto_apply"
            if decision_phase.startswith("phase_2")
            else "read_only"
        ),
        "invariants_evaluated": [
            "INV-001",
            "INV-003",
            "INV-005",
            "INV-006",
            "INV-008",
        ],
        "metrics": metrics,
        "integrity_slos": integrity_slos or {
            "status": "healthy",
            "window_days": _SLO_LOOKBACK_DAYS,
            "evaluated_at": evaluated_at,
            "metrics": {},
            "regressions": [],
        },
        "extraction_calibration": extraction_calibration
        or {
            "status": "healthy",
            "period_key": None,
            "classes_total": 0,
            "degraded_count": 0,
            "monitor_count": 0,
            "drift_alert_count": 0,
            "underperforming_classes": [],
        },
        "autonomy_policy": autonomy_policy
        or _autonomy_policy_from_slos(
            {"status": "healthy"},
            calibration_status=(
                extraction_calibration or {}
            ).get("status", "healthy"),
        ),
        "schema_capabilities": schema_capabilities
        or {
            "status": "healthy",
            "checked_at": evaluated_at,
            "missing_relations": [],
            "relations": {},
        },
        "last_repair_at": last_repair_at,
        "repair_apply_results": repair_apply_results or [],
        "repair_apply_results_total": len(repair_apply_results or []),
        "repair_apply_results_by_decision": {
            "applied": sum(
                1
                for result in (repair_apply_results or [])
                if result.get("decision") == "applied"
            ),
            "rejected": sum(
                1
                for result in (repair_apply_results or [])
                if result.get("decision") == "rejected"
            ),
        },
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
        "quality_repair_applied": data.get("repair_apply_results_by_decision", {}).get(
            "applied"
        ),
        "quality_last_repair_at": data.get("last_repair_at"),
        "quality_integrity_slo_status": data.get("integrity_slo_status"),
        "quality_autonomy_throttle_active": data.get("autonomy_policy", {}).get(
            "throttle_active"
        ),
    }


@projection_handler(
    *_EVENT_TYPES,
    dimension_meta={
        "name": "quality_health",
        "description": (
            "Invariant health + policy-gated repair proposals for Decision 13. "
            "Tier A deterministic repairs may auto-apply and are always evented."
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
            "quality_status": "string — healthy|monitor|degraded",
            "integrity_slo_status": "string — healthy|monitor|degraded",
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
                    "string — proposed|simulated_safe|simulated_risky|rejected|applied|auto_apply_rejected|verified_closed"
                ),
                "safe_for_apply": "boolean",
                "auto_apply_eligible": "boolean",
                "rationale": "string",
                "assumptions": ["string"],
                "repair_provenance": {
                    "entries": [{
                        "source_type": "string — explicit|inferred|estimated|user_confirmed",
                        "confidence": "number 0..1",
                        "confidence_band": "string — low|medium|high",
                        "applies_scope": "string — single_set|exercise_session|session",
                        "reason": "string",
                    }],
                    "summary": "object",
                },
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
                "applied": "integer",
                "auto_apply_rejected": "integer",
                "verified_closed": "integer",
            },
            "repair_apply_ready_ids": ["string"],
            "repair_confidence_filters": {
                "high_confidence_ids": ["string"],
                "low_confidence_ids": ["string"],
            },
            "repair_apply_enabled": "boolean",
            "repair_apply_gate": "string",
            "repair_apply_results": [{
                "proposal_id": "string",
                "issue_id": "string",
                "decision": "string — applied|rejected",
                "reason_code": "string",
                "repair_events_inserted": "integer",
                "repair_events_preexisting": "integer",
                "proposal_state_after_verify": "string | null",
            }],
            "repair_apply_results_total": "integer",
            "repair_apply_results_by_decision": {
                "applied": "integer",
                "rejected": "integer",
            },
            "simulate_bridge": {
                "target_endpoint": "string",
                "engine": "string",
                "decision_phase": "string",
            },
            "invariant_mode": "string — read_only|policy_gated_auto_apply",
            "invariants_evaluated": ["string"],
            "metrics": "object",
            "integrity_slos": {
                "status": "string — healthy|monitor|degraded",
                "window_days": "integer",
                "regressions": ["string"],
                "metrics": {
                    "unresolved_set_logged_pct": "object",
                    "save_claim_mismatch_rate_pct": "object",
                    "repair_latency_hours_p50": "object",
                },
            },
            "extraction_calibration": {
                "status": "string — healthy|monitor|degraded",
                "period_key": "string | null (ISO week key)",
                "classes_total": "integer",
                "degraded_count": "integer",
                "monitor_count": "integer",
                "drift_alert_count": "integer",
                "underperforming_classes": [{
                    "claim_class": "string",
                    "parser_version": "string",
                    "status": "string",
                    "drift_status": "string",
                    "brier_score": "number",
                    "precision_high_conf": "number | null",
                    "sample_count": "integer",
                }],
            },
            "autonomy_policy": {
                "policy_version": "string",
                "slo_status": "string",
                "calibration_status": "string — healthy|monitor|degraded",
                "throttle_active": "boolean",
                "max_scope_level": "string — strict|moderate|proactive",
                "require_confirmation_for_non_trivial_actions": "boolean",
                "require_confirmation_for_plan_updates": "boolean",
                "require_confirmation_for_repairs": "boolean",
                "repair_auto_apply_enabled": "boolean",
                "reason": "string",
                "confirmation_templates": {
                    "non_trivial_action": "string",
                    "plan_update": "string",
                    "repair_action": "string",
                    "post_save_followup": "string",
                },
            },
            "schema_capabilities": {
                "status": "string — healthy|degraded",
                "checked_at": "ISO 8601 datetime",
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
            "last_repair_at": "ISO 8601 datetime | null",
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
    rows = await _load_quality_source_rows(conn, user_id)
    rows = [r for r in rows if str(r["id"]) not in retracted_ids]
    relation_capabilities = await detect_relation_capabilities(
        conn,
        ["external_import_jobs"],
    )
    schema_capabilities = build_schema_capability_report(relation_capabilities)
    import_job_rows: list[dict[str, Any]] = []
    if relation_capabilities.get("external_import_jobs", False):
        import_job_rows = await _load_external_import_job_rows(conn, user_id)
    else:
        logger.warning(
            (
                "quality_health schema capability degraded: "
                "missing relation external_import_jobs for user=%s"
            ),
            user_id,
        )

    if not rows:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM projections WHERE user_id = %s AND projection_type = 'quality_health' AND key = 'overview'",
                (user_id,),
            )
        return

    alias_map = await get_alias_map(conn, user_id, retracted_ids=retracted_ids)
    issues, metrics = _evaluate_read_only_invariants(
        rows,
        alias_map,
        import_job_rows=import_job_rows,
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    simulated_proposals = _build_simulated_repair_proposals(issues, now_iso)
    detection_telemetry_events = _build_detection_learning_signal_events(
        str(user_id),
        issues,
        simulated_proposals,
        now_iso,
        source_anchor=str(rows[-1]["id"]),
    )
    await _insert_events_with_idempotency_guard(
        conn,
        user_id,
        detection_telemetry_events,
    )
    # Tier A repairs are deterministic and safe by definition — always allow them.
    # SLO degradation gates agent behavior (coaching, planning), not the system's
    # own repair machinery. Blocking Tier A when degraded creates a bootstrap
    # deadlock where the system can never heal itself.
    apply_cycle = await _auto_apply_tier_a_repairs(
        conn,
        user_id,
        simulated_proposals,
        now_iso,
        allow_tier_a_auto_apply=True,
    )
    apply_results = apply_cycle["results"]

    has_applied = any(result.get("decision") == "applied" for result in apply_results)
    last_repair_at: str | None = None
    if has_applied:
        retracted_ids = await get_retracted_event_ids(conn, user_id)
        rows = await _load_quality_source_rows(conn, user_id)
        rows = [r for r in rows if str(r["id"]) not in retracted_ids]
        if not rows:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM projections WHERE user_id = %s AND projection_type = 'quality_health' AND key = 'overview'",
                    (user_id,),
                )
            return
        alias_map = await get_alias_map(conn, user_id, retracted_ids=retracted_ids)
        issues, metrics = _evaluate_read_only_invariants(
            rows,
            alias_map,
            import_job_rows=import_job_rows,
        )
        verified_at = datetime.now(timezone.utc).isoformat()
        await _verify_applied_repairs(
            conn,
            user_id,
            apply_results,
            open_issue_ids={str(issue["issue_id"]) for issue in issues},
            verified_at=verified_at,
        )
        last_repair_at = verified_at
        now_iso = verified_at

    final_integrity_slos = _compute_integrity_slos(rows, metrics, now_iso)
    extraction_calibration = await resolve_extraction_calibration_status(conn)
    final_autonomy_policy = _autonomy_policy_from_slos(
        final_integrity_slos,
        calibration_status=str(extraction_calibration.get("status", "healthy")),
    )
    final_proposals = _build_simulated_repair_proposals(issues, now_iso)
    projection_data = _build_quality_projection_data(
        issues,
        metrics,
        now_iso,
        proposals=final_proposals,
        repair_apply_enabled=bool(
            final_autonomy_policy.get("repair_auto_apply_enabled", True)
        ),
        repair_apply_gate=_AUTO_APPLY_POLICY_GATE,
        repair_apply_results=apply_results,
        decision_phase="phase_2_autonomous_tier_a",
        last_repair_at=last_repair_at,
        integrity_slos=final_integrity_slos,
        autonomy_policy=final_autonomy_policy,
        extraction_calibration=extraction_calibration,
        schema_capabilities=schema_capabilities,
    )
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
        "Updated quality_health for user=%s (status=%s score=%.3f issues=%d applied=%d rejected=%d)",
        user_id,
        projection_data["status"],
        projection_data["score"],
        projection_data["issues_open"],
        projection_data["repair_apply_results_by_decision"]["applied"],
        projection_data["repair_apply_results_by_decision"]["rejected"],
    )
