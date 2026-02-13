"""Open observations projection handler (PDC.21).

Provides an extensible observation contract for session facts that do not fit
fixed event schemas. Reacts to observation.logged and keeps one projection per
dimension key under projection_type=open_observations.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..utils import get_retracted_event_ids, separate_known_unknown

logger = logging.getLogger(__name__)

_PROJECTION_TYPE = "open_observations"
_REGISTRY_VERSION = "open_observation.v1"
_KNOWN_FIELDS = {
    "dimension",
    "value",
    "unit",
    "scale",
    "context_text",
    "tags",
    "confidence",
    "provenance",
    "scope",
}
_KNOWN_DIMENSIONS: dict[str, dict[str, Any]] = {
    "motivation_pre": {
        "value_type": "number",
        "scale_min": 1.0,
        "scale_max": 5.0,
        "default_unit": None,
    },
    "discomfort_signal": {
        "value_type": "number_or_bool",
        "scale_min": 0.0,
        "scale_max": 10.0,
        "default_unit": None,
    },
    "jump_baseline": {
        "value_type": "number",
        "scale_min": None,
        "scale_max": None,
        "default_unit": "cm",
    },
}
_PROVISIONAL_PREFIXES = ("x_", "custom.", "provisional.")
_PROMOTION_MIN_SUPPORT = 5
_PROMOTION_MIN_AVG_CONFIDENCE = 0.8


def _round2(value: float) -> float:
    return round(value, 2)


def _as_optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_dimension(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace(" ", "_")
    if not normalized:
        return None
    return normalized


def _dimension_tier(dimension: str) -> str:
    if dimension in _KNOWN_DIMENSIONS:
        return "known"
    if any(dimension.startswith(prefix) for prefix in _PROVISIONAL_PREFIXES):
        return "provisional"
    return "unknown"


def _normalize_confidence(value: Any) -> tuple[float, list[str]]:
    flags: list[str] = []
    parsed = _as_optional_float(value)
    if parsed is None:
        flags.append("invalid_confidence_defaulted")
        return 0.5, flags
    if parsed < 0.0:
        flags.append("confidence_clamped_low")
        parsed = 0.0
    if parsed > 1.0:
        flags.append("confidence_clamped_high")
        parsed = 1.0
    return _round2(parsed), flags


def _normalize_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        tag = item.strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def _normalize_scope(value: Any) -> tuple[dict[str, Any], list[str]]:
    flags: list[str] = []
    scope = value if isinstance(value, dict) else {}
    if not scope:
        flags.append("scope_defaulted_to_session")

    level = str(scope.get("level") or "").strip().lower()
    if level not in {"session", "exercise", "set"}:
        level = "session"
        flags.append("invalid_scope_level_defaulted")

    normalized: dict[str, Any] = {"level": level}
    session_id = scope.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        normalized["session_id"] = session_id.strip()
    exercise_id = scope.get("exercise_id")
    if isinstance(exercise_id, str) and exercise_id.strip():
        normalized["exercise_id"] = exercise_id.strip().lower()

    return normalized, flags


def _normalize_known_value(
    dimension: str,
    value: Any,
    unit: Any,
) -> tuple[Any, str | None, list[str]]:
    spec = _KNOWN_DIMENSIONS.get(dimension) or {}
    flags: list[str] = []

    if dimension == "discomfort_signal":
        if isinstance(value, bool):
            return value, None, flags
        parsed = _as_optional_float(value)
        if parsed is None:
            flags.append("invalid_value_type")
            return value, None, flags
        min_v = float(spec.get("scale_min", 0.0))
        max_v = float(spec.get("scale_max", 10.0))
        if parsed < min_v or parsed > max_v:
            flags.append("scale_out_of_bounds")
        return _round2(parsed), None, flags

    parsed = _as_optional_float(value)
    if parsed is None:
        flags.append("invalid_value_type")
        return value, (str(unit).strip() if isinstance(unit, str) and unit.strip() else None), flags

    if dimension == "jump_baseline":
        normalized_unit = (
            str(unit).strip().lower() if isinstance(unit, str) and unit.strip() else None
        )
        if normalized_unit in {None, ""}:
            normalized_unit = "cm"
            flags.append("unit_defaulted_to_cm")
        elif normalized_unit in {"m", "meter", "meters"}:
            parsed = parsed * 100.0
            normalized_unit = "cm"
            flags.append("unit_converted_to_cm")
        elif normalized_unit not in {"cm", "centimeter", "centimeters"}:
            flags.append("unknown_unit_preserved")
        return _round2(parsed), normalized_unit, flags

    # motivation_pre
    min_v = spec.get("scale_min")
    max_v = spec.get("scale_max")
    if min_v is not None and parsed < float(min_v):
        flags.append("scale_out_of_bounds")
    if max_v is not None and parsed > float(max_v):
        flags.append("scale_out_of_bounds")
    normalized_unit = (
        str(unit).strip() if isinstance(unit, str) and unit and str(unit).strip() else None
    )
    return _round2(parsed), normalized_unit, flags


def _normalize_observation_entry(row: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    data = row.get("data") or {}
    dimension = _normalize_dimension(data.get("dimension"))
    if dimension is None:
        return None

    tier = _dimension_tier(dimension)
    quality_flags: list[str] = []
    if tier == "provisional":
        quality_flags.append("provisional_dimension")
    elif tier == "unknown":
        quality_flags.append("unknown_dimension")

    confidence, confidence_flags = _normalize_confidence(data.get("confidence", 0.5))
    quality_flags.extend(confidence_flags)

    context_text = data.get("context_text")
    if not isinstance(context_text, str):
        context_text = ""
    context_text = context_text.strip()

    tags = _normalize_tags(data.get("tags"))
    scope, scope_flags = _normalize_scope(data.get("scope"))
    quality_flags.extend(scope_flags)

    provenance = data.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
        quality_flags.append("missing_provenance")
    if not provenance.get("source_type"):
        quality_flags.append("missing_provenance_source_type")

    if tier == "known":
        normalized_value, normalized_unit, value_flags = _normalize_known_value(
            dimension,
            data.get("value"),
            data.get("unit"),
        )
        quality_flags.extend(value_flags)
    else:
        normalized_value = data.get("value")
        normalized_unit = (
            str(data.get("unit")).strip()
            if isinstance(data.get("unit"), str) and str(data.get("unit")).strip()
            else None
        )

    _, unknown = separate_known_unknown(data, _KNOWN_FIELDS)
    if unknown:
        quality_flags.append("unknown_payload_fields")

    entry = {
        "event_id": str(row.get("id")),
        "timestamp": row["timestamp"].isoformat(),
        "dimension": dimension,
        "tier": tier,
        "value": normalized_value,
        "unit": normalized_unit,
        "scale": data.get("scale"),
        "context_text": context_text,
        "tags": tags,
        "confidence": confidence,
        "provenance": provenance,
        "scope": scope,
        "quality_flags": sorted(set(quality_flags)),
    }
    return dimension, entry


def _build_lifecycle_summary(tier: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    total_entries = len(entries)
    if total_entries == 0:
        return {
            "status": "insufficient_support",
            "eligible_for_human_review": False,
            "avg_confidence": 0.0,
            "support_count": 0,
            "thresholds": {
                "promotion_min_support": _PROMOTION_MIN_SUPPORT,
                "promotion_min_avg_confidence": _PROMOTION_MIN_AVG_CONFIDENCE,
            },
        }

    avg_confidence = _round2(
        sum(float(entry.get("confidence", 0.0)) for entry in entries) / total_entries
    )
    quality_flagged_entries = sum(
        1 for entry in entries if entry.get("quality_flags")
    )
    quality_flag_rate = _round2(quality_flagged_entries / total_entries)

    if tier == "known":
        status = "already_known"
        eligible = False
    elif total_entries < _PROMOTION_MIN_SUPPORT:
        status = "insufficient_support"
        eligible = False
    elif avg_confidence < _PROMOTION_MIN_AVG_CONFIDENCE:
        status = "confidence_below_threshold"
        eligible = False
    else:
        status = "eligible_for_human_review"
        eligible = True

    return {
        "status": status,
        "eligible_for_human_review": eligible,
        "avg_confidence": avg_confidence,
        "support_count": total_entries,
        "quality_flag_rate": quality_flag_rate,
        "thresholds": {
            "promotion_min_support": _PROMOTION_MIN_SUPPORT,
            "promotion_min_avg_confidence": _PROMOTION_MIN_AVG_CONFIDENCE,
        },
    }


def _build_dimension_projection(dimension: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    latest = entries[-1]
    quality_counts: dict[str, int] = {}
    for entry in entries:
        for flag in entry.get("quality_flags", []):
            quality_counts[flag] = quality_counts.get(flag, 0) + 1
    lifecycle = _build_lifecycle_summary(str(latest.get("tier", "unknown")), entries)

    return {
        "dimension": dimension,
        "tier": latest.get("tier", "unknown"),
        "registry_version": _REGISTRY_VERSION,
        "entries": entries[-200:],
        "summary": {
            "total_entries": len(entries),
            "latest_value": latest.get("value"),
            "latest_unit": latest.get("unit"),
            "latest_confidence": latest.get("confidence"),
            "latest_context_text": latest.get("context_text"),
            "latest_timestamp": latest.get("timestamp"),
            "quality_flags_count": quality_counts,
            "lifecycle": lifecycle,
        },
    }


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not projection_rows:
        return {"dimensions_total": 0}
    tiers: dict[str, int] = {}
    for row in projection_rows:
        tier = str((row.get("data") or {}).get("tier") or "unknown")
        tiers[tier] = tiers.get(tier, 0) + 1
    return {
        "dimensions_total": len(projection_rows),
        "tiers": tiers,
    }


@projection_handler(
    "observation.logged",
    dimension_meta={
        "name": "open_observations",
        "description": "Extensible open-world session observations with tiered validation",
        "key_structure": "one projection per dimension",
        "projection_key": "<dimension>",
        "granularity": ["session", "exercise", "set"],
        "relates_to": {
            "session_feedback": {
                "join": "session_id",
                "why": "augment subjective feedback with additional open observations",
            },
            "quality_health": {
                "join": "overview",
                "why": "surface unknown/provisional observation quality flags",
            },
        },
        "context_seeds": [
            "motivation_pre",
            "discomfort_signal",
            "jump_baseline",
        ],
        "output_schema": {
            "dimension": "string",
            "tier": "string — known|provisional|unknown",
            "registry_version": "string",
            "entries": [{
                "event_id": "string",
                "timestamp": "ISO 8601 datetime",
                "value": "any",
                "unit": "string (optional)",
                "scale": "object|string|null",
                "context_text": "string",
                "tags": ["string"],
                "confidence": "number (0..1)",
                "provenance": "object",
                "scope": {
                    "level": "string — session|exercise|set",
                    "session_id": "string (optional)",
                    "exercise_id": "string (optional)",
                },
                "quality_flags": ["string"],
            }],
            "summary": {
                "total_entries": "integer",
                "latest_value": "any",
                "latest_unit": "string|null",
                "latest_confidence": "number",
                "latest_context_text": "string",
                "latest_timestamp": "ISO 8601 datetime",
                "quality_flags_count": {"<flag>": "integer"},
                "lifecycle": {
                    "status": "string — already_known|insufficient_support|confidence_below_threshold|eligible_for_human_review",
                    "eligible_for_human_review": "boolean",
                    "avg_confidence": "number",
                    "support_count": "integer",
                    "quality_flag_rate": "number",
                    "thresholds": {
                        "promotion_min_support": "integer",
                        "promotion_min_avg_confidence": "number",
                    },
                },
            },
        },
        "manifest_contribution": _manifest_contribution,
    },
)
async def update_open_observations(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of open observation projections by dimension."""
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'observation.logged'
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    rows = [row for row in rows if str(row["id"]) not in retracted_ids]

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT key
            FROM projections
            WHERE user_id = %s
              AND projection_type = %s
            """,
            (user_id, _PROJECTION_TYPE),
        )
        existing_rows = await cur.fetchall()
    existing_keys = {str(row["key"]) for row in existing_rows}

    if not rows:
        if existing_keys:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM projections
                    WHERE user_id = %s
                      AND projection_type = %s
                    """,
                    (user_id, _PROJECTION_TYPE),
                )
        return

    entries_by_dimension: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        normalized = _normalize_observation_entry(row)
        if normalized is None:
            continue
        dimension, entry = normalized
        entries_by_dimension.setdefault(dimension, []).append(entry)

    active_keys = set(entries_by_dimension)
    stale_keys = sorted(existing_keys - active_keys)
    if stale_keys:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM projections
                WHERE user_id = %s
                  AND projection_type = %s
                  AND key = ANY(%s)
                """,
                (user_id, _PROJECTION_TYPE, stale_keys),
            )

    last_event_id = str(payload.get("event_id") or rows[-1]["id"])
    for dimension, entries in entries_by_dimension.items():
        projection_data = _build_dimension_projection(dimension, entries)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
                VALUES (%s, %s, %s, %s, 1, %s, NOW())
                ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                    data = EXCLUDED.data,
                    version = projections.version + 1,
                    last_event_id = EXCLUDED.last_event_id,
                    updated_at = NOW()
                """,
                (
                    user_id,
                    _PROJECTION_TYPE,
                    dimension,
                    json.dumps(projection_data),
                    last_event_id,
                ),
            )

    logger.info(
        "Updated open_observations for user=%s (dimensions=%d)",
        user_id,
        len(entries_by_dimension),
    )
