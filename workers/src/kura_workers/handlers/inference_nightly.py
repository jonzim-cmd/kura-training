"""Recurring nightly inference refresh job."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..consistency_inbox import refresh_all_consistency_inboxes
from ..extraction_calibration import refresh_extraction_calibration
from ..inference_event_registry import (
    CAPABILITY_BACKFILL_TRIGGER_EVENT_TYPES,
    NIGHTLY_REFIT_TRIGGER_EVENT_TYPES,
    OBJECTIVE_BACKFILL_TRIGGER_EVENT_TYPES,
)
from ..issue_clustering import refresh_issue_clusters
from ..learning_backlog_bridge import refresh_learning_backlog_candidates
from ..population_priors import refresh_population_prior_profiles
from ..registry import register
from ..scheduler import nightly_interval_hours
from ..unknown_dimension_mining import refresh_unknown_dimension_proposals

logger = logging.getLogger(__name__)


async def _latest_event_id_for_type(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    event_type: str,
) -> str | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id
            FROM events
            WHERE user_id = %s
              AND event_type = %s
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            (user_id, event_type),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return str(row["id"])


async def _candidate_user_ids_for_event_types(
    conn: psycopg.AsyncConnection[Any],
    *,
    event_types: tuple[str, ...],
) -> list[str]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT DISTINCT user_id
            FROM events
            WHERE event_type = ANY(%s)
            ORDER BY user_id
            """,
            (list(event_types),),
        )
        rows = await cur.fetchall()
    return [str(row["user_id"]) for row in rows]


