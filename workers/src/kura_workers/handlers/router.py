"""Projection update router.

Dispatches projection.update jobs to all registered projection handlers
based on the event_type. One event can trigger multiple projection updates.

Failed handlers get a separate projection.retry job — reusing the existing
background_jobs pipeline for exponential backoff and dead-letter tracking.

Concurrent projection updates for the same user are serialized via
pg_advisory_xact_lock (transaction-scoped, auto-releases on commit/rollback).
"""

import logging
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..metrics import record_handler_invocation
from ..registry import (
    get_projection_handler_by_name,
    get_projection_handlers,
    register,
    registered_event_types,
)

logger = logging.getLogger(__name__)


async def _resolve_retraction(
    conn: psycopg.AsyncConnection[Any], event_id: str
) -> dict[str, str] | None:
    """Resolve an event.retracted event to the retracted event's info.

    Returns {"event_id": retracted_event_id, "event_type": retracted_event_type}
    or None if the retraction cannot be resolved.

    Uses retracted_event_type from event data if available (recommended field),
    falls back to looking up the original event if not.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT data FROM events WHERE id = %s", (event_id,))
        row = await cur.fetchone()

    if not row:
        logger.warning("Retraction event %s not found", event_id)
        return None

    data = row["data"]
    retracted_event_id = data.get("retracted_event_id")
    if not retracted_event_id:
        logger.warning("Retraction event %s has no retracted_event_id", event_id)
        return None

    retracted_event_type = data.get("retracted_event_type")

    # Fall back to looking up the original event if type wasn't provided
    if not retracted_event_type:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT event_type FROM events WHERE id = %s",
                (retracted_event_id,),
            )
            orig = await cur.fetchone()
        if not orig:
            logger.warning(
                "Retracted event %s not found (referenced by retraction %s)",
                retracted_event_id, event_id,
            )
            return None
        retracted_event_type = orig["event_type"]

    return {"event_id": retracted_event_id, "event_type": retracted_event_type}


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
    """Route projection.update jobs to all registered handlers for the event_type.

    Special case: event.retracted events are resolved to the retracted event's
    type and re-routed to handlers for that type. This way handlers don't need
    retraction-specific logic — they just do their normal full replay with
    retraction filtering (via get_retracted_event_ids in each handler).
    """
    event_type = payload.get("event_type", "")
    user_id = payload.get("user_id")
    if not user_id:
        raise ValueError(f"Missing user_id in projection.update payload (event_type={event_type})")

    # Resolve retraction: re-route to the retracted event's handlers
    if event_type == "event.retracted":
        resolved = await _resolve_retraction(conn, payload["event_id"])
        if not resolved:
            return
        logger.info(
            "Retraction resolved: event %s (type=%s) — routing to handlers",
            resolved["event_id"], resolved["event_type"],
        )
        event_type = resolved["event_type"]
        payload = {**payload, "event_id": resolved["event_id"], "event_type": event_type}

    handlers = get_projection_handlers(event_type)
    if not handlers:
        logger.debug("No projection handlers for event_type=%s, skipping", event_type)
        return

    await _acquire_user_lock(conn, user_id)

    for handler in handlers:
        t0 = time.monotonic()
        try:
            async with conn.transaction():
                await handler(conn, payload)
            duration_ms = (time.monotonic() - t0) * 1000
            record_handler_invocation(handler.__name__, duration_ms, success=True)
        except Exception:
            duration_ms = (time.monotonic() - t0) * 1000
            record_handler_invocation(handler.__name__, duration_ms, success=False)
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
    t0 = time.monotonic()
    try:
        await handler(conn, payload)
        duration_ms = (time.monotonic() - t0) * 1000
        record_handler_invocation(handler_name, duration_ms, success=True)
    except Exception:
        duration_ms = (time.monotonic() - t0) * 1000
        record_handler_invocation(handler_name, duration_ms, success=False)
        raise
