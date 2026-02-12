"""Learning-to-backlog bridge + regression promotion candidates (2zc.3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import logging
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LearningBacklogBridgeSettings:
    cluster_min_score: float
    cluster_min_events: int
    cluster_min_unique_users: int
    calibration_min_sample_count: int
    unknown_dimension_min_score: float
    max_candidates_per_source: int
    max_candidates_per_run: int


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(minimum, int(raw))
    except ValueError:
        return max(minimum, default)


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        parsed = float(raw)
    except ValueError:
        parsed = default
    return min(maximum, max(minimum, parsed))


def learning_backlog_bridge_settings() -> LearningBacklogBridgeSettings:
    return LearningBacklogBridgeSettings(
        cluster_min_score=_float_env(
            "KURA_LEARNING_BACKLOG_CLUSTER_MIN_SCORE", 0.18, 0.0, 1.0
        ),
        cluster_min_events=_int_env("KURA_LEARNING_BACKLOG_CLUSTER_MIN_EVENTS", 3, 1),
        cluster_min_unique_users=_int_env(
            "KURA_LEARNING_BACKLOG_CLUSTER_MIN_UNIQUE_USERS", 2, 1
        ),
        calibration_min_sample_count=_int_env(
            "KURA_LEARNING_BACKLOG_CALIBRATION_MIN_SAMPLES", 3, 1
        ),
        unknown_dimension_min_score=_float_env(
            "KURA_LEARNING_BACKLOG_UNKNOWN_DIMENSION_MIN_SCORE", 0.18, 0.0, 1.0
        ),
        max_candidates_per_source=_int_env(
            "KURA_LEARNING_BACKLOG_MAX_CANDIDATES_PER_SOURCE", 6, 1
        ),
        max_candidates_per_run=_int_env(
            "KURA_LEARNING_BACKLOG_MAX_CANDIDATES_PER_RUN", 12, 1
        ),
    )


def _normalize_text(value: Any, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    return normalized if normalized else fallback


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _stable_candidate_key(prefix: str, source_ref: str) -> str:
    digest = hashlib.sha1(f"{prefix}:{source_ref}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{source_ref}:{digest}"


def _priority_label(score: float) -> str:
    if score >= 0.60:
        return "P1"
    if score >= 0.35:
        return "P2"
    return "P3"


def _priority_from_cluster(score: float, event_count: int, unique_users: int) -> float:
    coverage_bonus = min(0.20, (event_count / 20.0) * 0.10 + (unique_users / 8.0) * 0.10)
    return round(_clamp01(score + coverage_bonus), 6)


def _priority_from_calibration(
    *,
    status: str,
    brier_score: float,
    precision_high_conf: float | None,
    sample_count: int,
) -> float:
    brier_component = min(1.0, max(0.0, brier_score) / 0.35)
    if precision_high_conf is None:
        precision_component = 0.50
    else:
        precision_component = _clamp01(1.0 - max(0.0, precision_high_conf))
    sample_component = min(1.0, max(0, sample_count) / 20.0)
    drift_boost = 0.12 if status == "drift_alert" else 0.0
    score = (
        0.45 * brier_component
        + 0.35 * precision_component
        + 0.20 * sample_component
        + drift_boost
    )
    return round(_clamp01(score), 6)


def _extract_invariant_suggestions(cluster_data: dict[str, Any]) -> list[dict[str, str]]:
    representatives = cluster_data.get("representative_examples")
    if not isinstance(representatives, list):
        return []
    ranked: list[str] = []
    seen: set[str] = set()
    for row in representatives:
        if not isinstance(row, dict):
            continue
        invariant_id = _normalize_text(row.get("invariant_id"), "none")
        if invariant_id == "none" or invariant_id in seen:
            continue
        seen.add(invariant_id)
        ranked.append(invariant_id)
    return [
        {
            "invariant_id": invariant_id,
            "action": "tighten_detection_or_guardrail",
        }
        for invariant_id in ranked[:2]
    ]


def _build_promotion_checklist(
    *,
    root_cause_hypothesis: str,
    suggested_updates: dict[str, Any],
) -> dict[str, Any]:
    has_root_cause = bool(_normalize_text(root_cause_hypothesis))
    has_update_mapping = bool(
        (suggested_updates.get("invariant_updates") or [])
        or (suggested_updates.get("policy_updates") or [])
    )
    has_regression_plan = bool(suggested_updates.get("regression_tests") or [])

    steps = [
        {
            "id": "human_approval_gate",
            "description": "Human approves candidate-to-issue promotion.",
            "automation": "manual",
            "status": "pending",
        },
        {
            "id": "root_cause_hypothesis_attached",
            "description": "Root-cause hypothesis is present and explicit.",
            "automation": "auto",
            "status": "completed" if has_root_cause else "pending",
        },
        {
            "id": "invariant_policy_mapping",
            "description": "Candidate includes invariant/policy update mapping.",
            "automation": "auto",
            "status": "completed" if has_update_mapping else "pending",
        },
        {
            "id": "regression_test_plan",
            "description": "Deterministic regression test plan is attached.",
            "automation": "auto",
            "status": "completed" if has_regression_plan else "pending",
        },
        {
            "id": "regression_test_implementation",
            "description": "Regression test implemented and passing in CI.",
            "automation": "manual",
            "status": "pending",
        },
        {
            "id": "shadow_re_evaluation",
            "description": "Shadow evaluation run confirms no policy regressions.",
            "automation": "manual",
            "status": "pending",
        },
    ]
    auto_total = sum(1 for step in steps if step["automation"] == "auto")
    auto_completed = sum(
        1
        for step in steps
        if step["automation"] == "auto" and step["status"] == "completed"
    )
    return {
        "workflow": "cluster_to_issue_to_regression_v1",
        "steps": steps,
        "auto_completed_steps": auto_completed,
        "auto_total_steps": auto_total,
    }


def _cluster_candidate(row: dict[str, Any]) -> dict[str, Any]:
    cluster_signature = _normalize_text(row.get("cluster_signature"), "unknown_cluster")
    period_key = _normalize_text(row.get("period_key"), "unknown")
    cluster_data = row.get("cluster_data")
    if not isinstance(cluster_data, dict):
        cluster_data = {}

    event_count = max(0, _safe_int(row.get("event_count"), 0))
    unique_users = max(0, _safe_int(row.get("unique_users"), 0))
    cluster_score = _clamp01(_safe_float(row.get("score"), 0.0))
    signal_type = _normalize_text(
        ((cluster_data.get("signature") or {}).get("signal_type_top")),
        "quality_issue_detected",
    )

    priority_score = _priority_from_cluster(cluster_score, event_count, unique_users)
    invariant_updates = _extract_invariant_suggestions(cluster_data)
    policy_updates = [
        {
            "policy_area": "quality_health.autonomy_policy",
            "action": f"review thresholding for recurring signal '{signal_type}'",
        }
    ]
    regression_tests = [
        {
            "suite": "workers/tests/test_integration.py",
            "scenario": f"learning_backlog_bridge_{cluster_signature}",
            "expected": "candidate_generated_with_dedupe_guardrails",
        }
    ]
    suggested_updates = {
        "invariant_updates": invariant_updates,
        "policy_updates": policy_updates,
        "regression_tests": regression_tests,
    }

    root_cause_hypothesis = (
        f"Recurring '{signal_type}' cluster '{cluster_signature}' indicates a missing "
        "or weak invariant/policy guard that allows repeated reliability failures."
    )
    impacted_metrics = {
        "event_count": event_count,
        "unique_users": unique_users,
        "cluster_score": cluster_score,
        "score_factors": cluster_data.get("score_factors") or {},
        "affected_workflow_phases": cluster_data.get("affected_workflow_phases") or [],
    }
    promotion_checklist = _build_promotion_checklist(
        root_cause_hypothesis=root_cause_hypothesis,
        suggested_updates=suggested_updates,
    )
    title = (
        f"[learning] recurring {signal_type} cluster "
        f"({event_count} events/{unique_users} users)"
    )
    issue_payload = {
        "schema_version": 1,
        "approval_required": True,
        "type": "feature",
        "priority": _priority_label(priority_score),
        "labels": ["learning-loop", "backlog-bridge", "regression-promotion"],
        "title": title,
        "description": (
            f"Auto-suggested from weekly learning cluster '{cluster_signature}' "
            f"(week={period_key})."
        ),
        "design": (
            "Follow promotion checklist: map cluster to invariant/policy deltas, "
            "add regression test, and run shadow re-evaluation before rollout."
        ),
        "acceptance_criteria": [
            "Root-cause hypothesis is validated or falsified with event evidence.",
            "Invariant/policy update is implemented with clear rollback path.",
            "Regression test reproduces pre-fix failure and passes post-fix.",
            "Shadow evaluation confirms no regression in autonomy quality metrics.",
        ],
        "source": {
            "source_type": "issue_cluster",
            "source_period_key": period_key,
            "source_ref": cluster_signature,
        },
        "root_cause_hypothesis": root_cause_hypothesis,
        "impacted_metrics": impacted_metrics,
        "suggested_updates": suggested_updates,
        "promotion_checklist": promotion_checklist,
    }
    return {
        "candidate_key": _stable_candidate_key("cluster", cluster_signature),
        "source_type": "issue_cluster",
        "source_period_key": period_key,
        "source_ref": cluster_signature,
        "priority_score": priority_score,
        "title": title,
        "root_cause_hypothesis": root_cause_hypothesis,
        "impacted_metrics": impacted_metrics,
        "suggested_updates": suggested_updates,
        "promotion_checklist": promotion_checklist,
        "issue_payload": issue_payload,
        "guardrails": {},
        "approval_required": True,
    }


def _calibration_candidate(row: dict[str, Any]) -> dict[str, Any]:
    period_key = _normalize_text(row.get("period_key"), "unknown")
    claim_class = _normalize_text(row.get("claim_class"), "unknown_claim_class")
    parser_version = _normalize_text(row.get("parser_version"), "unknown_parser")
    status = _normalize_text(row.get("status"), "underperforming")
    brier_score = max(0.0, _safe_float(row.get("brier_score"), 0.0))
    precision_raw = row.get("precision_high_conf")
    precision_high_conf = (
        _clamp01(_safe_float(precision_raw, 0.0)) if precision_raw is not None else None
    )
    sample_count = max(0, _safe_int(row.get("sample_count"), 0))
    details = row.get("details")
    if not isinstance(details, dict):
        details = {}

    priority_score = _priority_from_calibration(
        status=status,
        brier_score=brier_score,
        precision_high_conf=precision_high_conf,
        sample_count=sample_count,
    )
    root_cause_hypothesis = (
        f"Extraction class '{claim_class}' ({parser_version}) is underperforming "
        f"({status}), suggesting calibration drift or parser ambiguity."
    )
    suggested_updates = {
        "invariant_updates": [
            {
                "invariant_id": "INV-008",
                "action": f"tighten mention-bound persistence checks for '{claim_class}'",
            }
        ],
        "policy_updates": [
            {
                "policy_area": "agent.autonomy.repair",
                "action": (
                    f"review auto-repair confidence gate for parser '{parser_version}' "
                    f"and class '{claim_class}'"
                ),
            }
        ],
        "regression_tests": [
            {
                "suite": "workers/tests/test_integration.py",
                "scenario": f"calibration_bridge_{claim_class}_{parser_version}",
                "expected": "underperforming class produces backlog candidate",
            }
        ],
    }
    impacted_metrics = {
        "claim_class": claim_class,
        "parser_version": parser_version,
        "status": status,
        "brier_score": brier_score,
        "precision_high_conf": precision_high_conf,
        "sample_count": sample_count,
        "details": details,
    }
    promotion_checklist = _build_promotion_checklist(
        root_cause_hypothesis=root_cause_hypothesis,
        suggested_updates=suggested_updates,
    )
    source_ref = f"{claim_class}:{parser_version}:{status}"
    title = (
        f"[learning] extraction calibration regression "
        f"({claim_class}, {parser_version})"
    )
    issue_payload = {
        "schema_version": 1,
        "approval_required": True,
        "type": "feature",
        "priority": _priority_label(priority_score),
        "labels": [
            "learning-loop",
            "backlog-bridge",
            "extraction-calibration",
            "regression-promotion",
        ],
        "title": title,
        "description": (
            f"Auto-suggested from extraction calibration report (week={period_key}, status={status})."
        ),
        "design": (
            "Promote calibration findings into parser/policy/test updates with "
            "shadow re-evaluation before enabling aggressive auto-repair."
        ),
        "acceptance_criteria": [
            "Parser/prompt adjustments improve calibration metrics for target class.",
            "Quality policy gating reflects post-fix calibration status.",
            "Regression test captures previous extraction failure mode.",
            "Shadow evaluation confirms no new false-positive inflation.",
        ],
        "source": {
            "source_type": "extraction_calibration",
            "source_period_key": period_key,
            "source_ref": source_ref,
        },
        "root_cause_hypothesis": root_cause_hypothesis,
        "impacted_metrics": impacted_metrics,
        "suggested_updates": suggested_updates,
        "promotion_checklist": promotion_checklist,
    }
    return {
        "candidate_key": _stable_candidate_key("calibration", source_ref),
        "source_type": "extraction_calibration",
        "source_period_key": period_key,
        "source_ref": source_ref,
        "priority_score": priority_score,
        "title": title,
        "root_cause_hypothesis": root_cause_hypothesis,
        "impacted_metrics": impacted_metrics,
        "suggested_updates": suggested_updates,
        "promotion_checklist": promotion_checklist,
        "issue_payload": issue_payload,
        "guardrails": {},
        "approval_required": True,
    }


def _unknown_dimension_candidate(row: dict[str, Any]) -> dict[str, Any]:
    period_key = _normalize_text(row.get("period_key"), "unknown")
    proposal_key = _normalize_text(row.get("proposal_key"), "unknown_proposal")
    proposal_score = _clamp01(_safe_float(row.get("proposal_score"), 0.0))
    confidence = _clamp01(_safe_float(row.get("confidence"), 0.0))
    event_count = max(0, _safe_int(row.get("event_count"), 0))
    unique_users = max(0, _safe_int(row.get("unique_users"), 0))
    suggested_dimension = row.get("suggested_dimension")
    if not isinstance(suggested_dimension, dict):
        suggested_dimension = {}
    evidence_bundle = row.get("evidence_bundle")
    if not isinstance(evidence_bundle, dict):
        evidence_bundle = {}
    risk_notes = row.get("risk_notes")
    if not isinstance(risk_notes, list):
        risk_notes = []

    name = _normalize_text(suggested_dimension.get("name"), "unknown_dimension")
    value_type = _normalize_text(suggested_dimension.get("value_type"), "unknown")
    priority_score = round(
        _clamp01(
            max(
                proposal_score,
                (0.65 * proposal_score) + (0.35 * confidence),
            )
        ),
        6,
    )
    root_cause_hypothesis = (
        f"Unknown observation cluster '{proposal_key}' recurs across users and suggests "
        f"a missing dimension contract for '{name}'."
    )
    suggested_updates = {
        "invariant_updates": [
            {
                "invariant_id": "INV-008",
                "action": (
                    f"extend mention-bound extraction coverage for new dimension '{name}' "
                    f"({value_type})"
                ),
            }
        ],
        "policy_updates": [
            {
                "policy_area": "open_observation.contract_registry",
                "action": "add accepted unknown-dimension proposal to known/provisional registry",
            }
        ],
        "regression_tests": [
            {
                "suite": "workers/tests/test_integration.py",
                "scenario": f"unknown_dimension_bridge_{name}",
                "expected": "accepted proposal routes to backlog candidate with dedupe",
            }
        ],
    }
    impacted_metrics = {
        "proposal_score": proposal_score,
        "confidence": confidence,
        "event_count": event_count,
        "unique_users": unique_users,
        "suggested_dimension": suggested_dimension,
        "evidence_bundle": evidence_bundle,
        "risk_notes": risk_notes,
    }
    promotion_checklist = _build_promotion_checklist(
        root_cause_hypothesis=root_cause_hypothesis,
        suggested_updates=suggested_updates,
    )
    title = (
        f"[learning] promote unknown dimension proposal "
        f"({name}, confidence={confidence:.2f})"
    )
    issue_payload = {
        "schema_version": 1,
        "approval_required": True,
        "type": "feature",
        "priority": _priority_label(priority_score),
        "labels": [
            "learning-loop",
            "backlog-bridge",
            "unknown-dimension",
            "regression-promotion",
        ],
        "title": title,
        "description": (
            f"Auto-suggested from accepted unknown-dimension proposal '{proposal_key}' "
            f"(week={period_key})."
        ),
        "design": (
            "Convert accepted unknown-dimension proposal into explicit event/schema "
            "contract updates and enforce with regression + shadow evaluation."
        ),
        "acceptance_criteria": [
            "New dimension contract is defined with type/unit/scale semantics.",
            "Relevant parsing/projection path persists the dimension deterministically.",
            "Regression tests validate no fallback to unknown tier for this pattern.",
            "Shadow evaluation confirms no noisy duplicate candidate emissions.",
        ],
        "source": {
            "source_type": "unknown_dimension",
            "source_period_key": period_key,
            "source_ref": proposal_key,
        },
        "root_cause_hypothesis": root_cause_hypothesis,
        "impacted_metrics": impacted_metrics,
        "suggested_updates": suggested_updates,
        "promotion_checklist": promotion_checklist,
    }
    return {
        "candidate_key": _stable_candidate_key("unknown_dimension", proposal_key),
        "source_type": "unknown_dimension",
        "source_period_key": period_key,
        "source_ref": proposal_key,
        "priority_score": priority_score,
        "title": title,
        "root_cause_hypothesis": root_cause_hypothesis,
        "impacted_metrics": impacted_metrics,
        "suggested_updates": suggested_updates,
        "promotion_checklist": promotion_checklist,
        "issue_payload": issue_payload,
        "guardrails": {},
        "approval_required": True,
    }


def build_backlog_candidates(
    *,
    cluster_rows: list[dict[str, Any]],
    underperforming_rows: list[dict[str, Any]],
    unknown_dimension_rows: list[dict[str, Any]] | None = None,
    settings: LearningBacklogBridgeSettings,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    unknown_dimension_rows = unknown_dimension_rows or []
    candidates: list[dict[str, Any]] = []
    filtered_noise = 0
    cluster_candidates = 0
    calibration_candidates = 0
    unknown_candidates = 0

    for row in cluster_rows:
        event_count = max(0, _safe_int(row.get("event_count"), 0))
        unique_users = max(0, _safe_int(row.get("unique_users"), 0))
        score = _clamp01(_safe_float(row.get("score"), 0.0))
        if (
            score < settings.cluster_min_score
            or event_count < settings.cluster_min_events
            or unique_users < settings.cluster_min_unique_users
        ):
            filtered_noise += 1
            continue
        candidates.append(_cluster_candidate(row))
        cluster_candidates += 1

    for row in underperforming_rows:
        sample_count = max(0, _safe_int(row.get("sample_count"), 0))
        if sample_count < settings.calibration_min_sample_count:
            filtered_noise += 1
            continue
        candidates.append(_calibration_candidate(row))
        calibration_candidates += 1

    for row in unknown_dimension_rows:
        proposal_score = _clamp01(_safe_float(row.get("proposal_score"), 0.0))
        if proposal_score < settings.unknown_dimension_min_score:
            filtered_noise += 1
            continue
        candidates.append(_unknown_dimension_candidate(row))
        unknown_candidates += 1

    best_by_key: dict[str, dict[str, Any]] = {}
    duplicates_in_run = 0
    for candidate in candidates:
        key = candidate["candidate_key"]
        current = best_by_key.get(key)
        if current is None:
            best_by_key[key] = candidate
            continue
        duplicates_in_run += 1
        if float(candidate["priority_score"]) > float(current["priority_score"]):
            best_by_key[key] = candidate

    deduped = sorted(
        best_by_key.values(),
        key=lambda item: (
            -float(item["priority_score"]),
            item["source_type"],
            item["candidate_key"],
        ),
    )

    by_source_kept: dict[str, int] = {}
    limited_by_source = 0
    limited_by_run_cap = 0
    limited: list[dict[str, Any]] = []
    for candidate in deduped:
        source = str(candidate["source_type"])
        if by_source_kept.get(source, 0) >= settings.max_candidates_per_source:
            limited_by_source += 1
            continue
        if len(limited) >= settings.max_candidates_per_run:
            limited_by_run_cap += 1
            continue
        by_source_kept[source] = by_source_kept.get(source, 0) + 1
        limited.append(candidate)

    stats = {
        "cluster_candidates": cluster_candidates,
        "calibration_candidates": calibration_candidates,
        "unknown_candidates": unknown_candidates,
        "filtered_noise": filtered_noise,
        "duplicates_in_run": duplicates_in_run,
        "limited_by_source": limited_by_source,
        "limited_by_run_cap": limited_by_run_cap,
    }
    return limited, stats


async def _table_exists(conn: psycopg.AsyncConnection[Any], table_name: str) -> bool:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table_name,))
        row = await cur.fetchone()
    return bool(row and row.get("present"))


async def _load_latest_week_clusters(
    conn: psycopg.AsyncConnection[Any],
) -> tuple[str | None, list[dict[str, Any]]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT period_key
            FROM learning_issue_clusters
            WHERE period_granularity = 'week'
            ORDER BY period_key DESC
            LIMIT 1
            """
        )
        row = await cur.fetchone()
        if row is None:
            return None, []
        period_key = str(row["period_key"])
        await cur.execute(
            """
            SELECT period_key, cluster_signature, score, event_count, unique_users, cluster_data
            FROM learning_issue_clusters
            WHERE period_granularity = 'week'
              AND period_key = %s
            ORDER BY score DESC, cluster_signature ASC
            """,
            (period_key,),
        )
        return period_key, await cur.fetchall()