def _coerce_event_types(
    raw_event_types: Any,
    *,
    default: tuple[str, ...],
    allowed: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(raw_event_types, list):
        return default
    allowed_set = set(allowed)
    selected = [
        event_type
        for event_type in [str(value or "").strip() for value in raw_event_types]
        if event_type in allowed_set
    ]
    if not selected:
        return default
    return tuple(dict.fromkeys(selected))


def _coerce_user_ids(raw_user_ids: Any) -> list[str]:
    if not isinstance(raw_user_ids, list):
        return []
    selected = [str(value or "").strip() for value in raw_user_ids]
    return [user_id for user_id in selected if user_id]


def _coerce_bool(raw: Any, *, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


async def _enqueue_projection_update_dedup(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    event_type: str,
    event_id: str,
    source: str,
) -> bool:
    """Enqueue projection.update once per user/event_type/source while in-flight."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            INSERT INTO background_jobs (user_id, job_type, payload)
            SELECT %s, 'projection.update', %s
            WHERE NOT EXISTS (
                SELECT 1
                FROM background_jobs
                WHERE job_type = 'projection.update'
                  AND status IN ('pending', 'processing')
                  AND payload->>'source' = %s
                  AND payload->>'user_id' = %s
                  AND payload->>'event_type' = %s
            )
            RETURNING id
            """,
            (
                user_id,
                Json(
                    {
                        "event_id": event_id,
                        "event_type": event_type,
                        "user_id": user_id,
                        "source": source,
                    }
                ),
                source,
                user_id,
                event_type,
            ),
        )
        row = await cur.fetchone()
    return row is not None


def _synthetic_projection_event_id(*, user_id: str, event_type: str, source: str) -> str:
    return str(
        uuid.uuid5(
            uuid.uuid5(uuid.NAMESPACE_URL, f"kura:{source}:{event_type}"),
            user_id,
        )
    )


async def _enqueue_projection_updates_for_user_set(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_ids: list[str],
    event_types: tuple[str, ...],
    source: str,
    synthetic_event_type: str | None = None,
) -> int:
    enqueued = 0
    for user_id in user_ids:
        for event_type in event_types:
            latest_event_id = await _latest_event_id_for_type(conn, user_id, event_type)
            if latest_event_id is None:
                if synthetic_event_type and event_type == synthetic_event_type:
                    latest_event_id = _synthetic_projection_event_id(
                        user_id=user_id,
                        event_type=event_type,
                        source=source,
                    )
                else:
                    continue
            inserted = await _enqueue_projection_update_dedup(
                conn,
                user_id=user_id,
                event_type=event_type,
                event_id=latest_event_id,
                source=source,
            )
            if inserted:
                enqueued += 1
    return enqueued


@register("inference.nightly_refit")
async def handle_inference_nightly_refit(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Enqueue projection refresh jobs for nightly inference maintenance."""
    interval_h = int(payload.get("interval_hours", nightly_interval_hours()))
    scheduler_key = str(payload.get("scheduler_key") or "").strip()
    missed_runs = int(payload.get("missed_runs") or 0)
    event_types = NIGHTLY_REFIT_TRIGGER_EVENT_TYPES
    user_ids = await _candidate_user_ids_for_event_types(conn, event_types=event_types)
    source = "inference.nightly_refit"
    enqueued = await _enqueue_projection_updates_for_user_set(
        conn,
        user_ids=user_ids,
        event_types=event_types,
        source=source,
    )

    population_prior_summary: dict[str, Any] | None = None
    try:
        population_prior_summary = await refresh_population_prior_profiles(conn)
    except Exception as exc:
        logger.warning("Population prior refresh skipped due to error: %s", exc)

    issue_cluster_summary: dict[str, Any] | None = None
    try:
        issue_cluster_summary = await refresh_issue_clusters(conn)
    except Exception as exc:
        logger.warning("Issue clustering refresh skipped due to error: %s", exc)

    extraction_calibration_summary: dict[str, Any] | None = None
    try:
        extraction_calibration_summary = await refresh_extraction_calibration(conn)
    except Exception as exc:
        logger.warning("Extraction calibration refresh skipped due to error: %s", exc)

    unknown_dimension_summary: dict[str, Any] | None = None
    try:
        unknown_dimension_summary = await refresh_unknown_dimension_proposals(conn)
    except Exception as exc:
        logger.warning("Unknown-dimension mining refresh skipped due to error: %s", exc)

    learning_backlog_summary: dict[str, Any] | None = None
    try:
        learning_backlog_summary = await refresh_learning_backlog_candidates(conn)
    except Exception as exc:
        logger.warning("Learning backlog refresh skipped due to error: %s", exc)

    consistency_inbox_summary: dict[str, Any] | None = None
    try:
        consistency_inbox_summary = await refresh_all_consistency_inboxes(conn)
    except Exception as exc:
        logger.warning("Consistency inbox refresh skipped due to error: %s", exc)

    if scheduler_key:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE inference_scheduler_state
                SET last_enqueued_projection_updates = %s,
                    last_missed_runs = %s,
                    updated_at = NOW()
                WHERE scheduler_key = %s
                """,
                (
                    enqueued,
                    max(0, missed_runs),
                    scheduler_key,
                ),
            )

    logger.info(
        "Nightly refit enqueued %d projection.update jobs across %d users (interval_h=%d, missed_runs=%d, population_priors=%s, issue_clusters=%s, extraction_calibration=%s, unknown_dimensions=%s, learning_backlog=%s, consistency_inbox=%s)",
        enqueued,
        len(user_ids),
        interval_h,
        max(0, missed_runs),
        population_prior_summary or {"status": "failed"},
        issue_cluster_summary or {"status": "failed"},
        extraction_calibration_summary or {"status": "failed"},
        unknown_dimension_summary or {"status": "failed"},
        learning_backlog_summary or {"status": "failed"},
        consistency_inbox_summary or {"status": "failed"},
    )


@register("inference.capability_backfill")
async def handle_inference_capability_backfill(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Run a one-shot capability-estimation projection backfill queue fan-out."""
    source = str(payload.get("source") or "inference.capability_backfill").strip()
    if not source:
        source = "inference.capability_backfill"

    event_types = _coerce_event_types(
        payload.get("event_types"),
        default=CAPABILITY_BACKFILL_TRIGGER_EVENT_TYPES,
        allowed=CAPABILITY_BACKFILL_TRIGGER_EVENT_TYPES,
    )

    requested_user_ids = _coerce_user_ids(payload.get("user_ids"))
    if requested_user_ids:
        user_ids = requested_user_ids
    else:
        user_ids = await _candidate_user_ids_for_event_types(conn, event_types=event_types)

    enqueued = await _enqueue_projection_updates_for_user_set(
        conn,
        user_ids=user_ids,
        event_types=event_types,
        source=source,
    )

    logger.info(
        "Capability backfill enqueued %d projection.update jobs across %d users (source=%s, event_types=%s)",
        enqueued,
        len(user_ids),
        source,
        ",".join(event_types),
    )


@register("inference.objective_backfill")
async def handle_inference_objective_backfill(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Run a one-shot objective/advisory/modality projection backfill fan-out."""
    source = str(payload.get("source") or "inference.objective_backfill").strip()
    if not source:
        source = "inference.objective_backfill"

    event_types = _coerce_event_types(
        payload.get("event_types"),
        default=OBJECTIVE_BACKFILL_TRIGGER_EVENT_TYPES,
        allowed=OBJECTIVE_BACKFILL_TRIGGER_EVENT_TYPES,
    )

    requested_user_ids = _coerce_user_ids(payload.get("user_ids"))
    include_all_users = _coerce_bool(payload.get("include_all_users"), default=True)
    if requested_user_ids:
        user_ids = requested_user_ids
    else:
        user_ids = await _candidate_user_ids_for_event_types(conn, event_types=event_types)
        if include_all_users:
            logger.warning(
                "Objective backfill requested include_all_users=true without explicit user_ids; "
                "falling back to signal-derived users only."
            )

    enqueued = await _enqueue_projection_updates_for_user_set(
        conn,
        user_ids=user_ids,
        event_types=event_types,
        source=source,
        synthetic_event_type="profile.updated",
    )

    logger.info(
        "Objective backfill enqueued %d projection.update jobs across %d users (source=%s, include_all_users=%s, event_types=%s)",
        enqueued,
        len(user_ids),
        source,
        include_all_users,
        ",".join(event_types),
    )
