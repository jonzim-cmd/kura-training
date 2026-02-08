"""Projection update router.

Dispatches projection.update jobs to all registered projection handlers
based on the event_type. One event can trigger multiple projection updates.
"""

import logging
from typing import Any

import psycopg

from ..registry import get_projection_handlers, register, registered_event_types

logger = logging.getLogger(__name__)


@register("projection.update")
async def handle_projection_update(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Route projection.update jobs to all registered handlers for the event_type."""
    event_type = payload.get("event_type", "")
    handlers = get_projection_handlers(event_type)

    if not handlers:
        logger.debug("No projection handlers for event_type=%s, skipping", event_type)
        return

    for handler in handlers:
        await handler(conn, payload)
