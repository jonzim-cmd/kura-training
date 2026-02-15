"""Cross-user learning telemetry clustering (2zc.2).

Deterministic pipeline:
1) read `learning.signal.logged` rows from event store
2) group by stable `cluster_signature`
3) aggregate per day/week buckets
4) compute explainable priority score:
   frequency * severity * impact * reproducibility
5) persist machine-readable cluster artifacts for downstream automation
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

logger = logging.getLogger(__name__)

_SEVERITY_WEIGHT_BY_SIGNAL: dict[str, float] = {
    "save_claim_mismatch_attempt": 1.00,
    "workflow_violation": 0.95,
    "quality_issue_detected": 0.90,
    "repair_auto_rejected": 0.88,
    "repair_simulated_risky": 0.80,
    "save_handshake_pending": 0.78,
    "clarification_requested": 0.72,
    "repair_proposed": 0.65,
    "repair_simulated_safe": 0.55,
    "repair_auto_applied": 0.50,
    "repair_verified_closed": 0.45,
    "save_handshake_verified": 0.35,
    "workflow_phase_transition_closed": 0.30,
}

_IMPACT_WEIGHT_BY_SIGNAL: dict[str, float] = {
    "save_claim_mismatch_attempt": 1.00,
    "workflow_violation": 0.95,
    "repair_auto_rejected": 0.90,
    "quality_issue_detected": 0.86,
    "repair_simulated_risky": 0.82,
    "save_handshake_pending": 0.78,
    "clarification_requested": 0.72,
    "repair_proposed": 0.60,
    "repair_simulated_safe": 0.52,
    "repair_auto_applied": 0.48,
    "repair_verified_closed": 0.44,
    "save_handshake_verified": 0.36,
    "workflow_phase_transition_closed": 0.30,
}

_IMPACT_WEIGHT_BY_CATEGORY: dict[str, float] = {
    "friction_signal": 0.82,
    "quality_signal": 0.68,
    "correction_signal": 0.60,
    "outcome_signal": 0.42,
}


@dataclass(frozen=True)
class IssueClusterSettings:
    window_days: int
    min_support: int
    min_unique_users: int
    max_events_per_user_per_bucket: int
    include_low_confidence: bool
    frequency_reference_count: int
    reproducibility_reference_users: int
    representative_examples: int


@dataclass(frozen=True)
class LearningSignalSample:
    event_id: str
    captured_at: datetime
    cluster_signature: str
    signal_type: str
    category: str
    confidence_band: str
    issue_type: str
    invariant_id: str
    workflow_phase: str
    agent_version: str
    modality: str
    pseudonymized_user_id: str
    attributes: dict[str, Any]


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(minimum, int(raw))
    except ValueError:
        return max(minimum, default)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def issue_cluster_settings() -> IssueClusterSettings:
    return IssueClusterSettings(
        window_days=_int_env("KURA_ISSUE_CLUSTER_WINDOW_DAYS", 30, 1),
        min_support=_int_env("KURA_ISSUE_CLUSTER_MIN_SUPPORT", 3, 1),
        min_unique_users=_int_env("KURA_ISSUE_CLUSTER_MIN_UNIQUE_USERS", 2, 1),
        max_events_per_user_per_bucket=_int_env(
            "KURA_ISSUE_CLUSTER_MAX_EVENTS_PER_USER_PER_BUCKET", 3, 1
        ),
        include_low_confidence=_bool_env(
            "KURA_ISSUE_CLUSTER_INCLUDE_LOW_CONFIDENCE", False
        ),
        frequency_reference_count=_int_env(
            "KURA_ISSUE_CLUSTER_FREQUENCY_REFERENCE_COUNT", 12, 1
        ),
        reproducibility_reference_users=_int_env(
            "KURA_ISSUE_CLUSTER_REPRODUCIBILITY_REFERENCE_USERS", 4, 1
        ),
        representative_examples=_int_env(
            "KURA_ISSUE_CLUSTER_REPRESENTATIVE_EXAMPLES", 3, 1
        ),
    )


def _normalize_text(value: Any, fallback: str = "unknown") -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    if not normalized:
        return fallback
    return normalized


def normalize_confidence_band(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"low", "medium", "high"}:
        return text
    return "low"


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _period_key(captured_at: datetime, granularity: str) -> str:
    if granularity == "day":
        return captured_at.date().isoformat()
    iso_year, iso_week, _ = captured_at.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


# Mismatch-severity modifiers for save_claim_mismatch_attempt signals.
# When mismatch_severity is present in signal attributes, it overrides the
# flat 1.0 weight for this signal type to reflect data-integrity risk:
# - critical: full weight (echo missing → plausible data corruption)
# - warning:  reduced (partial echo → some values visible)
# - info:     minimal (protocol detail missing → no data risk)
_MISMATCH_SEVERITY_MODIFIER: dict[str, float] = {
    "critical": 1.0,
    "warning": 0.5,
    "info": 0.1,
    "none": 0.0,
}


def _severity_weight(
    signal_type: str,
    confidence_band: str,
    attributes: dict[str, Any] | None = None,
) -> float:
    signal = _normalize_text(signal_type, "unknown").lower()
    base = _SEVERITY_WEIGHT_BY_SIGNAL.get(signal, 0.60)
    # Apply mismatch-severity modifier for save_claim_mismatch_attempt signals
    if signal == "save_claim_mismatch_attempt" and attributes:
        ms = attributes.get("mismatch_severity")
        if ms and ms in _MISMATCH_SEVERITY_MODIFIER:
            base = base * _MISMATCH_SEVERITY_MODIFIER[ms]
    if confidence_band == "high":
        return min(1.0, base)
    if confidence_band == "medium":
        return min(1.0, base * 0.92)
    return min(1.0, base * 0.75)


def _impact_weight(
    signal_type: str,
    category: str,
    attributes: dict[str, Any] | None = None,
) -> float:
    signal = _normalize_text(signal_type, "unknown").lower()
    base: float | None = None
    if signal in _IMPACT_WEIGHT_BY_SIGNAL:
        base = _IMPACT_WEIGHT_BY_SIGNAL[signal]
    # Apply mismatch-severity modifier for save_claim_mismatch_attempt signals
    if signal == "save_claim_mismatch_attempt" and base is not None and attributes:
        ms = attributes.get("mismatch_severity")
        if ms and ms in _MISMATCH_SEVERITY_MODIFIER:
            base = base * _MISMATCH_SEVERITY_MODIFIER[ms]
    if base is not None:
        return base
    category_key = _normalize_text(category, "quality_signal").lower()
    return _IMPACT_WEIGHT_BY_CATEGORY.get(category_key, 0.60)


def compute_priority_score(
    *,
    event_count: int,
    unique_users: int,
    severity: float,
    impact: float,
    frequency_reference_count: int,
    reproducibility_reference_users: int,
) -> dict[str, float]:
    frequency = min(1.0, float(event_count) / float(max(1, frequency_reference_count)))
    user_coverage = min(
        1.0,
        float(unique_users) / float(max(1, reproducibility_reference_users)),
    )
    repeatability = min(
        1.0,
        (float(event_count) / float(max(1, unique_users))) / 2.0,
    )
    reproducibility = (user_coverage + repeatability) / 2.0

    score = frequency * max(0.0, min(1.0, severity)) * max(
        0.0, min(1.0, impact)
    ) * reproducibility
    return {
        "score": round(score, 6),
        "frequency": round(frequency, 6),
        "severity": round(max(0.0, min(1.0, severity)), 6),
        "impact": round(max(0.0, min(1.0, impact)), 6),
        "reproducibility": round(reproducibility, 6),
    }


def _sample_from_row(
    row: dict[str, Any],
    *,
    include_low_confidence: bool,
) -> tuple[LearningSignalSample | None, str | None]:
    payload = row.get("data")
    if not isinstance(payload, dict):
        return None, "invalid_payload"

    signature = payload.get("signature")
    if not isinstance(signature, dict):
        return None, "invalid_signature"

    user_ref = payload.get("user_ref")
    if not isinstance(user_ref, dict):
        return None, "invalid_user_ref"

    cluster_signature = _normalize_text(payload.get("cluster_signature"), "")
    pseudo_user = _normalize_text(user_ref.get("pseudonymized_user_id"), "")
    if not cluster_signature or not pseudo_user:
        return None, "missing_cluster_or_user_ref"

    confidence_band = normalize_confidence_band(signature.get("confidence_band"))
    if confidence_band == "low" and not include_low_confidence:
        return None, "low_confidence_filtered"

    captured_at = _parse_datetime(payload.get("captured_at")) or _parse_datetime(
        row.get("timestamp")
    )
    if captured_at is None:
        return None, "invalid_timestamp"

    attributes = payload.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}

    sample = LearningSignalSample(
        event_id=str(row.get("event_id") or ""),
        captured_at=captured_at,
        cluster_signature=cluster_signature,
        signal_type=_normalize_text(payload.get("signal_type")),
        category=_normalize_text(payload.get("category")),
        confidence_band=confidence_band,
        issue_type=_normalize_text(signature.get("issue_type"), "none"),
        invariant_id=_normalize_text(signature.get("invariant_id"), "none"),
        workflow_phase=_normalize_text(signature.get("workflow_phase")),
        agent_version=_normalize_text(signature.get("agent_version")),
        modality=_normalize_text(signature.get("modality")),
        pseudonymized_user_id=pseudo_user,
        attributes=attributes,
    )
    return sample, None


def build_issue_clusters(
    samples: list[LearningSignalSample],
    *,
    settings: IssueClusterSettings,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[tuple[str, str, str], list[LearningSignalSample]] = defaultdict(list)
    for sample in samples:
        for granularity in ("day", "week"):
            grouped[
                (
                    granularity,
                    _period_key(sample.captured_at, granularity),
                    sample.cluster_signature,
                )
            ].append(sample)

    clusters: list[dict[str, Any]] = []
    filtered_min_support = 0
    filtered_unique_users = 0
    dominance_dropped_events = 0

    for key in sorted(grouped.keys()):
        granularity, period_key, cluster_signature = key
        bucket = sorted(
            grouped[key],
            key=lambda item: (item.captured_at, item.event_id),
        )
        per_user_counts: dict[str, int] = {}
        capped_bucket: list[LearningSignalSample] = []
        dropped_for_bucket = 0
        for item in bucket:
            current = per_user_counts.get(item.pseudonymized_user_id, 0)
            if current >= settings.max_events_per_user_per_bucket:
                dropped_for_bucket += 1
                continue
            per_user_counts[item.pseudonymized_user_id] = current + 1
            capped_bucket.append(item)
        dominance_dropped_events += dropped_for_bucket

        event_count = len(capped_bucket)
        unique_users = len({item.pseudonymized_user_id for item in capped_bucket})

        if event_count < settings.min_support:
            filtered_min_support += 1
            continue
        if unique_users < settings.min_unique_users:
            filtered_unique_users += 1
            continue

        severity = sum(
            _severity_weight(item.signal_type, item.confidence_band, item.attributes)
            for item in capped_bucket
        ) / float(event_count)
        impact = sum(
            _impact_weight(item.signal_type, item.category, item.attributes)
            for item in capped_bucket
        ) / float(event_count)
        factors = compute_priority_score(
            event_count=event_count,
            unique_users=unique_users,
            severity=severity,
            impact=impact,
            frequency_reference_count=settings.frequency_reference_count,
            reproducibility_reference_users=settings.reproducibility_reference_users,
        )

        signal_counts = Counter(item.signal_type for item in capped_bucket)
        phase_counts = Counter(item.workflow_phase for item in capped_bucket)
        top_signal = sorted(signal_counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
        first_seen = capped_bucket[0].captured_at.isoformat()
        last_seen = capped_bucket[-1].captured_at.isoformat()
        representatives = [
            {
                "event_id": item.event_id,
                "captured_at": item.captured_at.isoformat(),
                "signal_type": item.signal_type,
                "workflow_phase": item.workflow_phase,
                "issue_type": item.issue_type,
                "invariant_id": item.invariant_id,
                "attributes": item.attributes,
            }
            for item in capped_bucket[: settings.representative_examples]
        ]
        cluster_data = {
            "summary": (
                f"{top_signal} recurred {event_count}x across {unique_users} users "
                f"({granularity} {period_key})."
            ),
            "signature": {
                "cluster_signature": cluster_signature,
                "signal_type_top": top_signal,
            },
            "score": factors["score"],
            "score_factors": {
                "frequency": factors["frequency"],
                "severity": factors["severity"],
                "impact": factors["impact"],
                "reproducibility": factors["reproducibility"],
                "formula": "frequency * severity * impact * reproducibility",
            },
            "aggregates": {
                "event_count": event_count,
                "unique_pseudo_users": unique_users,
                "first_seen_at": first_seen,
                "last_seen_at": last_seen,
                "signal_type_counts": [
                    {"signal_type": name, "count": count}
                    for name, count in sorted(
                        signal_counts.items(),
                        key=lambda pair: (-pair[1], pair[0]),
                    )
                ],
            },
            "affected_workflow_phases": [
                name
                for name, _ in sorted(phase_counts.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "representative_examples": representatives,
            "false_positive_controls": {
                "min_support": settings.min_support,
                "min_unique_users": settings.min_unique_users,
                "max_events_per_user_per_bucket": settings.max_events_per_user_per_bucket,
                "include_low_confidence": settings.include_low_confidence,
                "dominance_dropped_events": dropped_for_bucket,
                "status": "passed",
            },
        }
        clusters.append(
            {
                "period_granularity": granularity,
                "period_key": period_key,
                "cluster_signature": cluster_signature,
                "score": factors["score"],
                "event_count": event_count,
                "unique_users": unique_users,
                "cluster_data": cluster_data,
            }
        )

    clusters = sorted(
        clusters,
        key=lambda row: (
            row["period_granularity"],
            row["period_key"],
            -float(row["score"]),
            row["cluster_signature"],
        ),
    )
    return clusters, {
        "groups_total": len(grouped),
        "clusters_written": len(clusters),
        "filtered_min_support": filtered_min_support,
        "filtered_unique_users": filtered_unique_users,
        "dominance_dropped_events": dominance_dropped_events,
    }


async def _table_exists(conn: psycopg.AsyncConnection[Any], table_name: str) -> bool:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table_name,))
        row = await cur.fetchone()
    return bool(row and row.get("present"))


async def _load_learning_signal_rows(
    conn: psycopg.AsyncConnection[Any], *, window_days: int
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT e.id::text AS event_id, e.timestamp, e.data
            FROM events e
            WHERE e.event_type = 'learning.signal.logged'
              AND e.timestamp >= NOW() - make_interval(days => %s)
              AND NOT EXISTS (
                  SELECT 1
                  FROM events r
                  WHERE r.event_type = 'event.retracted'
                    AND r.data->>'retracted_event_id' = e.id::text
              )
            ORDER BY e.timestamp ASC, e.id ASC
            """,
            (window_days,),
        )
        return await cur.fetchall()


