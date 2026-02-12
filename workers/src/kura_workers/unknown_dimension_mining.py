"""Unknown-dimension mining + suggestion loop (2zc.6)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import logging
import os
import re
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

logger = logging.getLogger(__name__)

_KNOWN_DIMENSIONS = {"motivation_pre", "discomfort_signal", "jump_baseline"}
_PROVISIONAL_PREFIXES = ("x_", "custom.", "provisional.")
_SCOPE_IMPACT_WEIGHT = {"session": 0.72, "exercise": 0.86, "set": 1.0}
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "der",
    "die",
    "das",
    "und",
    "oder",
    "mit",
    "von",
    "den",
    "dem",
    "ein",
    "eine",
    "im",
    "in",
    "zu",
    "auf",
    "bei",
    "ist",
}


@dataclass(frozen=True)
class UnknownDimensionMiningSettings:
    window_days: int
    min_support: int
    min_unique_users: int
    frequency_reference_count: int
    reproducibility_reference_users: int
    representative_examples: int
    max_proposals_per_run: int


@dataclass(frozen=True)
class UnknownObservationSample:
    event_id: str
    captured_at: datetime
    dimension: str
    dimension_seed: str
    tier: str
    scope_level: str
    semantic_fingerprint: str
    value_type: str
    value: Any
    unit: str | None
    context_text: str
    tags: tuple[str, ...]
    pseudonymized_user_id: str


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(minimum, int(raw))
    except ValueError:
        return max(minimum, default)


def unknown_dimension_mining_settings() -> UnknownDimensionMiningSettings:
    return UnknownDimensionMiningSettings(
        window_days=_int_env("KURA_UNKNOWN_DIMENSION_WINDOW_DAYS", 30, 1),
        min_support=_int_env("KURA_UNKNOWN_DIMENSION_MIN_SUPPORT", 3, 1),
        min_unique_users=_int_env("KURA_UNKNOWN_DIMENSION_MIN_UNIQUE_USERS", 2, 1),
        frequency_reference_count=_int_env(
            "KURA_UNKNOWN_DIMENSION_FREQUENCY_REFERENCE_COUNT", 10, 1
        ),
        reproducibility_reference_users=_int_env(
            "KURA_UNKNOWN_DIMENSION_REPRODUCIBILITY_REFERENCE_USERS", 4, 1
        ),
        representative_examples=_int_env(
            "KURA_UNKNOWN_DIMENSION_REPRESENTATIVE_EXAMPLES", 4, 1
        ),
        max_proposals_per_run=_int_env("KURA_UNKNOWN_DIMENSION_MAX_PROPOSALS", 12, 1),
    )


def _normalize_text(value: Any, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    return normalized if normalized else fallback


def _normalize_dimension(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace(" ", "_")
    return normalized if normalized else None


def _dimension_tier(dimension: str) -> str:
    if dimension in _KNOWN_DIMENSIONS:
        return "known"
    if any(dimension.startswith(prefix) for prefix in _PROVISIONAL_PREFIXES):
        return "provisional"
    return "unknown"


def _pseudonymized_user(user_id: str) -> str:
    digest = hashlib.sha1(str(user_id).encode("utf-8")).hexdigest()[:12]
    return f"u_{digest}"


def _period_key(captured_at: datetime) -> str:
    iso_year, iso_week, _ = captured_at.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _scope_level(value: Any) -> str:
    if isinstance(value, dict):
        level = _normalize_text(value.get("level"), "").lower()
    else:
        level = ""
    if level in {"session", "exercise", "set"}:
        return level
    return "session"


def _normalize_tags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        tag = item.strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return tuple(out)


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _dimension_seed(dimension: str) -> str:
    seed = dimension
    for prefix in _PROVISIONAL_PREFIXES:
        if seed.startswith(prefix):
            seed = seed[len(prefix) :]
            break
    seed = re.sub(r"[^a-z0-9_]+", "_", seed.lower()).strip("_")
    return seed or "unknown_observation"


def _tokenize(text: str) -> list[str]:
    parts = re.findall(r"[a-z0-9_]+", text.lower())
    return [token for token in parts if len(token) >= 3 and token not in _STOPWORDS]


def _semantic_fingerprint(dimension: str, context_text: str, tags: tuple[str, ...]) -> str:
    joined = " ".join([dimension, context_text, " ".join(tags)]).strip()
    tokens = _tokenize(joined)
    if not tokens:
        return "no_semantic_terms"
    counts = Counter(tokens)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return "-".join(token for token, _ in ranked[:4])


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _stable_proposal_key(cluster_signature: str) -> str:
    digest = hashlib.sha1(cluster_signature.encode("utf-8")).hexdigest()[:16]
    return f"unknown:{cluster_signature}:{digest}"


def _suggested_dimension_name(seed: str, semantic_fingerprint: str) -> str:
    normalized_seed = re.sub(r"[^a-z0-9_]+", "_", seed.lower()).strip("_")
    if normalized_seed and normalized_seed != "unknown_observation":
        return normalized_seed
    token = semantic_fingerprint.split("-", 1)[0]
    token = re.sub(r"[^a-z0-9_]+", "_", token.lower()).strip("_")
    return f"observed_{token or 'unknown'}"


def _suggested_dimension_schema(
    bucket: list[UnknownObservationSample],
    *,
    semantic_fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value_type_counts = Counter(sample.value_type for sample in bucket)
    total = max(1, len(bucket))
    top_value_type, top_type_count = sorted(
        value_type_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )[0]
    type_consistency = float(top_type_count) / float(total)

    unit_counts = Counter(
        sample.unit for sample in bucket if isinstance(sample.unit, str) and sample.unit
    )
    expected_unit: str | None = None
    unit_consistency = 1.0
    if unit_counts:
        expected_unit, expected_unit_count = sorted(
            unit_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
        unit_consistency = float(expected_unit_count) / float(total)

    numeric_values = [
        _safe_float(sample.value)
        for sample in bucket
        if sample.value_type == "number"
    ]
    expected_scale = None
    if top_value_type == "number" and numeric_values:
        expected_scale = {
            "min": round(min(numeric_values), 3),
            "max": round(max(numeric_values), 3),
        }

    seed_counts = Counter(sample.dimension_seed for sample in bucket)
    top_seed, _ = sorted(seed_counts.items(), key=lambda item: (-item[1], item[0]))[0]
    suggested_name = _suggested_dimension_name(top_seed, semantic_fingerprint)
    schema = {
        "name": suggested_name,
        "value_type": top_value_type if type_consistency >= 0.6 else "mixed",
        "expected_unit": expected_unit,
        "expected_scale": expected_scale,
        "description": (
            f"Auto-mined unknown dimension from recurring observation patterns "
            f"({semantic_fingerprint})."
        ),
    }
    diagnostics = {
        "value_type_distribution": dict(sorted(value_type_counts.items())),
        "unit_distribution": dict(sorted(unit_counts.items())),
        "type_consistency": round(type_consistency, 6),
        "unit_consistency": round(unit_consistency, 6),
    }
    return schema, diagnostics


def _proposal_scores(
    *,
    event_count: int,
    unique_users: int,
    consistency: float,
    scope_impact: float,
    settings: UnknownDimensionMiningSettings,
) -> tuple[float, float, dict[str, float]]:
    frequency = min(1.0, float(event_count) / float(settings.frequency_reference_count))
    user_coverage = min(
        1.0,
        float(unique_users) / float(settings.reproducibility_reference_users),
    )
    repeatability = min(
        1.0,
        (float(event_count) / float(max(1, unique_users))) / 2.0,
    )
    reproducibility = (user_coverage + repeatability) / 2.0
    proposal_score = frequency * reproducibility * consistency * scope_impact
    confidence = 0.4 * reproducibility + 0.35 * consistency + 0.25 * frequency
    factors = {
        "frequency": round(frequency, 6),
        "reproducibility": round(reproducibility, 6),
        "consistency": round(consistency, 6),
        "scope_impact": round(scope_impact, 6),
    }
    return round(_clamp01(proposal_score), 6), round(_clamp01(confidence), 6), factors


def build_unknown_dimension_proposals(
    samples: list[UnknownObservationSample],
    *,
    settings: UnknownDimensionMiningSettings,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[tuple[str, str], list[UnknownObservationSample]] = defaultdict(list)
    for sample in samples:
        # Cluster on scope + dimension seed; semantic fingerprint remains in evidence.
        # This keeps grouping deterministic while avoiding over-fragmentation from
        # small wording differences in free-form context text.
        cluster_signature = f"{sample.scope_level}|{sample.dimension_seed}"
        grouped[(_period_key(sample.captured_at), cluster_signature)].append(sample)

    proposals: list[dict[str, Any]] = []
    filtered_min_support = 0
    filtered_unique_users = 0

    for key in sorted(grouped.keys()):
        period_key, cluster_signature = key
        bucket = sorted(
            grouped[key],
            key=lambda item: (item.captured_at, item.event_id),
        )
        event_count = len(bucket)
        unique_users = len({sample.pseudonymized_user_id for sample in bucket})
        if event_count < settings.min_support:
            filtered_min_support += 1
            continue
        if unique_users < settings.min_unique_users:
            filtered_unique_users += 1
            continue

        schema, schema_diag = _suggested_dimension_schema(
            bucket,
            semantic_fingerprint=bucket[0].semantic_fingerprint,
        )
        scope_counter = Counter(sample.scope_level for sample in bucket)
        scope_impact = sum(
            _SCOPE_IMPACT_WEIGHT.get(scope, 0.72) * count
            for scope, count in scope_counter.items()
        ) / float(event_count)
        consistency = (
            0.60 * float(schema_diag["type_consistency"])
            + 0.40 * float(schema_diag["unit_consistency"])
        )
        proposal_score, confidence, score_factors = _proposal_scores(
            event_count=event_count,
            unique_users=unique_users,
            consistency=consistency,
            scope_impact=scope_impact,
            settings=settings,
        )

        risk_notes: list[str] = []
        if schema["value_type"] == "mixed":
            risk_notes.append("mixed_value_types_detected")
        if len(schema_diag["unit_distribution"]) > 1:
            risk_notes.append("unit_inconsistency_detected")
        if confidence < 0.55:
            risk_notes.append("low_confidence_dimension_hypothesis")
        if any(sample.tier == "provisional" for sample in bucket):
            risk_notes.append("provisional_prefix_inputs_present")

        representatives = [
            {
                "event_id": sample.event_id,
                "captured_at": sample.captured_at.isoformat(),
                "dimension": sample.dimension,
                "scope_level": sample.scope_level,
                "context_text": sample.context_text,
                "value": sample.value,
                "unit": sample.unit,
            }
            for sample in bucket[: settings.representative_examples]
        ]
        context_counter = Counter(
            sample.context_text for sample in bucket if sample.context_text
        )
        sample_utterances = [
            text
            for text, _ in sorted(
                context_counter.items(),
                key=lambda item: (-item[1], item[0]),
            )[: settings.representative_examples]
        ]
        evidence_bundle = {
            "cluster_signature": cluster_signature,
            "event_count": event_count,
            "unique_users": unique_users,
            "scope_distribution": dict(sorted(scope_counter.items())),
            "sample_event_ids": [sample.event_id for sample in bucket[:10]],
            "sample_utterances": sample_utterances,
            "representative_examples": representatives,
            "schema_diagnostics": schema_diag,
        }
        root_cause_hypothesis = (
            "Recurring unknown/provisional observations share semantic and workflow "
            "context but lack a formal dimension contract."
        )
        proposal_payload = {
            "schema_version": 1,
            "approval_required": True,
            "status": "candidate",
            "period_key": period_key,
            "cluster_signature": cluster_signature,
            "root_cause_hypothesis": root_cause_hypothesis,
            "suggested_dimension": schema,
            "confidence": confidence,
            "proposal_score": proposal_score,
            "score_factors": score_factors,
            "evidence_bundle": evidence_bundle,
            "risk_notes": sorted(set(risk_notes)),
            "approval_workflow": {
                "steps": [
                    "candidate_review",
                    "human_acceptance",
                    "contract_draft_validation",
                    "backlog_bridge_promotion",
                ],
                "route_on_accept": {
                    "target": "learning_backlog_candidates",
                    "source_type": "unknown_dimension",
                    "dedupe_key": "proposal_key",
                },
            },
        }
        proposal_key = _stable_proposal_key(cluster_signature)
        proposals.append(
            {
                "proposal_key": proposal_key,
                "period_key": period_key,
                "cluster_signature": cluster_signature,
                "dimension_seed": str(schema["name"]),
                "proposal_score": proposal_score,
                "confidence": confidence,
                "event_count": event_count,
                "unique_users": unique_users,
                "suggested_dimension": schema,
                "evidence_bundle": evidence_bundle,
                "risk_notes": sorted(set(risk_notes)),
                "proposal_payload": proposal_payload,
            }
        )

    ordered = sorted(
        proposals,
        key=lambda row: (
            row["period_key"],
            -float(row["proposal_score"]),
            row["proposal_key"],
        ),
    )
    limited = ordered[: settings.max_proposals_per_run]
    return limited, {
        "groups_total": len(grouped),
        "proposals_generated": len(limited),
        "filtered_min_support": filtered_min_support,
        "filtered_unique_users": filtered_unique_users,
        "limited_by_run_cap": max(0, len(ordered) - len(limited)),
    }


async def _table_exists(conn: psycopg.AsyncConnection[Any], table_name: str) -> bool:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table_name,))
        row = await cur.fetchone()
    return bool(row and row.get("present"))


async def _load_observation_rows(
    conn: psycopg.AsyncConnection[Any],
    *,
    window_days: int,
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT e.id::text AS event_id, e.user_id::text AS user_id, e.timestamp, e.data
            FROM events e
            WHERE e.event_type = 'observation.logged'
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


def _sample_from_row(
    row: dict[str, Any],
) -> tuple[UnknownObservationSample | None, str | None]:
    data = row.get("data")
    if not isinstance(data, dict):
        return None, "invalid_payload"

    dimension = _normalize_dimension(data.get("dimension"))
    if not dimension:
        return None, "missing_dimension"
    tier = _dimension_tier(dimension)
    if tier == "known":
        return None, "known_dimension_skipped"

    timestamp = row.get("timestamp")
    if not isinstance(timestamp, datetime):
        return None, "invalid_timestamp"
    captured_at = timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp.replace(
        tzinfo=UTC
    )

    context_text = _normalize_text(data.get("context_text"), "")
    tags = _normalize_tags(data.get("tags"))
    semantic_fingerprint = _semantic_fingerprint(dimension, context_text, tags)

    unit = _normalize_text(data.get("unit"), "") if isinstance(data.get("unit"), str) else ""
    unit_value = unit if unit else None

    user_id = _normalize_text(row.get("user_id"), "")
    if not user_id:
        return None, "missing_user_id"

    scope_level = _scope_level(data.get("scope"))
    value = data.get("value")
    sample = UnknownObservationSample(
        event_id=_normalize_text(row.get("event_id"), ""),
        captured_at=captured_at,
        dimension=dimension,
        dimension_seed=_dimension_seed(dimension),
        tier=tier,
        scope_level=scope_level,
        semantic_fingerprint=semantic_fingerprint,
        value_type=_value_type(value),
        value=value,
        unit=unit_value,
        context_text=context_text,
        tags=tags,
        pseudonymized_user_id=_pseudonymized_user(user_id),
    )
    return sample, None


async def _load_existing_status_by_key(
    conn: psycopg.AsyncConnection[Any],
) -> dict[str, str]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT proposal_key, status
            FROM unknown_dimension_proposals
            """
        )
        rows = await cur.fetchall()
    return {
        str(row["proposal_key"]): str(row["status"])
        for row in rows
        if row.get("proposal_key") and row.get("status")
    }


