import asyncio
import logging
import signal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import Config
from .registry import get_handler

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._shutdown = asyncio.Event()

    async def run(self) -> None:
        """Main entry point: run listen + poll loops until shutdown."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_shutdown)

        logger.info(
            "Worker starting (poll_interval=%.1fs, batch_size=%d)",
            self.config.poll_interval_seconds,
            self.config.batch_size,
        )

        # Run LISTEN and poll concurrently
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._listen_loop())
            tg.create_task(self._poll_loop())

    def _request_shutdown(self) -> None:
        logger.info("Shutdown requested")
        self._shutdown.set()

    async def _listen_loop(self) -> None:
        """LISTEN on kura_jobs channel for instant wake-up on new events."""
        while not self._shutdown.is_set():
            try:
                async with await psycopg.AsyncConnection.connect(
                    self.config.database_url, autocommit=True
                ) as conn:
                    await conn.execute("LISTEN kura_jobs")
                    logger.info("Listening on kura_jobs channel")

                    gen = conn.notifies(timeout=self.config.poll_interval_seconds)
                    async for notify in gen:
                        logger.debug("NOTIFY received: %s", notify.payload)
                        await self._process_batch()
                        if self._shutdown.is_set():
                            break
            except psycopg.OperationalError:
                if self._shutdown.is_set():
                    break
                logger.warning("LISTEN connection lost, reconnecting in 5s")
                await asyncio.sleep(5)

        logger.info("Listen loop stopped")

    async def _poll_loop(self) -> None:
        """Fallback polling loop — catches anything LISTEN misses."""
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=self.config.poll_interval_seconds,
                )
                break  # shutdown was set
            except TimeoutError:
                pass

            await self._process_batch()

        logger.info("Poll loop stopped")

    async def _process_batch(self) -> None:
        """Claim and process a batch of pending jobs."""
        try:
            async with await psycopg.AsyncConnection.connect(
                self.config.database_url
            ) as conn:
                # Assume app_worker role for BYPASSRLS (cross-user event/projection access)
                await conn.execute("SET ROLE app_worker")
                await conn.commit()

                jobs = await self._claim_jobs(conn)
                await conn.commit()  # Commit claims immediately so they survive crashes

                for job in jobs:
                    await self._process_job(conn, job)
        except Exception:
            logger.exception("Error in process_batch")

    async def _claim_jobs(
        self, conn: psycopg.AsyncConnection[Any]
    ) -> list[dict[str, Any]]:
        """Claim pending jobs using SELECT FOR UPDATE SKIP LOCKED."""
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                UPDATE background_jobs
                SET status = 'processing', started_at = NOW(), attempt = attempt + 1
                WHERE id IN (
                    SELECT id FROM background_jobs
                    WHERE status = 'pending' AND scheduled_for <= NOW()
                    ORDER BY scheduled_for, priority DESC, id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, user_id, job_type, payload, attempt, max_retries
                """,
                (self.config.batch_size,),
            )
            return await cur.fetchall()

    async def _process_job(
        self, conn: psycopg.AsyncConnection[Any], job: dict[str, Any]
    ) -> None:
        """Process a single job. Each job runs in its own transaction."""
        job_id = job["id"]
        job_type = job["job_type"]

        handler = get_handler(job_type)
        if handler is None:
            logger.warning("No handler for job_type=%s (job_id=%d)", job_type, job_id)
            await self._fail_job(conn, job_id, f"No handler for job_type={job_type}")
            return

        try:
            # Handler + job completion in one transaction — no crash window
            async with conn.transaction():
                await handler(conn, job["payload"])
                await conn.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'completed', completed_at = NOW()
                    WHERE id = %s
                    """,
                    (job_id,),
                )
            logger.info("Job %d completed (type=%s)", job_id, job_type)

        except Exception as exc:
            await conn.rollback()
            logger.exception("Job %d failed (type=%s)", job_id, job_type)

            attempt = job["attempt"]
            max_retries = job["max_retries"]

            if attempt >= max_retries:
                await self._dead_job(conn, job_id, str(exc))
            else:
                await self._retry_job(conn, job_id, attempt, str(exc))

    async def _fail_job(
        self, conn: psycopg.AsyncConnection[Any], job_id: int, error: str
    ) -> None:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE background_jobs
                SET status = 'dead', error_message = %s, completed_at = NOW()
                WHERE id = %s
                """,
                (error, job_id),
            )
        await conn.commit()

    async def _dead_job(
        self, conn: psycopg.AsyncConnection[Any], job_id: int, error: str
    ) -> None:
        logger.error("Job %d is dead after max retries: %s", job_id, error)
        await self._fail_job(conn, job_id, error)

    async def _retry_job(
        self,
        conn: psycopg.AsyncConnection[Any],
        job_id: int,
        attempt: int,
        error: str,
    ) -> None:
        backoff_seconds = 2**attempt
        logger.info("Job %d retrying in %ds (attempt=%d)", job_id, backoff_seconds, attempt)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE background_jobs
                SET status = 'pending',
                    error_message = %s,
                    scheduled_for = NOW() + make_interval(secs => %s)
                WHERE id = %s
                """,
                (error, float(backoff_seconds), job_id),
            )
        await conn.commit()