async def _load_latest_underperforming(
    conn: psycopg.AsyncConnection[Any],
) -> tuple[str | None, list[dict[str, Any]]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT period_key
            FROM extraction_underperforming_classes
            ORDER BY period_key DESC
            LIMIT 1
            """
        )
        row = await cur.fetchone()
        if row is None:
            return None, []
        period_key = str(row["period_key"])
        await cur.execute(
            """
            SELECT period_key,
                   claim_class,
                   parser_version,
                   status,
                   brier_score,
                   precision_high_conf,
                   sample_count,
                   details
            FROM extraction_underperforming_classes
            WHERE period_key = %s
            ORDER BY brier_score DESC, claim_class ASC, parser_version ASC
            """,
            (period_key,),
        )
        return period_key, await cur.fetchall()


async def _load_accepted_unknown_dimension_proposals(
    conn: psycopg.AsyncConnection[Any],
) -> tuple[str | None, list[dict[str, Any]]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT period_key,
                   proposal_key,
                   proposal_score,
                   confidence,
                   event_count,
                   unique_users,
                   suggested_dimension,
                   evidence_bundle,
                   risk_notes
            FROM unknown_dimension_proposals
            WHERE status = 'accepted'
            ORDER BY proposal_score DESC, proposal_key ASC
            """
        )
        rows = await cur.fetchall()
    if not rows:
        return None, []
    latest_period = max(str(row["period_key"]) for row in rows if row.get("period_key"))
    return latest_period, rows


