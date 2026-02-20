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

from ..capability_estimation_runtime import (
    STATUS_INSUFFICIENT_DATA,
    STATUS_OK,
    build_capability_envelope,
    build_insufficient_envelope,
    confidence_from_evidence,
    data_sufficiency_block,
    effort_adjusted_e1rm,
)
from ..inference_engine import run_strength_inference, weekly_phase_from_date
from ..inference_telemetry import (
    INFERENCE_ERROR_INSUFFICIENT_DATA,
    classify_inference_error,
    safe_record_inference_run,
)
from ..population_priors import resolve_population_prior
from ..registry import projection_handler
from ..session_block_expansion import expand_session_logged_row
from ..training_signal_normalization import normalize_training_signal_rows
from ..utils import (
    SessionBoundaryState,
    find_all_keys_for_canonical,
    get_alias_map,
    get_retracted_event_ids,
    load_timezone_preference,
    next_fallback_session_key,
    normalize_temporal_point,
    resolve_exercise_key,
    resolve_timezone_context,
    resolve_through_aliases,
)

logger = logging.getLogger(__name__)


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"exercises": [r["key"] for r in projection_rows]}


@projection_handler("set.logged", "session.logged", "set.corrected", "exercise.alias_created", dimension_meta={
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
        "dynamics": {
            "estimated_1rm": {
                "value": "number",
                "velocity_per_day": "number|null",
                "velocity_per_week": "number|null",
                "acceleration_per_day2": "number|null",
                "trajectory_code": "string",
                "phase": "string",
                "direction": "string",
                "momentum": "string",
                "confidence": "number [0,1]",
                "samples": "integer",
                "model_velocity_per_day": "number (optional)",
                "model_velocity_ci95": "[number, number] (optional)",
            },
            "predicted_delta_kg": "number (optional)",
        },
        "phase": {
            "projection_phase": "string",
            "weekly_cycle": {
                "day_of_week": "string|null",
                "phase": "string",
                "angle_deg": "number|null",
                "bucket_index": "integer|null",
            },
        },
        "population_prior": {
            "applied": "boolean",
            "cohort_key": "string (optional)",
            "target_key": "string (optional)",
            "participants_count": "integer (optional)",
            "sample_size": "integer (optional)",
            "blend_weight": "number (optional)",
            "computed_at": "ISO 8601 datetime (optional)",
        },
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
        timezone_pref = await load_timezone_preference(conn, user_id, retracted_ids)
        timezone_context = resolve_timezone_context(timezone_pref)
        timezone_name = timezone_context["timezone"]

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, timestamp, data, metadata
                FROM events
                WHERE id = %s
                  AND user_id = %s
                """,
                (event_id, user_id),
            )
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

        event_data = row.get("data") if isinstance(row.get("data"), dict) else {}
        event_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}

        if event_type == "exercise.alias_created":
            canonical = str(event_data.get("exercise_id") or "").strip().lower()
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
        elif event_type == "session.logged":
            expanded = expand_session_logged_row(
                {
                    "id": row.get("id"),
                    "timestamp": row.get("timestamp"),
                    "data": event_data,
                    "metadata": event_metadata,
                }
            )
            raw_key = None
            for expanded_row in expanded:
                expanded_data = expanded_row.get("data") or {}
                if not isinstance(expanded_data, dict):
                    continue
                candidate = resolve_exercise_key(expanded_data)
                if candidate:
                    raw_key = candidate
                    break
            if not raw_key:
                await _record(
                    "skipped",
                    {
                        "skip_reason": "session_without_resolved_exercise",
                        "event_type": event_type,
                        "event_id": event_id,
                    },
                )
                return
            canonical = resolve_through_aliases(raw_key, alias_map)
        elif event_type == "set.corrected":
            raw_key = None
            changed_fields = event_data.get("changed_fields")
            if isinstance(changed_fields, dict):
                for field in ("exercise_id", "exercise"):
                    candidate = changed_fields.get(field)
                    if isinstance(candidate, dict) and "value" in candidate:
                        candidate = candidate.get("value")
                    if isinstance(candidate, str) and candidate.strip():
                        raw_key = candidate.strip().lower()
                        break
            if not raw_key:
                target_event_id = str(event_data.get("target_event_id") or "").strip()
                target_row: dict[str, Any] | None = None
                if target_event_id:
                    async with conn.cursor(row_factory=dict_row) as cur:
                        await cur.execute(
                            "SELECT data FROM events WHERE id = %s AND user_id = %s",
                            (target_event_id, user_id),
                        )
                        target_row = await cur.fetchone()
                if target_row and isinstance(target_row.get("data"), dict):
                    raw_key = resolve_exercise_key(target_row["data"])
            if not raw_key:
                await _record(
                    "skipped",
                    {
                        "skip_reason": "set_correction_without_resolved_exercise",
                        "event_type": event_type,
                        "event_id": event_id,
                    },
                )
                return
            canonical = resolve_through_aliases(raw_key, alias_map)
        else:
            raw_key = resolve_exercise_key(event_data)
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
                SELECT id, timestamp, event_type, data, metadata
                FROM events
                WHERE user_id = %s
                  AND event_type IN ('set.logged', 'session.logged', 'set.corrected')
                ORDER BY timestamp ASC
                """,
                (user_id,),
            )
            rows = await cur.fetchall()

        rows = [r for r in rows if str(r["id"]) not in retracted_ids]
        normalized_rows = normalize_training_signal_rows(rows, include_passthrough=False)

        rows = []
        for candidate_row in normalized_rows:
            data = candidate_row.get("data")
            if not isinstance(data, dict):
                continue
            raw_key = resolve_exercise_key(data)
            if not raw_key:
                continue
            resolved = resolve_through_aliases(raw_key, alias_map)
            if resolved in all_keys:
                rows.append(candidate_row)

        rows.sort(key=lambda entry: (entry.get("timestamp"), str(entry.get("id") or "")))
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
        e1rm_sources: dict[str, int] = {"explicit": 0, "inferred_from_rpe": 0, "fallback_epley": 0}
        temporal_conflicts: dict[str, int] = {}
        fallback_session_state: SessionBoundaryState | None = None
        for row in rows:
            data = row["data"]
            ts = row["timestamp"]
            metadata = row.get("metadata") or {}
            temporal = normalize_temporal_point(
                ts,
                timezone_name=timezone_name,
                data=data if isinstance(data, dict) else {},
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            ts = temporal.timestamp_utc
            local_day = temporal.local_date
            for conflict in temporal.conflicts:
                temporal_conflicts[conflict] = temporal_conflicts.get(conflict, 0) + 1

            try:
                weight = float(data.get("weight_kg", data.get("weight", 0)))
                reps = int(data.get("reps", 0))
            except (ValueError, TypeError):
                continue

            e1rm, e1rm_source = effort_adjusted_e1rm(
                weight,
                reps,
                rir=data.get("rir"),
                rpe=data.get("rpe"),
            )
            if e1rm <= 0:
                continue

            if e1rm_source not in e1rm_sources:
                e1rm_sources[e1rm_source] = 0
            e1rm_sources[e1rm_source] += 1

            raw_session_id = str(metadata.get("session_id") or "").strip()
            if raw_session_id:
                session_id = raw_session_id
                fallback_session_state = None
            else:
                session_id, fallback_session_state = next_fallback_session_key(
                    local_date=local_day,
                    timestamp_utc=ts,
                    state=fallback_session_state,
                )

            prev = session_best.get(session_id)
            if prev is None or e1rm > prev[1]:
                session_best[session_id] = (ts, e1rm)

            day_key = local_day.isoformat()
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

        population_prior = await resolve_population_prior(
            conn,
            user_id=user_id,
            projection_type="strength_inference",
            target_key=canonical,
            retracted_ids=retracted_ids,
        )
        inference = run_strength_inference(model_points, population_prior=population_prior)
        telemetry_engine = str(inference.get("engine", "none") or "none")
        history = [
            {"date": d, "estimated_1rm": round(v, 2)}
            for d, v in sorted(by_date_best.items())
        ][-120:]
        dynamics_snapshot = dict(inference.get("dynamics", {}))
        projection_phase = str(dynamics_snapshot.get("phase") or "unknown")
        weekly_cycle = weekly_phase_from_date(history[-1]["date"] if history else None)
        observed_points = len(points)
        required_points = int(inference.get("required_points", 3) or 3)
        insufficient = inference.get("status") == STATUS_INSUFFICIENT_DATA
        status = STATUS_INSUFFICIENT_DATA if insufficient else STATUS_OK
        confidence = confidence_from_evidence(
            observed_points=observed_points,
            required_points=required_points,
        )
        data_sufficiency = data_sufficiency_block(
            required_observations=required_points,
            observed_observations=observed_points,
            uncertainty_reason_codes=(
                ["insufficient_observation_count"] if insufficient else []
            )
            + (["effort_context_missing"] if e1rm_sources.get("fallback_epley", 0) > 0 else []),
            recommended_next_observations=(
                [
                    "Log additional heavy sets until at least three sessions are available.",
                    "Provide RIR or RPE to reduce e1RM uncertainty.",
                ]
                if insufficient
                else (
                    ["Provide RIR or RPE for more sets to improve confidence."]
                    if e1rm_sources.get("fallback_epley", 0) > 0
                    else []
                )
            ),
        )

        if insufficient:
            capability_estimation = build_insufficient_envelope(
                capability="strength_1rm",
                required_observations=required_points,
                observed_observations=observed_points,
                model_version="strength_inference.v2",
                recommended_next_observations=data_sufficiency.get(
                    "recommended_next_observations"
                ),
                protocol_signature={"projection_key": canonical},
                diagnostics={
                    "sessions_used": len(points),
                    "sets_used": len(rows),
                    "e1rm_source_counts": e1rm_sources,
                    "temporal_conflicts": temporal_conflicts,
                    "timezone": timezone_context.get("timezone"),
                },
            )
        else:
            capability_estimation = build_capability_envelope(
                capability="strength_1rm",
                estimate_mean=float((inference.get("estimated_1rm") or {}).get("mean", 0.0)),
                estimate_interval=(inference.get("estimated_1rm") or {}).get("ci95") or [None, None],
                status=status,
                confidence=confidence,
                data_sufficiency=data_sufficiency,
                model_version="strength_inference.v2",
                protocol_signature={"projection_key": canonical},
                diagnostics={
                    "sessions_used": len(points),
                    "sets_used": len(rows),
                    "e1rm_source_counts": e1rm_sources,
                    "temporal_conflicts": temporal_conflicts,
                    "timezone": timezone_context.get("timezone"),
                    "engine": inference.get("engine"),
                },
            )

        projection_data: dict[str, Any] = {
            "exercise_id": canonical,
            "history": history,
            "status": status,
            "confidence": confidence,
            "data_sufficiency": data_sufficiency,
            "estimate": {
                "mean": (inference.get("estimated_1rm") or {}).get("mean"),
                "interval": (inference.get("estimated_1rm") or {}).get("ci95"),
            },
            "capability_estimation": capability_estimation,
            "timezone_context": timezone_context,
            "dynamics": {"estimated_1rm": dynamics_snapshot},
            "phase": {
                "projection_phase": projection_phase,
                "weekly_cycle": weekly_cycle,
            },
            "data_quality": {
                "sessions_used": len(points),
                "sets_used": len(rows),
                "insufficient_data": inference.get("status") == "insufficient_data",
                "e1rm_source_counts": e1rm_sources,
                "temporal_conflicts": temporal_conflicts,
            },
            "diagnostics": inference.get("diagnostics", {}),
            "engine": inference.get("engine"),
            "population_prior": inference.get("population_prior", {"applied": False}),
        }

        telemetry_status = "success"
        telemetry_error_taxonomy: str | None = None
        telemetry_diagnostics = dict(inference.get("diagnostics", {}))
        if isinstance(inference.get("population_prior"), dict):
            telemetry_diagnostics["population_prior"] = inference["population_prior"]
        if inference.get("status") == "insufficient_data":
            projection_data["status"] = STATUS_INSUFFICIENT_DATA
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
            predicted_mean = inference["predicted_1rm"].get("mean")
            estimated_mean = inference["estimated_1rm"].get("mean")
            if isinstance(predicted_mean, (int, float)) and isinstance(estimated_mean, (int, float)):
                projection_data["dynamics"]["predicted_delta_kg"] = round(
                    float(predicted_mean) - float(estimated_mean), 2
                )

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
