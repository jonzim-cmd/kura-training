import logging
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

# Handler signature: async def handler(conn: AsyncConnection, payload: dict) -> None
HandlerFn = Callable[[psycopg.AsyncConnection[Any], dict[str, Any]], Awaitable[None]]

# Job-level registry: one handler per job_type
_registry: dict[str, HandlerFn] = {}

# Projection-level registry: multiple handlers per event_type
# Each handler updates one projection type for a given event_type
_projection_handlers: dict[str, list[HandlerFn]] = {}


def register(job_type: str) -> Callable[[HandlerFn], HandlerFn]:
    """Register a handler for a job_type (e.g. 'projection.update')."""

    def decorator(fn: HandlerFn) -> HandlerFn:
        if job_type in _registry:
            raise ValueError(f"Duplicate handler for job_type={job_type!r}")
        _registry[job_type] = fn
        logger.info("Registered handler for job_type=%s", job_type)
        return fn

    return decorator


def projection_handler(*event_types: str) -> Callable[[HandlerFn], HandlerFn]:
    """Register a projection handler for one or more event_types.

    Multiple handlers can register for the same event_type — all will be called.
    This allows one event to update multiple projections.

    Usage:
        @projection_handler("set.logged")
        async def update_exercise_progression(conn, payload):
            ...

        @projection_handler("set.logged", "exercise.alias_created", "preference.set")
        async def update_user_profile(conn, payload):
            ...
    """

    def decorator(fn: HandlerFn) -> HandlerFn:
        for et in event_types:
            _projection_handlers.setdefault(et, []).append(fn)
            logger.info("Registered projection handler %s for event_type=%s", fn.__name__, et)
        return fn

    return decorator


def get_handler(job_type: str) -> HandlerFn | None:
    return _registry.get(job_type)


def get_projection_handlers(event_type: str) -> list[HandlerFn]:
    return _projection_handlers.get(event_type, [])


def registered_types() -> list[str]:
    return list(_registry.keys())


def registered_event_types() -> list[str]:
    return list(_projection_handlers.keys())