async def _record_run(
    conn: psycopg.AsyncConnection[Any],
    *,
    status: str,
    settings: IssueClusterSettings,
    total_signals: int,
    considered_signals: int,
    cluster_stats: dict[str, int],
    filtered_low_confidence: int,
    filtered_invalid_rows: int,
    details: dict[str, Any],
    started_at: datetime,
) -> None:
    if not await _table_exists(conn, "learning_issue_cluster_runs"):
        return
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO learning_issue_cluster_runs (
                status,
                window_days,
                total_signals,
                considered_signals,
                clusters_written,
                filtered_low_confidence,
                filtered_min_support,
                filtered_unique_users,
                details,
                started_at,
                completed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                status,
                settings.window_days,
                total_signals,
                considered_signals,
                int(cluster_stats.get("clusters_written", 0)),
                filtered_low_confidence,
                int(cluster_stats.get("filtered_min_support", 0)),
                int(cluster_stats.get("filtered_unique_users", 0)),
                Json(
                    {
                        **details,
                        "filtered_invalid_rows": filtered_invalid_rows,
                        "groups_total": int(cluster_stats.get("groups_total", 0)),
                        "frequency_reference_count": settings.frequency_reference_count,
                        "reproducibility_reference_users": settings.reproducibility_reference_users,
                    }
                ),
                started_at,
            ),
        )


