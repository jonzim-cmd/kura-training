"""Recurring nightly inference refresh job."""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..population_priors import refresh_population_prior_profiles
from ..registry import register
from ..scheduler import nightly_interval_hours

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


async def _enqueue_projection_update_dedup(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    event_type: str,
    event_id: str,
) -> bool:
    """Enqueue nightly projection update once per user/event_type while in-flight."""
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
                  AND payload->>'source' = 'inference.nightly_refit'
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
                        "source": "inference.nightly_refit",
                    }
                ),
                user_id,
                event_type,
            ),
        )
        row = await cur.fetchone()
    return row is not None


@register("inference.nightly_refit")
async def handle_inference_nightly_refit(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Enqueue projection refresh jobs for nightly inference maintenance."""
    interval_h = int(payload.get("interval_hours", nightly_interval_hours()))
    scheduler_key = str(payload.get("scheduler_key") or "").strip()
    missed_runs = int(payload.get("missed_runs") or 0)
    event_types = (
        "set.logged",
        "exercise.alias_created",
        "sleep.logged",
        "soreness.logged",
        "energy.logged",
    )

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT DISTINCT user_id FROM events ORDER BY user_id")
        user_rows = await cur.fetchall()

    user_ids = [str(r["user_id"]) for r in user_rows]

    enqueued = 0
    for user_id in user_ids:
        for event_type in event_types:
            latest_event_id = await _latest_event_id_for_type(conn, user_id, event_type)
            if latest_event_id is None:
                continue
            inserted = await _enqueue_projection_update_dedup(
                conn,
                user_id=user_id,
                event_type=event_type,
                event_id=latest_event_id,
            )
            if inserted:
                enqueued += 1

    population_prior_summary: dict[str, Any] | None = None
    try:
        population_prior_summary = await refresh_population_prior_profiles(conn)
    except Exception as exc:
        logger.warning("Population prior refresh skipped due to error: %s", exc)

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
        "Nightly refit enqueued %d projection.update jobs across %d users (interval_h=%d, missed_runs=%d, population_priors=%s)",
        enqueued,
        len(user_ids),
        interval_h,
        max(0, missed_runs),
        population_prior_summary or {"status": "failed"},
    )
