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

# Projection handler by function name: for targeted retry dispatch
_handler_by_name: dict[str, HandlerFn] = {}

# Dimension metadata: declared by handlers at registration time (Decision 7)
# Maps dimension name → metadata dict (description, granularity, relates_to, etc.)
_dimension_metadata: dict[str, dict[str, Any]] = {}


def register(job_type: str) -> Callable[[HandlerFn], HandlerFn]:
    """Register a handler for a job_type (e.g. 'projection.update')."""

    def decorator(fn: HandlerFn) -> HandlerFn:
        if job_type in _registry:
            raise ValueError(f"Duplicate handler for job_type={job_type!r}")
        _registry[job_type] = fn
        logger.info("Registered handler for job_type=%s", job_type)
        return fn

    return decorator


def projection_handler(
    *event_types: str,
    dimension_meta: dict[str, Any] | None = None,
) -> Callable[[HandlerFn], HandlerFn]:
    """Register a projection handler for one or more event_types.

    Multiple handlers can register for the same event_type — all will be called.
    This allows one event to update multiple projections.

    Optionally declare dimension metadata (Decision 7) for the system layer.
    Handlers that are not dimensions (e.g. user_profile) omit dimension_meta.

    Usage:
        @projection_handler("set.logged", dimension_meta={
            "name": "exercise_progression",
            "description": "Strength progression per exercise over time",
            ...
        })
        async def update_exercise_progression(conn, payload):
            ...
    """

    def decorator(fn: HandlerFn) -> HandlerFn:
        for et in event_types:
            _projection_handlers.setdefault(et, []).append(fn)
            logger.info("Registered projection handler %s for event_type=%s", fn.__name__, et)

        _handler_by_name[fn.__name__] = fn

        if dimension_meta is not None:
            name = dimension_meta.get("name")
            if not name:
                raise ValueError(
                    f"dimension_meta must include 'name' for handler {fn.__name__}"
                )
            if name in _dimension_metadata:
                raise ValueError(f"Duplicate dimension_meta name={name!r}")
            _dimension_metadata[name] = {
                **dimension_meta,
                "event_types": list(event_types),
            }
            logger.info("Registered dimension metadata for %s", name)

        return fn

    return decorator


def get_handler(job_type: str) -> HandlerFn | None:
    return _registry.get(job_type)


def get_projection_handlers(event_type: str) -> list[HandlerFn]:
    return _projection_handlers.get(event_type, [])


def get_projection_handler_by_name(name: str) -> HandlerFn | None:
    """Look up a projection handler by function name (for targeted retry)."""
    return _handler_by_name.get(name)


def registered_types() -> list[str]:
    return list(_registry.keys())


def registered_event_types() -> list[str]:
    return list(_projection_handlers.keys())


def get_dimension_metadata() -> dict[str, dict[str, Any]]:
    """Return all declared dimension metadata (Decision 7 system layer source)."""
    return dict(_dimension_metadata)
