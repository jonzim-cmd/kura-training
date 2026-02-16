"""Durable recurring scheduler helpers for background maintenance jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

logger = logging.getLogger(__name__)

NIGHTLY_SCHEDULER_KEY = "nightly_inference_refit"
LOG_RETENTION_JOB_TYPE = "maintenance.log_retention"


def nightly_interval_hours() -> int:
    raw = os.environ.get("KURA_NIGHTLY_REFIT_HOURS", "24")
    try:
        return max(1, int(raw))
    except ValueError:
        return 24


def log_retention_interval_hours() -> int:
    raw = os.environ.get("KURA_LOG_RETENTION_INTERVAL_HOURS", "24")
    try:
        return max(1, int(raw))
    except ValueError:
        return 24


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def due_run_count(now: datetime, next_run_at: datetime, interval_hours: int) -> int:
    """Return how many runs are due, including missed catch-up slots."""
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")

    now_utc = _as_utc(now)
    next_run_utc = _as_utc(next_run_at)
    if now_utc < next_run_utc:
        return 0

    elapsed_seconds = (now_utc - next_run_utc).total_seconds()
    slot_seconds = interval_hours * 3600
    return int(elapsed_seconds // slot_seconds) + 1


async def ensure_nightly_inference_scheduler(conn: psycopg.AsyncConnection[Any]) -> None:
    """Maintain durable scheduler state and enqueue at most one in-flight job."""
    interval_h = nightly_interval_hours()

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO inference_scheduler_state (
                scheduler_key, interval_hours, next_run_at, last_run_status
            )
            VALUES (%s, %s, NOW() + make_interval(hours => %s), 'idle')
            ON CONFLICT (scheduler_key) DO NOTHING
            """,
            (NIGHTLY_SCHEDULER_KEY, interval_h, interval_h),
        )

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT scheduler_key, interval_hours, next_run_at, in_flight_job_id
            FROM inference_scheduler_state
            WHERE scheduler_key = %s
            FOR UPDATE
            """,
            (NIGHTLY_SCHEDULER_KEY,),
        )
        state = await cur.fetchone()
        if state is None:
            return

        if int(state["interval_hours"]) != interval_h:
            await cur.execute(
                """
                UPDATE inference_scheduler_state
                SET interval_hours = %s,
                    updated_at = NOW()
                WHERE scheduler_key = %s
                """,
                (interval_h, NIGHTLY_SCHEDULER_KEY),
            )

        in_flight_job_id = state["in_flight_job_id"]
        if in_flight_job_id is not None:
            await cur.execute(
                """
                SELECT status, error_message, completed_at
                FROM background_jobs
                WHERE id = %s
                """,
                (in_flight_job_id,),
            )
            job = await cur.fetchone()

            if job is None:
                await cur.execute(
                    """
                    UPDATE inference_scheduler_state
                    SET in_flight_job_id = NULL,
                        in_flight_started_at = NULL,
                        last_run_status = 'failed',
                        last_error = 'in-flight nightly refit job missing',
                        next_run_at = LEAST(next_run_at, NOW()),
                        updated_at = NOW()
                    WHERE scheduler_key = %s
                    """,
                    (NIGHTLY_SCHEDULER_KEY,),
                )
            elif job["status"] in ("pending", "processing"):
                await cur.execute(
                    """
                    UPDATE inference_scheduler_state
                    SET last_run_status = 'running',
                        updated_at = NOW()
                    WHERE scheduler_key = %s
                    """,
                    (NIGHTLY_SCHEDULER_KEY,),
                )
                return
            elif job["status"] == "completed":
                await cur.execute(
                    """
                    UPDATE inference_scheduler_state
                    SET in_flight_job_id = NULL,
                        in_flight_started_at = NULL,
                        last_run_completed_at = COALESCE(%s, NOW()),
                        last_run_status = 'completed',
                        last_error = NULL,
                        total_runs = total_runs + 1,
                        updated_at = NOW()
                    WHERE scheduler_key = %s
                    """,
                    (job["completed_at"], NIGHTLY_SCHEDULER_KEY),
                )
            elif job["status"] in ("dead", "failed"):
                await cur.execute(
                    """
                    UPDATE inference_scheduler_state
                    SET in_flight_job_id = NULL,
                        in_flight_started_at = NULL,
                        last_run_status = 'failed',
                        last_error = COALESCE(%s, 'nightly refit job failed'),
                        next_run_at = LEAST(next_run_at, NOW()),
                        updated_at = NOW()
                    WHERE scheduler_key = %s
                    """,
                    (job["error_message"], NIGHTLY_SCHEDULER_KEY),
                )

        await cur.execute(
            """
            SELECT interval_hours, next_run_at
            FROM inference_scheduler_state
            WHERE scheduler_key = %s
            FOR UPDATE
            """,
            (NIGHTLY_SCHEDULER_KEY,),
        )
        refreshed = await cur.fetchone()
        if refreshed is None:
            return

        interval_h = int(refreshed["interval_hours"])
        run_count = due_run_count(
            datetime.now(timezone.utc),
            refreshed["next_run_at"],
            interval_h,
        )
        if run_count == 0:
            return

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
            await cur.execute(
                """
                UPDATE inference_scheduler_state
                SET next_run_at = NOW() + make_interval(hours => %s),
                    last_run_status = 'idle',
                    updated_at = NOW()
                WHERE scheduler_key = %s
                """,
                (interval_h, NIGHTLY_SCHEDULER_KEY),
            )
            logger.info("No events yet; skipping inference.nightly_refit scheduling")
            return

        missed_runs = max(0, run_count - 1)
        payload = {
            "interval_hours": interval_h,
            "scheduler_key": NIGHTLY_SCHEDULER_KEY,
            "due_runs": run_count,
            "missed_runs": missed_runs,
        }
        await cur.execute(
            """
            INSERT INTO background_jobs (user_id, job_type, payload, scheduled_for)
            VALUES (%s, 'inference.nightly_refit', %s, NOW())
            RETURNING id
            """,
            (seed_row["user_id"], Json(payload)),
        )
        row = await cur.fetchone()
        if row is None:
            return
        new_job_id = int(row["id"])

        await cur.execute(
            """
            UPDATE inference_scheduler_state
            SET in_flight_job_id = %s,
                in_flight_started_at = NOW(),
                last_run_started_at = NOW(),
                last_run_status = 'running',
                next_run_at = next_run_at + make_interval(hours => %s),
                last_missed_runs = %s,
                total_catch_up_runs = total_catch_up_runs + %s,
                updated_at = NOW()
            WHERE scheduler_key = %s
            """,
            (
                new_job_id,
                interval_h * run_count,
                missed_runs,
                missed_runs,
                NIGHTLY_SCHEDULER_KEY,
            ),
        )

    logger.info(
        "Scheduled inference.nightly_refit (job_id=%d, due_runs=%d, missed_runs=%d, interval_h=%d)",
        new_job_id,
        run_count,
        missed_runs,
        interval_h,
    )


async def ensure_log_retention_job(conn: psycopg.AsyncConnection[Any]) -> None:
    """Schedule recurring log-retention cleanup as a single in-flight maintenance job."""
    interval_h = log_retention_interval_hours()
    now = datetime.now(timezone.utc)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE job_type = %s
              AND status IN ('pending', 'processing')
            ORDER BY scheduled_for ASC, id ASC
            LIMIT 1
            """,
            (LOG_RETENTION_JOB_TYPE,),
        )
        in_flight = await cur.fetchone()
        if in_flight is not None:
            return

        await cur.execute(
            """
            SELECT completed_at
            FROM background_jobs
            WHERE job_type = %s
              AND status = 'completed'
              AND completed_at IS NOT NULL
            ORDER BY completed_at DESC
            LIMIT 1
            """,
            (LOG_RETENTION_JOB_TYPE,),
        )
        last_completed = await cur.fetchone()
        if last_completed is not None:
            completed_at = _as_utc(last_completed["completed_at"])
            if completed_at + timedelta(hours=interval_h) > now:
                return

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
            logger.info("No users/events yet; skipping maintenance.log_retention scheduling")
            return

        payload = {
            "interval_hours": interval_h,
            "scheduler_key": "maintenance.log_retention",
            "scheduled_at": now.isoformat(),
        }
        await cur.execute(
            """
            INSERT INTO background_jobs (
                user_id, job_type, payload, scheduled_for, priority, max_retries
            )
            VALUES (%s, %s, %s, NOW(), 50, 5)
            RETURNING id
            """,
            (seed_row["user_id"], LOG_RETENTION_JOB_TYPE, Json(payload)),
        )
        row = await cur.fetchone()
        if row is None:
            return
        job_id = int(row["id"])

    logger.info(
        "Scheduled maintenance.log_retention (job_id=%d, interval_h=%d)",
        job_id,
        interval_h,
    )


async def ensure_nightly_inference_job(conn: psycopg.AsyncConnection[Any]) -> None:
    """Backward-compatible wrapper for older call sites."""
    await ensure_nightly_inference_scheduler(conn)
