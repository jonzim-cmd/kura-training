"""Cross-capability estimation projection for strength/sprint/jump/endurance."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..capability_estimation_runtime import (
    STATUS_DEGRADED_COMPARABILITY,
    STATUS_OK,
    build_capability_envelope,
    build_insufficient_envelope,
    confidence_from_evidence,
    data_sufficiency_block,
    effort_adjusted_e1rm,
    interval_around,
    summarize_observations,
)
from ..inference_event_registry import CAPABILITY_BACKFILL_TRIGGER_EVENT_TYPES
from ..registry import projection_handler
from ..training_load_v2 import infer_row_modality_with_context
from ..training_signal_normalization import normalize_training_signal_rows
from ..utils import (
    SessionBoundaryState,
    get_retracted_event_ids,
    load_timezone_preference,
    next_fallback_session_key,
    normalize_temporal_point,
    resolve_timezone_context,
)

MODEL_VERSION = "capability_estimation.v1"


@dataclass(frozen=True)
class CapabilityEnvelopeSpec:
    capability: str
    required_observations: int
    minimum_observations: int
    comparability_fields: tuple[str, ...]
    comparability_fallback: str
    insufficient_recommendations: tuple[str, ...]
    degraded_reason_code: str | None = None
    degraded_recommendations: tuple[str, ...] = ()
    degraded_caveat_code: str | None = None


CAPABILITY_SPECS: dict[str, CapabilityEnvelopeSpec] = {
    "strength_1rm": CapabilityEnvelopeSpec(
        capability="strength_1rm",
        required_observations=6,
        minimum_observations=3,
        comparability_fields=("equipment_profile", "implements_type"),
        comparability_fallback="strength:unspecified",
        insufficient_recommendations=(
            "Log additional heavy sets with reps and load.",
            "Add RIR or RPE to improve effort-adjusted estimation.",
        ),
        degraded_reason_code="multiple_comparability_groups",
        degraded_recommendations=("Keep equipment and setup stable for tighter comparability.",),
        degraded_caveat_code="multiple_comparability_groups",
    ),
    "sprint_max_speed": CapabilityEnvelopeSpec(
        capability="sprint_max_speed",
        required_observations=6,
        minimum_observations=3,
        comparability_fields=("surface", "timing_method"),
        comparability_fallback="sprint:unknown_protocol",
        insufficient_recommendations=(
            "Log sprint sets with distance_meters and duration_seconds.",
            "Persist timing_method and surface for comparability.",
        ),
        degraded_reason_code="protocol_mismatch",
        degraded_recommendations=("Keep timing method and surface consistent.",),
        degraded_caveat_code="protocol_mismatch",
    ),
    "jump_height": CapabilityEnvelopeSpec(
        capability="jump_height",
        required_observations=6,
        minimum_observations=3,
        comparability_fields=("device_type", "surface"),
        comparability_fallback="jump:unknown_protocol",
        insufficient_recommendations=(
            "Log jump height attempts with explicit jump_height_cm.",
            "Include device_type and surface for comparability.",
        ),
        degraded_reason_code="device_surface_mismatch",
        degraded_recommendations=("Keep device and surface stable across test sessions.",),
        degraded_caveat_code="device_surface_mismatch",
    ),
    "endurance_threshold": CapabilityEnvelopeSpec(
        capability="endurance_threshold",
        required_observations=6,
        minimum_observations=3,
        comparability_fields=("reference_type", "surface"),
        comparability_fallback="endurance:unknown_reference",
        insufficient_recommendations=(
            "Log duration+distance or power for endurance blocks.",
            "Include relative_intensity reference metadata when available.",
        ),
        degraded_reason_code="reference_protocol_mismatch",
        degraded_caveat_code="reference_protocol_mismatch",
    ),
}


def _spec_for(capability: str) -> CapabilityEnvelopeSpec:
    return CAPABILITY_SPECS[capability]


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _comparability_group(data: dict[str, Any], fields: tuple[str, ...], *, fallback: str) -> str:
    parts: list[str] = []
    for field in fields:
        value = str(data.get(field) or "").strip().lower()
        if value:
            parts.append(f"{field}:{value}")
    if not parts:
        return fallback
    return "|".join(parts)


def _build_insufficient_from_spec(
    capability: str,
    *,
    observed_observations: int,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = _spec_for(capability)
    return build_insufficient_envelope(
        capability=spec.capability,
        required_observations=spec.required_observations,
        observed_observations=observed_observations,
        model_version=MODEL_VERSION,
        recommended_next_observations=list(spec.insufficient_recommendations),
        diagnostics=diagnostics,
    )


def _build_envelope_from_spec(
    capability: str,
    *,
    estimate_mean: float,
    estimate_sd: float,
    observed_observations: int,
    groups: set[str],
    degraded: bool,
    include_default_degraded_metadata: bool = True,
    extra_reason_codes: list[str] | None = None,
    extra_recommendations: list[str] | None = None,
    extra_caveats: list[dict[str, Any]] | None = None,
    diagnostics: dict[str, Any] | None = None,
    freshness_days: float | None = None,
    freshness_half_life_days: float | None = None,
) -> dict[str, Any]:
    spec = _spec_for(capability)

    reason_codes: list[str] = []
    if degraded and include_default_degraded_metadata and spec.degraded_reason_code:
        reason_codes.append(spec.degraded_reason_code)
    if extra_reason_codes:
        reason_codes.extend(extra_reason_codes)

    recommended_next_observations: list[str] = []
    if degraded and include_default_degraded_metadata:
        recommended_next_observations.extend(spec.degraded_recommendations)
    if extra_recommendations:
        recommended_next_observations.extend(extra_recommendations)

    caveats: list[dict[str, Any]] = []
    if degraded and include_default_degraded_metadata and spec.degraded_caveat_code:
        caveats.append(
            {
                "code": spec.degraded_caveat_code,
                "severity": "medium",
                "details": {"group_count": len(groups)},
            }
        )
    if extra_caveats:
        caveats.extend(extra_caveats)

    status = STATUS_DEGRADED_COMPARABILITY if degraded else STATUS_OK
    sufficiency = data_sufficiency_block(
        required_observations=spec.required_observations,
        observed_observations=observed_observations,
        uncertainty_reason_codes=reason_codes,
        recommended_next_observations=recommended_next_observations,
    )
    return build_capability_envelope(
        capability=spec.capability,
        estimate_mean=estimate_mean,
        estimate_interval=interval_around(estimate_mean, estimate_sd),
        status=status,
        confidence=confidence_from_evidence(
            observed_points=observed_observations,
            required_points=spec.required_observations,
            comparability_degraded=degraded,
            freshness_days=freshness_days,
            freshness_half_life_days=freshness_half_life_days,
        ),
        data_sufficiency=sufficiency,
        model_version=MODEL_VERSION,
        caveats=caveats,
        comparability={"groups_total": len(groups), "groups": sorted(groups)},
        diagnostics=diagnostics,
    )


def _strength_envelope(rows: list[dict[str, Any]], *, timezone_name: str) -> dict[str, Any]:
    del timezone_name
    spec = _spec_for("strength_1rm")

    observations: list[float] = []
    groups: set[str] = set()
    sources: dict[str, int] = {"explicit": 0, "inferred_from_rpe": 0, "fallback_epley": 0}
    for row in rows:
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        modality = infer_row_modality_with_context(data).get("modality")
        if modality != "strength":
            continue

        weight = _to_float(data.get("weight_kg", data.get("weight")))
        reps = data.get("reps")
        if weight is None:
            continue

        e1rm, source = effort_adjusted_e1rm(
            weight,
            reps,
            rir=data.get("rir"),
            rpe=data.get("rpe"),
        )
        if e1rm <= 0:
            continue

        observations.append(e1rm)
        groups.add(
            _comparability_group(
                data,
                spec.comparability_fields,
                fallback=spec.comparability_fallback,
            )
        )
        if source not in sources:
            sources[source] = 0
        sources[source] += 1

    observed = len(observations)
    if observed < spec.minimum_observations:
        return _build_insufficient_from_spec(
            spec.capability,
            observed_observations=observed,
            diagnostics={"e1rm_source_counts": sources},
        )

    mean, sd = summarize_observations(observations)
    assert mean is not None
    degraded = len(groups) > 1
    extra_reason_codes = ["effort_context_missing"] if sources.get("fallback_epley", 0) else []
    return _build_envelope_from_spec(
        spec.capability,
        estimate_mean=mean,
        estimate_sd=sd,
        observed_observations=observed,
        groups=groups,
        degraded=degraded,
        extra_reason_codes=extra_reason_codes,
        diagnostics={"e1rm_source_counts": sources},
    )


def _extract_speed_mps(data: dict[str, Any]) -> float | None:
    distance = _to_float(data.get("distance_meters"))
    duration = _to_float(data.get("duration_seconds"))
    if distance is not None and duration is not None and distance > 0 and duration > 0:
        return distance / duration

    relative = data.get("relative_intensity")
    if isinstance(relative, dict):
        value_pct = _to_float(relative.get("value_pct"))
        reference_value = _to_float(relative.get("reference_value"))
        if (
            value_pct is not None
            and reference_value is not None
            and value_pct > 0
            and reference_value > 0
        ):
            return reference_value * (value_pct / 100.0)
    return None


def _sprint_envelope(rows: list[dict[str, Any]], *, timezone_name: str) -> dict[str, Any]:
    del timezone_name
    spec = _spec_for("sprint_max_speed")

    observations: list[float] = []
    groups: set[str] = set()
    for row in rows:
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        modality = infer_row_modality_with_context(data).get("modality")
        if modality != "sprint":
            continue

        speed = _extract_speed_mps(data)
        if speed is None or speed <= 0:
            continue

        observations.append(speed)
        groups.add(
            _comparability_group(
                data,
                spec.comparability_fields,
                fallback=spec.comparability_fallback,
            )
        )

    observed = len(observations)
    if observed < spec.minimum_observations:
        return _build_insufficient_from_spec(spec.capability, observed_observations=observed)

    # Top-end speed capability is better represented by fast-tail observations.
    tail = sorted(observations, reverse=True)[: max(3, observed // 3)]
    mean, sd = summarize_observations(tail)
    assert mean is not None
    degraded = len(groups) > 1
    return _build_envelope_from_spec(
        spec.capability,
        estimate_mean=mean,
        estimate_sd=sd,
        observed_observations=observed,
        groups=groups,
        degraded=degraded,
    )


def _extract_jump_height_cm(data: dict[str, Any]) -> float | None:
    for key in ("jump_height_cm", "cmj_height_cm", "vertical_jump_cm"):
        value = _to_float(data.get(key))
        if value is not None and value > 0:
            return value
    for key in ("jump_height_inches", "vertical_jump_inches"):
        value = _to_float(data.get(key))
        if value is not None and value > 0:
            return value * 2.54
    return None


def _jump_envelope(rows: list[dict[str, Any]], *, timezone_name: str) -> dict[str, Any]:
    spec = _spec_for("jump_height")

    attempts: list[tuple[str, float, str]] = []
    fallback_session_state: SessionBoundaryState | None = None
    for row in rows:
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        modality = infer_row_modality_with_context(data).get("modality")
        looks_like_jump = "jump" in str(data.get("exercise_id") or "").lower()
        if modality != "plyometric" and not looks_like_jump:
            continue

        jump_cm = _extract_jump_height_cm(data)
        if jump_cm is None or jump_cm <= 0:
            continue

        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue

        temporal = normalize_temporal_point(
            ts,
            timezone_name=timezone_name,
            data=data,
            metadata=metadata,
        )
        raw_session_id = str(metadata.get("session_id") or "").strip()
        if raw_session_id:
            session_key = raw_session_id
            fallback_session_state = None
        else:
            session_key, fallback_session_state = next_fallback_session_key(
                local_date=temporal.local_date,
                timestamp_utc=temporal.timestamp_utc,
                state=fallback_session_state,
            )

        group = _comparability_group(
            data,
            spec.comparability_fields,
            fallback=spec.comparability_fallback,
        )
        attempts.append((session_key, jump_cm, group))

    observed = len(attempts)
    if observed < spec.minimum_observations:
        return _build_insufficient_from_spec(spec.capability, observed_observations=observed)

    grouped_attempts: dict[str, list[float]] = defaultdict(list)
    groups: set[str] = set()
    for session_key, jump_cm, group in attempts:
        grouped_attempts[session_key].append(jump_cm)
        groups.add(group)

    session_best = [max(values) for values in grouped_attempts.values() if values]
    mean, sd = summarize_observations(session_best)
    assert mean is not None
    degraded = len(groups) > 1
    return _build_envelope_from_spec(
        spec.capability,
        estimate_mean=mean,
        estimate_sd=sd,
        observed_observations=observed,
        groups=groups,
        degraded=degraded,
        diagnostics={"session_trials": len(grouped_attempts)},
    )


def _endurance_observation(data: dict[str, Any]) -> tuple[float | None, float | None]:
    power = _to_float(data.get("power_watt"))
    if power is not None and power > 0:
        return power, None

    distance = _to_float(data.get("distance_meters"))
    duration = _to_float(data.get("duration_seconds"))
    if distance is not None and duration is not None and distance > 0 and duration > 0:
        return distance / duration, None

    relative = data.get("relative_intensity")
    if isinstance(relative, dict):
        pct = _to_float(relative.get("value_pct"))
        reference = _to_float(relative.get("reference_value"))
        if pct is not None and reference is not None and pct > 0 and reference > 0:
            freshness_days = None
            measured_at = relative.get("reference_measured_at")
            if isinstance(measured_at, str) and measured_at.strip():
                try:
                    measured_dt = datetime.fromisoformat(measured_at.replace("Z", "+00:00"))
                except ValueError:
                    measured_dt = None
                if measured_dt is not None and measured_dt.tzinfo is not None:
                    freshness_days = max(
                        0.0,
                        (datetime.now(measured_dt.tzinfo) - measured_dt).total_seconds() / 86400.0,
                    )
            return reference * (pct / 100.0), freshness_days
    return None, None


def _endurance_envelope(rows: list[dict[str, Any]], *, timezone_name: str) -> dict[str, Any]:
    del timezone_name
    spec = _spec_for("endurance_threshold")

    observations: list[float] = []
    freshness_days: list[float] = []
    groups: set[str] = set()
    stale_reference_count = 0

    for row in rows:
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        modality = infer_row_modality_with_context(data).get("modality")
        if modality != "endurance":
            continue

        value, freshness = _endurance_observation(data)
        if value is None or value <= 0:
            continue

        observations.append(value)
        if freshness is not None:
            freshness_days.append(freshness)
            if freshness > 30.0:
                stale_reference_count += 1
        groups.add(
            _comparability_group(
                data,
                spec.comparability_fields,
                fallback=spec.comparability_fallback,
            )
        )

    observed = len(observations)
    if observed < spec.minimum_observations:
        return _build_insufficient_from_spec(spec.capability, observed_observations=observed)

    mean, sd = summarize_observations(observations)
    assert mean is not None

    average_freshness = (sum(freshness_days) / len(freshness_days)) if freshness_days else None
    mismatched_groups = len(groups) > 1
    stale_reference = stale_reference_count > 0
    degraded = mismatched_groups or stale_reference

    extra_reason_codes: list[str] = []
    extra_recommendations: list[str] = []
    extra_caveats: list[dict[str, Any]] = []

    if stale_reference:
        extra_reason_codes.append("stale_reference")
        extra_recommendations.append(
            "Refresh threshold references (MSS/critical speed/power) periodically."
        )
        extra_caveats.append(
            {
                "code": "stale_reference",
                "severity": "medium",
                "details": {"stale_reference_count": stale_reference_count},
            }
        )

    return _build_envelope_from_spec(
        spec.capability,
        estimate_mean=mean,
        estimate_sd=sd,
        observed_observations=observed,
        groups=groups,
        degraded=degraded,
        include_default_degraded_metadata=mismatched_groups,
        extra_reason_codes=extra_reason_codes,
        extra_recommendations=extra_recommendations,
        extra_caveats=extra_caveats,
        diagnostics={"average_reference_freshness_days": average_freshness},
        freshness_days=average_freshness,
        freshness_half_life_days=21.0,
    )


CAPABILITY_ENVELOPE_BUILDERS = {
    "strength_1rm": _strength_envelope,
    "sprint_max_speed": _sprint_envelope,
    "jump_height": _jump_envelope,
    "endurance_threshold": _endurance_envelope,
}


def build_capability_envelopes(
    rows: list[dict[str, Any]],
    *,
    timezone_name: str,
) -> dict[str, dict[str, Any]]:
    return {
        capability: builder(rows, timezone_name=timezone_name)
        for capability, builder in CAPABILITY_ENVELOPE_BUILDERS.items()
    }


@projection_handler(
    *CAPABILITY_BACKFILL_TRIGGER_EVENT_TYPES,
    dimension_meta={
        "name": "capability_estimation",
        "description": (
            "Unified capability estimation envelope for strength, sprint, jump, and endurance."
        ),
        "key_structure": "one row per capability",
        "projection_key": "<capability_name>",
        "granularity": ["event_replay", "capability_state"],
        "output_schema": {
            "schema_version": "capability_output.v1",
            "status": "ok|insufficient_data|degraded_comparability",
            "estimate": {"mean": "number|null", "interval": "[number|null, number|null]"},
            "confidence": "number [0,1]",
            "data_sufficiency": "object",
            "caveats": "list",
            "model_version": "string",
        },
    },
)
async def update_capability_estimation(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    user_id = payload["user_id"]
    last_event_id = str(payload.get("event_id") or "")

    retracted_ids = await get_retracted_event_ids(conn, user_id)
    timezone_pref = await load_timezone_preference(conn, user_id, retracted_ids)
    timezone_context = resolve_timezone_context(timezone_pref)
    timezone_name = timezone_context["timezone"]

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id, list(CAPABILITY_BACKFILL_TRIGGER_EVENT_TYPES)),
        )
        rows = await cur.fetchall()

    rows = [row for row in rows if str(row.get("id") or "") not in retracted_ids]
    normalized_rows = normalize_training_signal_rows(rows, include_passthrough=True)

    envelopes = build_capability_envelopes(normalized_rows, timezone_name=timezone_name)

    for capability, envelope in envelopes.items():
        envelope["timezone_context"] = timezone_context
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
                VALUES (%s, 'capability_estimation', %s, %s, 1, %s, NOW())
                ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                    data = EXCLUDED.data,
                    version = projections.version + 1,
                    last_event_id = EXCLUDED.last_event_id,
                    updated_at = NOW()
                """,
                (user_id, capability, json.dumps(envelope), last_event_id),
            )
