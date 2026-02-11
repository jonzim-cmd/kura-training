"""Session feedback projection handler (PDC.8).

Standardizes session.completed feedback into a first-class projection so
coaching/planning can adapt to subjective session quality over time.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime
from statistics import mean
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler
from ..set_corrections import apply_set_correction_chain
from ..utils import get_retracted_event_ids, merge_observed_attributes, separate_known_unknown

logger = logging.getLogger(__name__)

_KNOWN_FIELDS: set[str] = {
    "enjoyment",
    "enjoyment_score",
    "session_enjoyment",
    "fun",
    "perceived_quality",
    "quality",
    "quality_score",
    "session_quality",
    "perceived_exertion",
    "session_rpe",
    "exertion",
    "rpe_summary",
    "pain_discomfort",
    "pain_level",
    "discomfort",
    "pain_signal",
    "context",
    "context_text",
    "summary",
    "comment",
    "notes",
    "feeling",
    "felt_good",
}

_POSITIVE_HINTS = (
    "good",
    "great",
    "fun",
    "spa",
    "strong",
    "solid",
    "leicht",
    "easy",
)
_NEGATIVE_HINTS = (
    "bad",
    "terrible",
    "schlecht",
    "pain",
    "hurt",
    "injury",
    "m\u00fcde",
    "tired",
)
_PAIN_HINTS = ("pain", "hurt", "schmerz", "ache", "injury")


def _as_optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _round2(value: float) -> float:
    return round(value, 2)


def _normalize_session_scope(metadata: dict[str, Any], timestamp: datetime) -> str:
    raw = str(metadata.get("session_id") or "").strip()
    if raw:
        return raw
    return timestamp.date().isoformat()


def _normalize_scale(
    value: Any,
    *,
    lower: float,
    upper: float,
    ten_scale_to_five: bool = False,
) -> float | None:
    parsed = _as_optional_float(value)
    if parsed is None:
        return None
    if ten_scale_to_five and parsed > 5:
        parsed = parsed / 2.0
    parsed = max(lower, min(upper, parsed))
    return _round2(parsed)


def _extract_context_text(data: dict[str, Any]) -> str | None:
    for key in ("context", "context_text", "summary", "comment", "notes", "feeling"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _infer_enjoyment_from_text(text: str | None) -> float | None:
    if not text:
        return None
    normalized = text.lower()
    has_positive = any(token in normalized for token in _POSITIVE_HINTS)
    has_negative = any(token in normalized for token in _NEGATIVE_HINTS)
    if has_positive and not has_negative:
        return 4.0
    if has_negative and not has_positive:
        return 2.0
    return None


def _infer_pain_from_text(text: str | None) -> float | None:
    if not text:
        return None
    normalized = text.lower()
    if any(token in normalized for token in _PAIN_HINTS):
        return 5.0
    return None


def _normalize_pain_signal(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) > 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return None
        if normalized in {"yes", "true", "pain", "present", "high", "medium"}:
            return True
        if normalized in {"no", "false", "none", "low", "absent"}:
            return False
    return None


def _normalize_session_feedback_payload(data: dict[str, Any]) -> dict[str, Any]:
    context = _extract_context_text(data)

    enjoyment = None
    for key in ("enjoyment", "enjoyment_score", "session_enjoyment", "fun"):
        enjoyment = _normalize_scale(
            data.get(key),
            lower=1,
            upper=5,
            ten_scale_to_five=True,
        )
        if enjoyment is not None:
            break
    if enjoyment is None and isinstance(data.get("felt_good"), bool):
        enjoyment = 4.0 if data.get("felt_good") else 2.0
    if enjoyment is None:
        enjoyment = _infer_enjoyment_from_text(context)

    perceived_quality = None
    for key in ("perceived_quality", "quality", "quality_score", "session_quality"):
        perceived_quality = _normalize_scale(
            data.get(key),
            lower=1,
            upper=5,
            ten_scale_to_five=True,
        )
        if perceived_quality is not None:
            break

    perceived_exertion = None
    for key in ("perceived_exertion", "session_rpe", "exertion", "rpe_summary"):
        perceived_exertion = _normalize_scale(data.get(key), lower=1, upper=10)
        if perceived_exertion is not None:
            break

    pain_discomfort = None
    for key in ("pain_discomfort", "pain_level", "discomfort"):
        pain_discomfort = _normalize_scale(data.get(key), lower=0, upper=10)
        if pain_discomfort is not None:
            break
    if pain_discomfort is None:
        pain_discomfort = _infer_pain_from_text(context)

    pain_signal = _normalize_pain_signal(data.get("pain_signal"))
    if pain_signal is None and pain_discomfort is not None:
        pain_signal = pain_discomfort > 0

    return {
        "enjoyment": enjoyment,
        "perceived_quality": perceived_quality,
        "perceived_exertion": perceived_exertion,
        "pain_discomfort": pain_discomfort,
        "pain_signal": pain_signal,
        "context": context,
    }


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return _round2(mean(values))


def _compute_enjoyment_trend(entries: list[dict[str, Any]]) -> str:
    values = [float(e["enjoyment"]) for e in entries if e.get("enjoyment") is not None]
    if len(values) < 4:
        return "insufficient_data"

    recent = values[-4:]
    baseline = values[-8:-4] if len(values) >= 8 else values[:-4]
    if not baseline:
        return "insufficient_data"

    delta = mean(recent) - mean(baseline)
    if delta >= 0.35:
        return "improving"
    if delta <= -0.35:
        return "declining"
    return "stable"


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    x_mean = mean(xs)
    y_mean = mean(ys)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=False))
    den_x = sum((x - x_mean) ** 2 for x in xs)
    den_y = sum((y - y_mean) ** 2 for y in ys)
    if den_x <= 0 or den_y <= 0:
        return None
    return num / ((den_x * den_y) ** 0.5)


def _compute_load_to_enjoyment_alignment(entries: list[dict[str, Any]]) -> dict[str, Any]:
    loads: list[float] = []
    enjoyments: list[float] = []

    for entry in entries:
        enjoyment = entry.get("enjoyment")
        load = (entry.get("session_load") or {}).get("total_volume_kg")
        if enjoyment is None or load is None:
            continue
        loads.append(float(load))
        enjoyments.append(float(enjoyment))

    corr = _pearson(loads, enjoyments)
    if corr is None:
        return {"correlation": None, "status": "insufficient_data"}
    corr = round(corr, 3)
    if corr >= 0.25:
        status = "positive"
    elif corr <= -0.25:
        status = "inverse"
    else:
        status = "neutral"
    return {"correlation": corr, "status": status}


def _aggregate_session_load(set_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_session: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total_sets": 0, "total_reps": 0, "total_volume_kg": 0.0}
    )

    for row in set_rows:
        data = row.get("effective_data") or row.get("data") or {}
        metadata = row.get("metadata") or {}
        ts: datetime = row["timestamp"]
        session_key = _normalize_session_scope(metadata, ts)

        try:
            reps = int(data.get("reps", 0))
        except (TypeError, ValueError):
            reps = 0
        try:
            weight = float(data.get("weight_kg", data.get("weight", 0.0)))
        except (TypeError, ValueError):
            weight = 0.0

        bucket = by_session[session_key]
        bucket["total_sets"] += 1
        bucket["total_reps"] += max(reps, 0)
        bucket["total_volume_kg"] += max(weight, 0.0) * max(reps, 0)

    for bucket in by_session.values():
        bucket["total_volume_kg"] = _round2(bucket["total_volume_kg"])

    return by_session


def _build_session_feedback_projection(
    feedback_rows: list[dict[str, Any]],
    set_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    session_load = _aggregate_session_load(set_rows)

    entries: list[dict[str, Any]] = []
    observed_attr_counts: dict[str, dict[str, int]] = {}

    for row in feedback_rows:
        data = row.get("data") or {}
        metadata = row.get("metadata") or {}
        ts: datetime = row["timestamp"]

        _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS)
        merge_observed_attributes(observed_attr_counts, "session.completed", unknown)

        normalized = _normalize_session_feedback_payload(data)
        session_key = _normalize_session_scope(metadata, ts)

        entry: dict[str, Any] = {
            "timestamp": ts.isoformat(),
            "date": ts.date().isoformat(),
        }
        session_id = metadata.get("session_id")
        if session_id is not None:
            entry["session_id"] = session_id

        for key in (
            "enjoyment",
            "perceived_quality",
            "perceived_exertion",
            "pain_discomfort",
            "pain_signal",
            "context",
        ):
            value = normalized.get(key)
            if value is not None:
                entry[key] = value

        load = session_load.get(session_key)
        if load is not None:
            entry["session_load"] = load

        entries.append(entry)

    entries = entries[-30:]

    enjoyment_values = [float(e["enjoyment"]) for e in entries if e.get("enjoyment") is not None]
    quality_values = [
        float(e["perceived_quality"])
        for e in entries
        if e.get("perceived_quality") is not None
    ]
    exertion_values = [
        float(e["perceived_exertion"])
        for e in entries
        if e.get("perceived_exertion") is not None
    ]
    pain_values = [
        float(e["pain_discomfort"])
        for e in entries
        if e.get("pain_discomfort") is not None
    ]

    return {
        "recent_sessions": entries,
        "trends": {
            "enjoyment_avg_last_4": _safe_mean(enjoyment_values[-4:]),
            "quality_avg_last_4": _safe_mean(quality_values[-4:]),
            "exertion_avg_last_4": _safe_mean(exertion_values[-4:]),
            "pain_discomfort_avg_last_4": _safe_mean(pain_values[-4:]),
            "enjoyment_trend": _compute_enjoyment_trend(entries),
            "load_to_enjoyment_alignment": _compute_load_to_enjoyment_alignment(entries),
        },
        "counts": {
            "sessions_with_feedback": len(entries),
            "sessions_with_load_and_feedback": sum(
                1
                for e in entries
                if e.get("session_load") is not None and e.get("enjoyment") is not None
            ),
        },
        "data_quality": {
            "observed_attributes": observed_attr_counts,
        },
    }


@projection_handler(
    "session.completed",
    "set.logged",
    "set.corrected",
    dimension_meta={
        "name": "session_feedback",
        "description": "Subjective post-session feedback and trend aggregation",
        "key_structure": "single overview per user",
        "projection_key": "overview",
        "granularity": ["session", "trend"],
        "relates_to": {
            "training_plan": {
                "join": "session trends",
                "why": "adapt training load based on enjoyment/exertion/pain trajectory",
            },
            "training_timeline": {
                "join": "session_id/date",
                "why": "load-to-feedback alignment",
            },
        },
        "context_seeds": [
            "session_enjoyment",
            "session_quality",
            "session_exertion",
            "pain_signal",
        ],
        "output_schema": {
            "recent_sessions": [
                {
                    "timestamp": "ISO 8601 datetime",
                    "date": "ISO 8601 date",
                    "session_id": "string (optional)",
                    "enjoyment": "number (optional, 1..5)",
                    "perceived_quality": "number (optional, 1..5)",
                    "perceived_exertion": "number (optional, 1..10)",
                    "pain_discomfort": "number (optional, 0..10)",
                    "pain_signal": "boolean (optional)",
                    "context": "string (optional)",
                    "session_load": {
                        "total_sets": "integer",
                        "total_reps": "integer",
                        "total_volume_kg": "number",
                    },
                }
            ],
            "trends": {
                "enjoyment_avg_last_4": "number (optional)",
                "quality_avg_last_4": "number (optional)",
                "exertion_avg_last_4": "number (optional)",
                "pain_discomfort_avg_last_4": "number (optional)",
                "enjoyment_trend": "string (improving|stable|declining|insufficient_data)",
                "load_to_enjoyment_alignment": {
                    "correlation": "number (optional)",
                    "status": "string (positive|neutral|inverse|insufficient_data)",
                },
            },
            "counts": {
                "sessions_with_feedback": "integer",
                "sessions_with_load_and_feedback": "integer",
            },
            "data_quality": {
                "observed_attributes": {
                    "session.completed": {
                        "<field>": "integer"
                    }
                }
            },
        },
    },
)
async def update_session_feedback(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of standardized session feedback projection."""
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = 'session.completed'
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id,),
        )
        feedback_rows = await cur.fetchall()

    feedback_rows = [
        row for row in feedback_rows if str(row["id"]) not in retracted_ids
    ]

    if not feedback_rows:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM projections
                WHERE user_id = %s
                  AND projection_type = 'session_feedback'
                  AND key = 'overview'
                """,
                (user_id,),
            )
        return

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = 'set.logged'
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id,),
        )
        set_rows = await cur.fetchall()

    set_rows = [row for row in set_rows if str(row["id"]) not in retracted_ids]
    set_ids = [str(row["id"]) for row in set_rows]
    correction_rows: list[dict[str, Any]] = []
    if set_ids:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, timestamp, data
                FROM events
                WHERE user_id = %s
                  AND event_type = 'set.corrected'
                  AND data->>'target_event_id' = ANY(%s)
                ORDER BY timestamp ASC, id ASC
                """,
                (user_id, set_ids),
            )
            correction_rows = await cur.fetchall()
    correction_rows = [
        row for row in correction_rows if str(row["id"]) not in retracted_ids
    ]
    set_rows = apply_set_correction_chain(set_rows, correction_rows)

    projection_data = _build_session_feedback_projection(feedback_rows, set_rows)
    last_event_id = str(payload.get("event_id") or feedback_rows[-1]["id"])

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'session_feedback', 'overview', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), last_event_id),
        )

    logger.info(
        "Updated session_feedback for user=%s (sessions=%d)",
        user_id,
        projection_data.get("counts", {}).get("sessions_with_feedback", 0),
    )
