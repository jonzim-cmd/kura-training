"""Async deep-analysis worker job handler.

Creates deterministic backend analysis envelopes for external agent polling.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..registry import register

logger = logging.getLogger(__name__)

ANALYSIS_RESULT_SCHEMA_VERSION = "deep_analysis_result.v1"


async def _fetch_analysis_job(
    conn: psycopg.AsyncConnection[Any],
    *,
    analysis_job_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, objective, horizon_days, focus
            FROM analysis_jobs
            WHERE id = %s
              AND user_id = %s
            """,
            (analysis_job_id, user_id),
        )
        return await cur.fetchone()


async def _mark_processing(
    conn: psycopg.AsyncConnection[Any],
    *,
    analysis_job_id: str,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE analysis_jobs
            SET status = 'processing',
                started_at = COALESCE(started_at, NOW()),
                error_code = NULL,
                error_message = NULL
            WHERE id = %s
            """,
            (analysis_job_id,),
        )


async def _load_event_type_counts(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    horizon_days: int,
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT event_type, COUNT(*)::BIGINT AS count
            FROM events
            WHERE user_id = %s
              AND timestamp >= NOW() - (%s::INT * INTERVAL '1 day')
            GROUP BY event_type
            ORDER BY count DESC, event_type ASC
            LIMIT 8
            """,
            (user_id, horizon_days),
        )
        return await cur.fetchall()


async def _load_quality_health_context(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data, updated_at
            FROM projections
            WHERE user_id = %s
              AND projection_type = 'quality_health'
              AND key = 'overview'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        return await cur.fetchone()


def _safe_focus_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip().lower()
        if not normalized:
            continue
        if normalized in out:
            continue
        out.append(normalized)
    return out


def build_deep_analysis_result(
    *,
    objective: str,
    horizon_days: int,
    focus: list[str],
    event_type_counts: list[dict[str, Any]],
    quality_health: dict[str, Any] | None,
) -> dict[str, Any]:
    total_events = sum(int(item.get("count") or 0) for item in event_type_counts)
    top_event_types = [
        {
            "event_type": str(item.get("event_type") or "unknown"),
            "count": int(item.get("count") or 0),
        }
        for item in event_type_counts
    ]
    top_labels = [f"{item['event_type']} ({item['count']})" for item in top_event_types[:3]]

    uncertainty: list[str] = []
    if total_events < 25:
        uncertainty.append("low_data_density")
    if quality_health is None:
        uncertainty.append("quality_health_projection_missing")

    summary_suffix = ", ".join(top_labels) if top_labels else "no dominant event types"
    summary = (
        f"Objective '{objective}' analyzed over {horizon_days} days with "
        f"{total_events} events. Top signals: {summary_suffix}."
    )

    evidence_refs: list[dict[str, Any]] = [
        {
            "kind": "events.aggregate",
            "window_days": horizon_days,
            "total_events": total_events,
            "top_event_types": top_event_types[:5],
        }
    ]
    if quality_health is not None:
        evidence_refs.append(
            {
                "kind": "projection.quality_health",
                "projection_type": "quality_health",
                "key": "overview",
                "updated_at": quality_health.get("updated_at"),
            }
        )

    return {
        "schema_version": ANALYSIS_RESULT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "objective": objective,
        "window_days": horizon_days,
        "focus": focus,
        "summary": summary,
        "highlights": [
            {
                "label": "event_volume",
                "value": total_events,
                "detail": f"Events observed in the last {horizon_days} days.",
            },
            {
                "label": "top_event_types",
                "value": top_event_types[:3],
                "detail": "Most frequent event categories in analysis window.",
            },
        ],
        "evidence_refs": evidence_refs,
        "uncertainty": uncertainty,
    }


async def _mark_completed(
    conn: psycopg.AsyncConnection[Any],
    *,
    analysis_job_id: str,
    result_payload: dict[str, Any],
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE analysis_jobs
            SET status = 'completed',
                result_payload = %s,
                error_code = NULL,
                error_message = NULL,
                completed_at = NOW()
            WHERE id = %s
            """,
            (Json(result_payload), analysis_job_id),
        )


async def _mark_failed(
    conn: psycopg.AsyncConnection[Any],
    *,
    analysis_job_id: str,
    error_code: str,
    error_message: str,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE analysis_jobs
            SET status = 'failed',
                error_code = %s,
                error_message = %s,
                completed_at = NOW()
            WHERE id = %s
            """,
            (error_code, error_message[:2000], analysis_job_id),
        )


@register("analysis.deep_insight")
async def handle_deep_analysis(conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]) -> None:
    analysis_job_id = str(payload.get("analysis_job_id") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    if not analysis_job_id or not user_id:
        raise ValueError("analysis.deep_insight payload requires analysis_job_id + user_id")

    job = await _fetch_analysis_job(conn, analysis_job_id=analysis_job_id, user_id=user_id)
    if job is None:
        raise ValueError(f"analysis job not found: {analysis_job_id}")

    await _mark_processing(conn, analysis_job_id=analysis_job_id)

    objective = str(job.get("objective") or "").strip() or "general insight"
    horizon_days = int(job.get("horizon_days") or 90)
    focus = _safe_focus_list(job.get("focus"))

    try:
        event_type_counts = await _load_event_type_counts(
            conn,
            user_id=user_id,
            horizon_days=horizon_days,
        )
        quality_health = await _load_quality_health_context(conn, user_id=user_id)
        result_payload = build_deep_analysis_result(
            objective=objective,
            horizon_days=horizon_days,
            focus=focus,
            event_type_counts=event_type_counts,
            quality_health=quality_health,
        )
    except Exception as exc:
        logger.exception("analysis.deep_insight failed for job_id=%s", analysis_job_id)
        await _mark_failed(
            conn,
            analysis_job_id=analysis_job_id,
            error_code="analysis_processing_failed",
            error_message=str(exc),
        )
        return

    await _mark_completed(
        conn,
        analysis_job_id=analysis_job_id,
        result_payload=result_payload,
    )