async def refresh_issue_clusters(
    conn: psycopg.AsyncConnection[Any],
) -> dict[str, Any]:
    """Refresh day/week cluster artifacts from learning telemetry."""
    started_at = datetime.now(UTC)
    settings = issue_cluster_settings()

    if not await _table_exists(conn, "learning_issue_clusters"):
        summary = {
            "status": "skipped",
            "reason": "learning_issue_clusters_table_missing",
            "clusters_written": 0,
        }
        await _record_run(
            conn,
            status="skipped",
            settings=settings,
            total_signals=0,
            considered_signals=0,
            cluster_stats={},
            filtered_low_confidence=0,
            filtered_invalid_rows=0,
            details=summary,
            started_at=started_at,
        )
        return summary

    rows = await _load_learning_signal_rows(conn, window_days=settings.window_days)
    samples: list[LearningSignalSample] = []
    filtered_low_confidence = 0
    filtered_invalid_rows = 0

    for row in rows:
        sample, reason = _sample_from_row(
            row,
            include_low_confidence=settings.include_low_confidence,
        )
        if sample is not None:
            samples.append(sample)
            continue
        if reason == "low_confidence_filtered":
            filtered_low_confidence += 1
        else:
            filtered_invalid_rows += 1

    clusters, cluster_stats = build_issue_clusters(samples, settings=settings)

    async with conn.cursor() as cur:
        await cur.execute("DELETE FROM learning_issue_clusters")
        for cluster in clusters:
            await cur.execute(
                """
                INSERT INTO learning_issue_clusters (
                    period_granularity,
                    period_key,
                    cluster_signature,
                    score,
                    event_count,
                    unique_users,
                    cluster_data,
                    computed_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (
                    cluster["period_granularity"],
                    cluster["period_key"],
                    cluster["cluster_signature"],
                    float(cluster["score"]),
                    int(cluster["event_count"]),
                    int(cluster["unique_users"]),
                    Json(cluster["cluster_data"]),
                ),
            )

    summary = {
        "status": "success",
        "window_days": settings.window_days,
        "total_signals": len(rows),
        "considered_signals": len(samples),
        "clusters_written": cluster_stats["clusters_written"],
        "filtered_low_confidence": filtered_low_confidence,
        "filtered_invalid_rows": filtered_invalid_rows,
        "filtered_min_support": cluster_stats["filtered_min_support"],
        "filtered_unique_users": cluster_stats["filtered_unique_users"],
        "groups_total": cluster_stats["groups_total"],
        "period_granularities": ["day", "week"],
    }

    await _record_run(
        conn,
        status="success",
        settings=settings,
        total_signals=len(rows),
        considered_signals=len(samples),
        cluster_stats=cluster_stats,
        filtered_low_confidence=filtered_low_confidence,
        filtered_invalid_rows=filtered_invalid_rows,
        details=summary,
        started_at=started_at,
    )

    logger.info(
        "Refreshed issue clusters: signals=%d considered=%d clusters=%d filtered(low_conf=%d,min_support=%d,unique_users=%d)",
        summary["total_signals"],
        summary["considered_signals"],
        summary["clusters_written"],
        summary["filtered_low_confidence"],
        summary["filtered_min_support"],
        summary["filtered_unique_users"],
    )
    return summary