async def _record_run(
    conn: psycopg.AsyncConnection[Any],
    *,
    status: str,
    settings: UnknownDimensionMiningSettings,
    total_observations: int,
    considered_observations: int,
    proposals_written: int,
    filtered_invalid_rows: int,
    filtered_noise: int,
    details: dict[str, Any],
    started_at: datetime,
) -> None:
    if not await _table_exists(conn, "unknown_dimension_mining_runs"):
        return
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO unknown_dimension_mining_runs (
                status,
                window_days,
                total_observations,
                considered_observations,
                proposals_written,
                filtered_invalid_rows,
                filtered_noise,
                details,
                started_at,
                completed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                status,
                settings.window_days,
                total_observations,
                considered_observations,
                proposals_written,
                filtered_invalid_rows,
                filtered_noise,
                Json(details),
                started_at,
            ),
        )


async def refresh_unknown_dimension_proposals(
    conn: psycopg.AsyncConnection[Any],
) -> dict[str, Any]:
    """Refresh ranked unknown-dimension proposals from observation telemetry."""
    started_at = datetime.now(UTC)
    settings = unknown_dimension_mining_settings()

    if not await _table_exists(conn, "unknown_dimension_proposals"):
        summary = {
            "status": "skipped",
            "reason": "unknown_dimension_proposals_table_missing",
            "proposals_written": 0,
        }
        await _record_run(
            conn,
            status="skipped",
            settings=settings,
            total_observations=0,
            considered_observations=0,
            proposals_written=0,
            filtered_invalid_rows=0,
            filtered_noise=0,
            details=summary,
            started_at=started_at,
        )
        return summary

    rows = await _load_observation_rows(conn, window_days=settings.window_days)
    samples: list[UnknownObservationSample] = []
    filtered_invalid_rows = 0
    for row in rows:
        sample, reason = _sample_from_row(row)
        if sample is not None:
            samples.append(sample)
        elif reason != "known_dimension_skipped":
            filtered_invalid_rows += 1

    proposals, stats = build_unknown_dimension_proposals(samples, settings=settings)
    existing_status_by_key = await _load_existing_status_by_key(conn)
    written = 0
    promoted_or_accepted_skipped = 0

    async with conn.cursor() as cur:
        for proposal in proposals:
            key = str(proposal["proposal_key"])
            existing_status = existing_status_by_key.get(key)
            if existing_status in {"accepted", "promoted"}:
                promoted_or_accepted_skipped += 1
                continue
            await cur.execute(
                """
                INSERT INTO unknown_dimension_proposals (
                    proposal_key,
                    status,
                    period_key,
                    cluster_signature,
                    dimension_seed,
                    proposal_score,
                    confidence,
                    event_count,
                    unique_users,
                    suggested_dimension,
                    evidence_bundle,
                    risk_notes,
                    proposal_payload,
                    computed_at,
                    updated_at
                )
                VALUES (%s, 'candidate', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (proposal_key) DO UPDATE SET
                    period_key = EXCLUDED.period_key,
                    cluster_signature = EXCLUDED.cluster_signature,
                    dimension_seed = EXCLUDED.dimension_seed,
                    proposal_score = EXCLUDED.proposal_score,
                    confidence = EXCLUDED.confidence,
                    event_count = EXCLUDED.event_count,
                    unique_users = EXCLUDED.unique_users,
                    suggested_dimension = EXCLUDED.suggested_dimension,
                    evidence_bundle = EXCLUDED.evidence_bundle,
                    risk_notes = EXCLUDED.risk_notes,
                    proposal_payload = EXCLUDED.proposal_payload,
                    computed_at = NOW(),
                    updated_at = NOW(),
                    status = CASE
                        WHEN unknown_dimension_proposals.status = 'dismissed' THEN 'candidate'
                        ELSE unknown_dimension_proposals.status
                    END
                WHERE unknown_dimension_proposals.status IN ('candidate', 'dismissed')
                """,
                (
                    key,
                    proposal["period_key"],
                    proposal["cluster_signature"],
                    proposal["dimension_seed"],
                    float(proposal["proposal_score"]),
                    float(proposal["confidence"]),
                    int(proposal["event_count"]),
                    int(proposal["unique_users"]),
                    Json(proposal["suggested_dimension"]),
                    Json(proposal["evidence_bundle"]),
                    Json(proposal["risk_notes"]),
                    Json(proposal["proposal_payload"]),
                ),
            )
            written += 1

    filtered_noise = (
        int(stats["filtered_min_support"])
        + int(stats["filtered_unique_users"])
        + int(stats["limited_by_run_cap"])
    )
    summary = {
        "status": "success",
        "window_days": settings.window_days,
        "total_observations": len(rows),
        "considered_observations": len(samples),
        "proposals_generated": stats["proposals_generated"],
        "proposals_written": written,
        "filtered_invalid_rows": filtered_invalid_rows,
        "filtered_noise": filtered_noise,
        "accepted_or_promoted_skipped": promoted_or_accepted_skipped,
    }
    await _record_run(
        conn,
        status="success",
        settings=settings,
        total_observations=len(rows),
        considered_observations=len(samples),
        proposals_written=written,
        filtered_invalid_rows=filtered_invalid_rows,
        filtered_noise=filtered_noise,
        details={**summary, **stats},
        started_at=started_at,
    )
    logger.info(
        "Refreshed unknown-dimension proposals: observations=%d considered=%d generated=%d written=%d",
        summary["total_observations"],
        summary["considered_observations"],
        summary["proposals_generated"],
        summary["proposals_written"],
    )
    return summary
