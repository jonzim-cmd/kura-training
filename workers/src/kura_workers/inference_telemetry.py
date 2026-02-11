"""Inference telemetry persistence and error taxonomy helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.types.json import Json

logger = logging.getLogger(__name__)

INFERENCE_ERROR_INSUFFICIENT_DATA = "insufficient_data"
INFERENCE_ERROR_NUMERIC_INSTABILITY = "numeric_instability"
INFERENCE_ERROR_ENGINE_UNAVAILABLE = "engine_unavailable"
INFERENCE_ERROR_UNEXPECTED = "unexpected"

INFERENCE_ERROR_TAXONOMY = (
    INFERENCE_ERROR_INSUFFICIENT_DATA,
    INFERENCE_ERROR_NUMERIC_INSTABILITY,
    INFERENCE_ERROR_ENGINE_UNAVAILABLE,
    INFERENCE_ERROR_UNEXPECTED,
)

_STATUS_VALUES = {"success", "failed", "skipped"}

_ERROR_HINTS: dict[str, tuple[str, ...]] = {
    INFERENCE_ERROR_INSUFFICIENT_DATA: (
        "insufficient data",
        "required_points",
        "observed_points",
        "not enough data",
        "too few points",
    ),
    INFERENCE_ERROR_NUMERIC_INSTABILITY: (
        "nan",
        "inf",
        "singular",
        "overflow",
        "underflow",
        "not positive definite",
        "determinant",
        "non-finite",
        "divide by zero",
    ),
    INFERENCE_ERROR_ENGINE_UNAVAILABLE: (
        "no module named",
        "module not found",
        "unavailable",
        "importerror",
        "pymc",
        "arviz",
    ),
}


def classify_inference_error(error: BaseException | str | None) -> str:
    """Map runtime failures to a stable taxonomy."""
    text = str(error or "").strip().lower()
    if not text:
        return INFERENCE_ERROR_UNEXPECTED

    for code in (
        INFERENCE_ERROR_INSUFFICIENT_DATA,
        INFERENCE_ERROR_NUMERIC_INSTABILITY,
        INFERENCE_ERROR_ENGINE_UNAVAILABLE,
    ):
        hints = _ERROR_HINTS[code]
        if any(token in text for token in hints):
            return code
    return INFERENCE_ERROR_UNEXPECTED


async def record_inference_run(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_type: str,
    key: str,
    engine: str,
    status: str,
    diagnostics: dict[str, Any] | None = None,
    error_message: str | None = None,
    error_taxonomy: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    """Persist one inference run row in inference_runs."""
    if status not in _STATUS_VALUES:
        raise ValueError(f"Unsupported inference run status: {status!r}")

    diag = dict(diagnostics or {})
    if error_taxonomy:
        diag["error_taxonomy"] = error_taxonomy

    start_ts = started_at or datetime.now(timezone.utc)
    end_ts = completed_at or datetime.now(timezone.utc)
    run_engine = (engine or "").strip() or "none"
    run_key = (key or "").strip() or "unknown"

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO inference_runs (
                user_id, projection_type, key, engine, status,
                diagnostics, error_message, started_at, completed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                projection_type,
                run_key,
                run_engine,
                status,
                Json(diag),
                error_message,
                start_ts,
                end_ts,
            ),
        )


async def safe_record_inference_run(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_type: str,
    key: str,
    engine: str,
    status: str,
    diagnostics: dict[str, Any] | None = None,
    error_message: str | None = None,
    error_taxonomy: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    """Best-effort telemetry write that never breaks projection processing."""
    try:
        await record_inference_run(
            conn,
            user_id=user_id,
            projection_type=projection_type,
            key=key,
            engine=engine,
            status=status,
            diagnostics=diagnostics,
            error_message=error_message,
            error_taxonomy=error_taxonomy,
            started_at=started_at,
            completed_at=completed_at,
        )
    except Exception as exc:
        logger.warning(
            "Inference telemetry write failed (projection_type=%s key=%s status=%s): %s",
            projection_type,
            key,
            status,
            exc,
        )
