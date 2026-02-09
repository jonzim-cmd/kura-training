"""Projection update router.

Dispatches projection.update jobs to all registered projection handlers
based on the event_type. One event can trigger multiple projection updates.

Failed handlers get a separate projection.retry job — reusing the existing
background_jobs pipeline for exponential backoff and dead-letter tracking.

Concurrent projection updates for the same user are serialized via
pg_advisory_xact_lock (transaction-scoped, auto-releases on commit/rollback).
"""

import logging
from typing import Any

import psycopg
from psycopg.types.json import Json

from ..registry import (
    get_projection_handler_by_name,
    get_projection_handlers,
    register,
    registered_event_types,
)

logger = logging.getLogger(__name__)


async def _acquire_user_lock(
    conn: psycopg.AsyncConnection[Any], user_id: str
) -> None:
    """Serialize all projection work for the same user."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
        (str(user_id),),
    )


@register("projection.update")
async def handle_projection_update(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Route projection.update jobs to all registered handlers for the event_type."""
    event_type = payload.get("event_type", "")
    user_id = payload.get("user_id")
    if not user_id:
        raise ValueError(f"Missing user_id in projection.update payload (event_type={event_type})")

    handlers = get_projection_handlers(event_type)
    if not handlers:
        logger.debug("No projection handlers for event_type=%s, skipping", event_type)
        return

    await _acquire_user_lock(conn, user_id)

    for handler in handlers:
        try:
            async with conn.transaction():
                await handler(conn, payload)
        except Exception:
            logger.exception(
                "Projection handler %s failed for event_type=%s event_id=%s — scheduling retry",
                handler.__name__, event_type, payload.get("event_id", "?"),
            )
            # Enqueue a targeted retry job for this specific handler.
            # This INSERT is outside the failed handler's transaction block,
            # so it's part of the outer projection.update transaction that will commit.
            try:
                await conn.execute(
                    """
                    INSERT INTO background_jobs (user_id, job_type, payload, max_retries)
                    VALUES (%s, 'projection.retry', %s, 3)
                    """,
                    (
                        user_id,
                        Json({**payload, "handler_name": handler.__name__}),
                    ),
                )
            except Exception:
                logger.exception(
                    "CRITICAL: Failed to enqueue retry job for handler %s — failure will be lost",
                    handler.__name__,
                )


@register("projection.retry")
async def handle_projection_retry(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Retry a single failed projection handler by name.

    If the handler fails again, the worker's _process_job handles
    attempt counting, exponential backoff, and dead-letter marking.
    """
    handler_name = payload.get("handler_name", "")
    user_id = payload.get("user_id")
    if not user_id:
        raise ValueError(f"Missing user_id in projection.retry payload (handler={handler_name})")

    await _acquire_user_lock(conn, user_id)

    handler = get_projection_handler_by_name(handler_name)
    if handler is None:
        raise ValueError(f"Unknown projection handler for retry: {handler_name!r}")

    logger.info(
        "Retrying handler %s for event_type=%s user=%s",
        handler_name, payload.get("event_type", "?"), user_id,
    )
    await handler(conn, payload)
