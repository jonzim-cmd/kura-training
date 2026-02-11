"""Strength inference projection handler.

Bayesian per-exercise trend inference over estimated 1RM history.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..inference_engine import run_strength_inference
from ..inference_telemetry import (
    INFERENCE_ERROR_INSUFFICIENT_DATA,
    classify_inference_error,
    safe_record_inference_run,
)
from ..registry import projection_handler
from ..utils import (
    epley_1rm,
    find_all_keys_for_canonical,
    get_alias_map,
    get_retracted_event_ids,
    resolve_exercise_key,
    resolve_through_aliases,
)

logger = logging.getLogger(__name__)


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"exercises": [r["key"] for r in projection_rows]}


@projection_handler("set.logged", "exercise.alias_created", dimension_meta={
    "name": "strength_inference",
    "description": "Bayesian strength trend and near-term forecast per exercise",
    "key_structure": "one per exercise (exercise_id as key)",
    "projection_key": "<exercise_id>",
    "granularity": ["session", "week", "forecast"],
    "relates_to": {
        "exercise_progression": {"join": "exercise_id", "why": "same exercise history, probabilistic view"},
        "training_timeline": {"join": "week", "why": "volume/frequency context for trend shifts"},
    },
    "context_seeds": [
        "goals",
        "experience_level",
        "injuries",
        "training_modality",
    ],
    "output_schema": {
        "exercise_id": "string",
        "history": [{"date": "ISO 8601 date", "estimated_1rm": "number"}],
        "trend": {
            "slope_kg_per_day": "number",
            "slope_kg_per_week": "number",
            "slope_ci95": "[number, number]",
            "plateau_probability": "number",
            "improving_probability": "number",
        },
        "estimated_1rm": {"mean": "number", "ci95": "[number, number]"},
        "predicted_1rm": {"horizon_days": "integer", "mean": "number", "ci95": "[number, number]"},
        "diagnostics": "object",
        "data_quality": {
            "sessions_used": "integer",
            "sets_used": "integer",
            "insufficient_data": "boolean",
        },
    },
    "manifest_contribution": _manifest_contribution,
})
async def update_strength_inference(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    user_id = payload["user_id"]
    event_id = payload["event_id"]
    event_type = payload.get("event_type", "")
    started_at = datetime.now(timezone.utc)
    projection_key = "unknown"
    telemetry_engine = "none"

    async def _record(
        status: str,
        diagnostics: dict[str, Any],
        *,
        error_message: str | None = None,
        error_taxonomy: str | None = None,
    ) -> None:
        await safe_record_inference_run(
            conn,
            user_id=user_id,
            projection_type="strength_inference",
            key=projection_key,
            engine=telemetry_engine,
            status=status,
            diagnostics=diagnostics,
            error_message=error_message,
            error_taxonomy=error_taxonomy,
            started_at=started_at,
        )

    try:
        retracted_ids = await get_retracted_event_ids(conn, user_id)
        alias_map = await get_alias_map(conn, user_id, retracted_ids=retracted_ids)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT data FROM events WHERE id = %s", (event_id,))
            row = await cur.fetchone()
            if row is None:
                logger.warning("Strength inference event %s not found", event_id)
                await _record(
                    "skipped",
                    {
                        "skip_reason": "event_not_found",
                        "event_type": event_type,
                        "event_id": event_id,
                    },
                )
                return

        if event_type == "exercise.alias_created":
            canonical = row["data"].get("exercise_id", "").strip().lower()
            if not canonical:
                await _record(
                    "skipped",
                    {
                        "skip_reason": "alias_without_exercise_id",
                        "event_type": event_type,
                        "event_id": event_id,
                    },
                )
                return
        else:
            raw_key = resolve_exercise_key(row["data"])
            if not raw_key:
                await _record(
                    "skipped",
                    {
                        "skip_reason": "exercise_unresolved",
                        "event_type": event_type,
                        "event_id": event_id,
                    },
                )
                return
            canonical = resolve_through_aliases(raw_key, alias_map)

        projection_key = canonical
        all_keys = list(find_all_keys_for_canonical(canonical, alias_map))

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, timestamp, data, metadata
                FROM events
                WHERE user_id = %s
                  AND event_type = 'set.logged'
                  AND (
                      lower(trim(data->>'exercise_id')) = ANY(%s)
                      OR lower(trim(data->>'exercise')) = ANY(%s)
                  )
                ORDER BY timestamp ASC
                """,
                (user_id, all_keys, all_keys),
            )
            rows = await cur.fetchall()

        rows = [r for r in rows if str(r["id"]) not in retracted_ids]
        if not rows:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM projections
                    WHERE user_id = %s
                      AND projection_type = 'strength_inference'
                      AND key = %s
                    """,
                    (user_id, canonical),
                )
            await _record(
                "skipped",
                {
                    "skip_reason": "no_matching_sets",
                    "event_type": event_type,
                    "event_id": event_id,
                },
            )
            return

        # Aggregate best e1rm per session/day.
        session_best: dict[str, tuple[datetime, float]] = {}
        by_date_best: dict[str, float] = defaultdict(float)
        for row in rows:
            data = row["data"]
            ts = row["timestamp"]
            metadata = row.get("metadata") or {}

            try:
                weight = float(data.get("weight_kg", data.get("weight", 0)))
                reps = int(data.get("reps", 0))
            except (ValueError, TypeError):
                continue

            e1rm = epley_1rm(weight, reps)
            if e1rm <= 0:
                continue

            session_id = metadata.get("session_id") or ts.date().isoformat()
            prev = session_best.get(session_id)
            if prev is None or e1rm > prev[1]:
                session_best[session_id] = (ts, e1rm)

            day_key = ts.date().isoformat()
            if e1rm > by_date_best[day_key]:
                by_date_best[day_key] = e1rm

        points = sorted((ts, e1rm) for ts, e1rm in session_best.values())
        if not points:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM projections
                    WHERE user_id = %s
                      AND projection_type = 'strength_inference'
                      AND key = %s
                    """,
                    (user_id, canonical),
                )
            await _record(
                "skipped",
                {
                    "skip_reason": "no_valid_sets",
                    "event_type": event_type,
                    "event_id": event_id,
                },
            )
            return

        start_ts = points[0][0]
        model_points: list[tuple[float, float]] = []
        for ts, e1rm in points:
            day_offset = (ts - start_ts).total_seconds() / 86400.0
            model_points.append((day_offset, e1rm))

        inference = run_strength_inference(model_points)
        telemetry_engine = str(inference.get("engine", "none") or "none")
        history = [
            {"date": d, "estimated_1rm": round(v, 2)}
            for d, v in sorted(by_date_best.items())
        ][-120:]

        projection_data: dict[str, Any] = {
            "exercise_id": canonical,
            "history": history,
            "data_quality": {
                "sessions_used": len(points),
                "sets_used": len(rows),
                "insufficient_data": inference.get("status") == "insufficient_data",
            },
            "diagnostics": inference.get("diagnostics", {}),
            "engine": inference.get("engine"),
        }

        telemetry_status = "success"
        telemetry_error_taxonomy: str | None = None
        telemetry_diagnostics = dict(inference.get("diagnostics", {}))
        if inference.get("status") == "insufficient_data":
            projection_data["status"] = "insufficient_data"
            projection_data["required_points"] = inference.get("required_points", 3)
            projection_data["observed_points"] = inference.get("observed_points", len(points))
            telemetry_status = "skipped"
            telemetry_error_taxonomy = INFERENCE_ERROR_INSUFFICIENT_DATA
            telemetry_diagnostics.update(
                {
                    "skip_reason": "insufficient_data",
                    "required_points": inference.get("required_points", 3),
                    "observed_points": inference.get("observed_points", len(points)),
                }
            )
        else:
            projection_data["trend"] = inference["trend"]
            projection_data["estimated_1rm"] = inference["estimated_1rm"]
            projection_data["predicted_1rm"] = inference["predicted_1rm"]

        last_event_id = str(rows[-1]["id"])
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
                VALUES (%s, 'strength_inference', %s, %s, 1, %s, NOW())
                ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                    data = EXCLUDED.data,
                    version = projections.version + 1,
                    last_event_id = EXCLUDED.last_event_id,
                    updated_at = NOW()
                """,
                (user_id, canonical, json.dumps(projection_data), last_event_id),
            )

        logger.info(
            "Updated strength_inference for user=%s exercise=%s (sessions=%d, sets=%d)",
            user_id,
            canonical,
            len(points),
            len(rows),
        )
        await _record(
            telemetry_status,
            {
                **telemetry_diagnostics,
                "event_type": event_type,
                "event_id": event_id,
                "sessions_used": len(points),
                "sets_used": len(rows),
            },
            error_taxonomy=telemetry_error_taxonomy,
        )
    except Exception as exc:
        await _record(
            "failed",
            {
                "event_type": event_type,
                "event_id": event_id,
            },
            error_message=str(exc),
            error_taxonomy=classify_inference_error(exc),
        )
        raise