async def _load_existing_status_by_key(
    conn: psycopg.AsyncConnection[Any],
) -> dict[str, str]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT candidate_key, status
            FROM learning_backlog_candidates
            """
        )
        rows = await cur.fetchall()
    return {
        str(row["candidate_key"]): str(row["status"])
        for row in rows
        if row.get("candidate_key") and row.get("status")
    }


async def _record_run(
    conn: psycopg.AsyncConnection[Any],
    *,
    status: str,
    source_period_key: str | None,
    total_cluster_rows: int,
    total_underperforming_rows: int,
    candidates_considered: int,
    candidates_written: int,
    filtered_noise: int,
    duplicates_skipped: int,
    details: dict[str, Any],
    started_at: datetime,
) -> None:
    if not await _table_exists(conn, "learning_backlog_bridge_runs"):
        return
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO learning_backlog_bridge_runs (
                status,
                source_period_key,
                total_cluster_rows,
                total_underperforming_rows,
                candidates_considered,
                candidates_written,
                filtered_noise,
                duplicates_skipped,
                details,
                started_at,
                completed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                status,
                source_period_key,
                total_cluster_rows,
                total_underperforming_rows,
                candidates_considered,
                candidates_written,
                filtered_noise,
                duplicates_skipped,
                Json(details),
                started_at,
            ),
        )


async def refresh_learning_backlog_candidates(
    conn: psycopg.AsyncConnection[Any],
) -> dict[str, Any]:
    """Refresh machine-readable backlog candidate payloads from learning signals."""
    started_at = datetime.now(UTC)
    settings = learning_backlog_bridge_settings()

    if not await _table_exists(conn, "learning_backlog_candidates"):
        summary = {
            "status": "skipped",
            "reason": "learning_backlog_candidates_table_missing",
            "candidates_written": 0,
        }
        await _record_run(
            conn,
            status="skipped",
            source_period_key=None,
            total_cluster_rows=0,
            total_underperforming_rows=0,
            candidates_considered=0,
            candidates_written=0,
            filtered_noise=0,
            duplicates_skipped=0,
            details=summary,
            started_at=started_at,
        )
        return summary

    cluster_rows: list[dict[str, Any]] = []
    cluster_period: str | None = None
    if await _table_exists(conn, "learning_issue_clusters"):
        cluster_period, cluster_rows = await _load_latest_week_clusters(conn)

    underperforming_rows: list[dict[str, Any]] = []
    underperforming_period: str | None = None
    if await _table_exists(conn, "extraction_underperforming_classes"):
        underperforming_period, underperforming_rows = await _load_latest_underperforming(
            conn
        )

    unknown_dimension_rows: list[dict[str, Any]] = []
    unknown_period: str | None = None
    if await _table_exists(conn, "unknown_dimension_proposals"):
        unknown_period, unknown_dimension_rows = (
            await _load_accepted_unknown_dimension_proposals(conn)
        )

    candidates, stats = build_backlog_candidates(
        cluster_rows=cluster_rows,
        underperforming_rows=underperforming_rows,
        unknown_dimension_rows=unknown_dimension_rows,
        settings=settings,
    )

    existing_status_by_key = await _load_existing_status_by_key(conn)
    duplicates_skipped = 0
    written = 0
    async with conn.cursor() as cur:
        for candidate in candidates:
            candidate_key = str(candidate["candidate_key"])
            existing_status = existing_status_by_key.get(candidate_key)
            if existing_status in {"approved", "promoted"}:
                duplicates_skipped += 1
                continue

            guardrails = {
                "noise_controls": {
                    "cluster_min_score": settings.cluster_min_score,
                    "cluster_min_events": settings.cluster_min_events,
                    "cluster_min_unique_users": settings.cluster_min_unique_users,
                    "calibration_min_sample_count": settings.calibration_min_sample_count,
                    "unknown_dimension_min_score": settings.unknown_dimension_min_score,
                },
                "dedupe": {
                    "candidate_key": candidate_key,
                    "existing_status": existing_status,
                },
                "caps": {
                    "max_candidates_per_source": settings.max_candidates_per_source,
                    "max_candidates_per_run": settings.max_candidates_per_run,
                },
            }
            candidate["guardrails"] = guardrails
            await cur.execute(
                """
                INSERT INTO learning_backlog_candidates (
                    candidate_key,
                    status,
                    source_type,
                    source_period_key,
                    source_ref,
                    priority_score,
                    title,
                    root_cause_hypothesis,
                    impacted_metrics,
                    suggested_updates,
                    promotion_checklist,
                    issue_payload,
                    guardrails,
                    approval_required,
                    computed_at,
                    updated_at
                )
                VALUES (%s, 'candidate', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (candidate_key) DO UPDATE SET
                    source_type = EXCLUDED.source_type,
                    source_period_key = EXCLUDED.source_period_key,
                    source_ref = EXCLUDED.source_ref,
                    priority_score = EXCLUDED.priority_score,
                    title = EXCLUDED.title,
                    root_cause_hypothesis = EXCLUDED.root_cause_hypothesis,
                    impacted_metrics = EXCLUDED.impacted_metrics,
                    suggested_updates = EXCLUDED.suggested_updates,
                    promotion_checklist = EXCLUDED.promotion_checklist,
                    issue_payload = EXCLUDED.issue_payload,
                    guardrails = EXCLUDED.guardrails,
                    approval_required = EXCLUDED.approval_required,
                    computed_at = NOW(),
                    updated_at = NOW(),
                    status = CASE
                        WHEN learning_backlog_candidates.status = 'dismissed' THEN 'candidate'
                        ELSE learning_backlog_candidates.status
                    END
                WHERE learning_backlog_candidates.status IN ('candidate', 'dismissed')
                """,
                (
                    candidate_key,
                    candidate["source_type"],
                    candidate["source_period_key"],
                    candidate["source_ref"],
                    float(candidate["priority_score"]),
                    candidate["title"],
                    candidate["root_cause_hypothesis"],
                    Json(candidate["impacted_metrics"]),
                    Json(candidate["suggested_updates"]),
                    Json(candidate["promotion_checklist"]),
                    Json(candidate["issue_payload"]),
                    Json(candidate["guardrails"]),
                    bool(candidate["approval_required"]),
                ),
            )
            written += 1

    source_period_key = max(
        [key for key in [cluster_period, underperforming_period, unknown_period] if key],
        default=None,
    )
    details = {
        "settings": {
            "cluster_min_score": settings.cluster_min_score,
            "cluster_min_events": settings.cluster_min_events,
            "cluster_min_unique_users": settings.cluster_min_unique_users,
            "calibration_min_sample_count": settings.calibration_min_sample_count,
            "unknown_dimension_min_score": settings.unknown_dimension_min_score,
            "max_candidates_per_source": settings.max_candidates_per_source,
            "max_candidates_per_run": settings.max_candidates_per_run,
        },
        "build_stats": stats,
        "candidates_preview": [
            {
                "candidate_key": row["candidate_key"],
                "source_type": row["source_type"],
                "source_ref": row["source_ref"],
                "priority_score": row["priority_score"],
                "title": row["title"],
            }
            for row in candidates[:5]
        ],
    }
    summary = {
        "status": "success",
        "source_period_key": source_period_key,
        "total_cluster_rows": len(cluster_rows),
        "total_underperforming_rows": len(underperforming_rows),
        "total_unknown_dimension_rows": len(unknown_dimension_rows),
        "candidates_considered": len(candidates),
        "candidates_written": written,
        "filtered_noise": (
            int(stats["filtered_noise"])
            + int(stats["limited_by_source"])
            + int(stats["limited_by_run_cap"])
        ),
        "duplicates_skipped": duplicates_skipped + int(stats["duplicates_in_run"]),
    }
    await _record_run(
        conn,
        status="success",
        source_period_key=source_period_key,
        total_cluster_rows=len(cluster_rows),
        total_underperforming_rows=len(underperforming_rows),
        candidates_considered=len(candidates),
        candidates_written=written,
        filtered_noise=summary["filtered_noise"],
        duplicates_skipped=summary["duplicates_skipped"],
        details={**summary, **details},
        started_at=started_at,
    )

    logger.info(
        "Refreshed learning backlog bridge: clusters=%d underperforming=%d unknown=%d candidates=%d written=%d duplicates=%d",
        summary["total_cluster_rows"],
        summary["total_underperforming_rows"],
        summary["total_unknown_dimension_rows"],
        summary["candidates_considered"],
        summary["candidates_written"],
        summary["duplicates_skipped"],
    )
    return summary
