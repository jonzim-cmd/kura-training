"""In-memory worker metrics.

Asyncio is single-threaded, so plain dicts are safe — no locking needed.
"""

import time

_start_time = time.monotonic()

_metrics: dict = {
    "jobs_processed": 0,
    "jobs_failed": 0,
    "jobs_dead": 0,
    "handlers": {},
}


def record_handler_invocation(handler_name: str, duration_ms: float, success: bool) -> None:
    """Record a single handler invocation with timing."""
    h = _metrics["handlers"].setdefault(handler_name, {
        "invocations": 0,
        "successes": 0,
        "failures": 0,
        "total_duration_ms": 0.0,
    })
    h["invocations"] += 1
    h["total_duration_ms"] += duration_ms
    if success:
        h["successes"] += 1
    else:
        h["failures"] += 1


def record_job_completed() -> None:
    _metrics["jobs_processed"] += 1


def record_job_failed() -> None:
    _metrics["jobs_failed"] += 1


def record_job_dead() -> None:
    _metrics["jobs_dead"] += 1


def get_metrics() -> dict:
    """Return a snapshot of current metrics."""
    return {
        "uptime_seconds": round(time.monotonic() - _start_time, 1),
        "jobs_processed": _metrics["jobs_processed"],
        "jobs_failed": _metrics["jobs_failed"],
        "jobs_dead": _metrics["jobs_dead"],
        "handlers": {
            name: dict(stats)
            for name, stats in _metrics["handlers"].items()
        },
    }
