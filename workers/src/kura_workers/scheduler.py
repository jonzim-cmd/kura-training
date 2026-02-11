"""Recurring job bootstrap helpers."""

from __future__ import annotations

import logging
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


def nightly_interval_hours() -> int:
    raw = os.environ.get("KURA_NIGHTLY_REFIT_HOURS", "24")
    try:
        return max(1, int(raw))
    except ValueError:
        return 24


async def ensure_nightly_inference_job(conn: psycopg.AsyncConnection[Any]) -> None:
    """Ensure one pending recurring inference refit job exists."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE job_type = 'inference.nightly_refit'
              AND status IN ('pending', 'processing')
            ORDER BY id DESC
            LIMIT 1
            """
        )
        existing = await cur.fetchone()

    if existing is not None:
        return

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT user_id
            FROM events
            ORDER BY timestamp DESC
            LIMIT 1
            """
        )
        seed_row = await cur.fetchone()

    if seed_row is None:
        logger.info("No events yet; skipping inference.nightly_refit scheduling")
        return

    interval_h = nightly_interval_hours()
    seed_user_id = seed_row["user_id"]
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO background_jobs (user_id, job_type, payload, scheduled_for)
            VALUES (%s, 'inference.nightly_refit', %s, NOW() + make_interval(hours => %s))
            """,
            (
                seed_user_id,
                {"interval_hours": interval_h},
                float(interval_h),
            ),
        )

    logger.info("Scheduled recurring inference.nightly_refit job (interval_hours=%d)", interval_h)
