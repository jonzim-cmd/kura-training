"""Recurring nightly inference refresh job."""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

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


@register("inference.nightly_refit")
async def handle_inference_nightly_refit(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Enqueue projection refresh jobs and schedule the next nightly run."""
    interval_h = int(payload.get("interval_hours", nightly_interval_hours()))
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
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO background_jobs (user_id, job_type, payload)
                    VALUES (%s, 'projection.update', %s)
                    """,
                    (
                        user_id,
                        Json(
                            {
                                "event_id": latest_event_id,
                                "event_type": event_type,
                                "user_id": user_id,
                                "source": "inference.nightly_refit",
                            }
                        ),
                    ),
                )
            enqueued += 1

    # Re-schedule recurring job.
    if user_ids:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO background_jobs (user_id, job_type, payload, scheduled_for)
                VALUES (%s, 'inference.nightly_refit', %s, NOW() + make_interval(hours => %s))
                """,
                (
                    user_ids[0],
                    Json({"interval_hours": interval_h}),
                    float(interval_h),
                ),
            )

    logger.info(
        "Nightly refit enqueued %d projection.update jobs across %d users (next in %dh)",
        enqueued,
        len(user_ids),
        interval_h,
    )
