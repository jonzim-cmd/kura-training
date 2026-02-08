import logging
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

# Handler signature: async def handler(conn: AsyncConnection, payload: dict) -> None
HandlerFn = Callable[[psycopg.AsyncConnection[Any], dict[str, Any]], Awaitable[None]]

_registry: dict[str, HandlerFn] = {}


def register(job_type: str) -> Callable[[HandlerFn], HandlerFn]:
    """Decorator to register a handler for a job type.

    Usage:
        @register("projection.update")
        async def handle_projection_update(conn, payload):
            ...
    """

    def decorator(fn: HandlerFn) -> HandlerFn:
        if job_type in _registry:
            raise ValueError(f"Duplicate handler for job_type={job_type!r}")
        _registry[job_type] = fn
        logger.info("Registered handler for job_type=%s", job_type)
        return fn

    return decorator


def get_handler(job_type: str) -> HandlerFn | None:
    return _registry.get(job_type)


def registered_types() -> list[str]:
    return list(_registry.keys())
