"""Population prior engine for privacy-safe cross-user Bayesian priors.

The engine computes anonymized cohort aggregates from opted-in users and
stores them in `population_prior_profiles`. Individual inference handlers
can then optionally blend local priors with cohort-level priors.

Strict separation:
- No user identifiers are stored in population prior artifacts.
- Only aggregated cohort statistics are persisted.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

logger = logging.getLogger(__name__)

POPULATION_OPT_IN_KEY = "population_priors_opt_in"
STRENGTH_FALLBACK_TARGET_KEY = "__all__"
READINESS_TARGET_KEY = "overview"


def population_priors_enabled() -> bool:
    raw = os.environ.get("KURA_POPULATION_PRIORS_ENABLED", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def population_prior_min_cohort_size() -> int:
    return max(2, int(os.environ.get("KURA_POPULATION_PRIOR_MIN_COHORT_SIZE", "25")))


def population_prior_window_days() -> int:
    return max(7, int(os.environ.get("KURA_POPULATION_PRIOR_WINDOW_DAYS", "180")))


def population_prior_blend_weight() -> float:
    raw = float(os.environ.get("KURA_POPULATION_PRIOR_BLEND_WEIGHT", "0.35"))
    return min(0.95, max(0.0, raw))


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    return normalized or "unknown"


def _bool_from_any(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _weighted_stats(values: list[float], weights: list[float]) -> tuple[float, float]:
    if not values:
        raise ValueError("Cannot compute stats on empty value list")
    if len(values) != len(weights):
        raise ValueError("Values and weights must have same length")

    safe_weights = [max(0.0, w) for w in weights]
    total_weight = sum(safe_weights)
    if total_weight <= 0.0:
        total_weight = float(len(values))
        safe_weights = [1.0] * len(values)

    mean = sum(v * w for v, w in zip(values, safe_weights)) / total_weight
    var = sum(w * ((v - mean) ** 2) for v, w in zip(values, safe_weights)) / total_weight
    return mean, max(1e-6, var)


def _cohort_key_from_user_profile(data: dict[str, Any] | None) -> str:
    profile = {}
    if isinstance(data, dict):
        user = data.get("user")
        if isinstance(user, dict):
            maybe_profile = user.get("profile")
            if isinstance(maybe_profile, dict):
                profile = maybe_profile

    training_modality = _normalize(profile.get("training_modality"))
    experience_level = _normalize(profile.get("experience_level"))
    return f"tm:{training_modality}|el:{experience_level}"


async def _record_refresh_run(
    conn: psycopg.AsyncConnection[Any],
    *,
    status: str,
    users_opted_in: int,
    cohorts_considered: int,
    priors_written: int,
    details: dict[str, Any],
    started_at: datetime,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO population_prior_refresh_runs (
                status, users_opted_in, cohorts_considered, priors_written,
                details, started_at, completed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                status,
                users_opted_in,
                cohorts_considered,
                priors_written,
                Json(details),
                started_at,
            ),
        )


async def _table_exists(conn: psycopg.AsyncConnection[Any], table_name: str) -> bool:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT to_regclass(%s) IS NOT NULL AS present",
            (table_name,),
        )
        row = await cur.fetchone()
    return bool(row and row.get("present"))


async def _safe_record_refresh_run(
    conn: psycopg.AsyncConnection[Any],
    **kwargs: Any,
) -> None:
    if not await _table_exists(conn, "population_prior_refresh_runs"):
        return
    try:
        await _record_refresh_run(conn, **kwargs)
    except Exception as exc:
        logger.warning("Population prior refresh run telemetry skipped: %s", exc)


async def _global_retracted_event_ids(conn: psycopg.AsyncConnection[Any]) -> set[str]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data->>'retracted_event_id' AS retracted_id
            FROM events
            WHERE event_type = 'event.retracted'
            """,
        )
        rows = await cur.fetchall()
    return {str(r["retracted_id"]) for r in rows if r.get("retracted_id")}


async def _load_opted_in_users(
    conn: psycopg.AsyncConnection[Any],
    *,
    retracted_event_ids: set[str],
) -> set[str]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, user_id::text AS user_id, timestamp, data
            FROM events
            WHERE event_type = 'preference.set'
              AND lower(trim(data->>'key')) = %s
            ORDER BY timestamp ASC, id ASC
            """,
            (POPULATION_OPT_IN_KEY,),
        )
        rows = await cur.fetchall()

    latest_value_by_user: dict[str, bool] = {}
    for row in rows:
        event_id = str(row["id"])
        if event_id in retracted_event_ids:
            continue
        data = row.get("data") or {}
        parsed = _bool_from_any(data.get("value"))
        if parsed is None:
            continue
        latest_value_by_user[str(row["user_id"])] = parsed

    return {user_id for user_id, opted_in in latest_value_by_user.items() if opted_in}


async def _load_user_cohorts(
    conn: psycopg.AsyncConnection[Any],
    user_ids: list[str],
) -> dict[str, str]:
    if not user_ids:
        return {}
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT user_id::text AS user_id, data
            FROM projections
            WHERE projection_type = 'user_profile'
              AND key = 'me'
              AND user_id::text = ANY(%s)
            """,
            (user_ids,),
        )
        rows = await cur.fetchall()

    cohort_by_user = {user_id: "tm:unknown|el:unknown" for user_id in user_ids}
    for row in rows:
        user_id = str(row["user_id"])
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        cohort_by_user[user_id] = _cohort_key_from_user_profile(data)
    return cohort_by_user


def _add_aggregate_sample(
    bucket_by_group: dict[tuple[str, str], dict[str, Any]],
    *,
    cohort_key: str,
    target_key: str,
    user_id: str,
    value: float,
    weight: float,
) -> None:
    bucket = bucket_by_group.setdefault(
        (cohort_key, target_key),
        {
            "users": set(),
            "values": [],
            "weights": [],
        },
    )
    bucket["users"].add(user_id)
    bucket["values"].append(value)
    bucket["weights"].append(max(0.0, weight))


async def _load_strength_projection_rows(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_ids: list[str],
    window_days: int,
) -> list[dict[str, Any]]:
    if not user_ids:
        return []
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT user_id::text AS user_id, key, data
            FROM projections
            WHERE projection_type = 'strength_inference'
              AND user_id::text = ANY(%s)
              AND updated_at >= NOW() - make_interval(days => %s)
            """,
            (user_ids, window_days),
        )
        return await cur.fetchall()


async def _load_readiness_projection_rows(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_ids: list[str],
    window_days: int,
) -> list[dict[str, Any]]:
    if not user_ids:
        return []
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT user_id::text AS user_id, key, data
            FROM projections
            WHERE projection_type = 'readiness_inference'
              AND key = %s
              AND user_id::text = ANY(%s)
              AND updated_at >= NOW() - make_interval(days => %s)
            """,
            (READINESS_TARGET_KEY, user_ids, window_days),
        )
        return await cur.fetchall()


def _build_strength_prior_rows(
    rows: list[dict[str, Any]],
    cohort_by_user: dict[str, str],
    *,
    min_cohort_size: int,
    window_days: int,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        user_id = str(row.get("user_id") or "")
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        if bool((data.get("data_quality") or {}).get("insufficient_data")):
            continue

        slope = _as_float((data.get("trend") or {}).get("slope_kg_per_day"))
        if slope is None:
            continue

        confidence = _as_float(
            ((data.get("dynamics") or {}).get("estimated_1rm") or {}).get("confidence")
        )
        if confidence is None:
            confidence = 0.5

        cohort_key = cohort_by_user.get(user_id, "tm:unknown|el:unknown")
        target_key = _normalize(row.get("key"))

        _add_aggregate_sample(
            groups,
            cohort_key=cohort_key,
            target_key=target_key,
            user_id=user_id,
            value=slope,
            weight=confidence,
        )
        _add_aggregate_sample(
            groups,
            cohort_key=cohort_key,
            target_key=STRENGTH_FALLBACK_TARGET_KEY,
            user_id=user_id,
            value=slope,
            weight=confidence,
        )

    out: list[dict[str, Any]] = []
    for (cohort_key, target_key), bucket in groups.items():
        participants_count = len(bucket["users"])
        sample_size = len(bucket["values"])
        if participants_count < min_cohort_size or sample_size < min_cohort_size:
            continue

        mean, var = _weighted_stats(bucket["values"], bucket["weights"])
        prior_payload = {
            "parameter": "slope_kg_per_day",
            "mean": round(mean, 8),
            "var": round(var, 8),
            "std": round(math.sqrt(var), 8),
            "min": round(min(bucket["values"]), 8),
            "max": round(max(bucket["values"]), 8),
            "privacy_gate_passed": True,
        }

        out.append(
            {
                "projection_type": "strength_inference",
                "target_key": target_key,
                "cohort_key": cohort_key,
                "prior_payload": prior_payload,
                "participants_count": participants_count,
                "sample_size": sample_size,
                "min_cohort_size": min_cohort_size,
                "source_window_days": window_days,
            }
        )

    return out


def _build_readiness_prior_rows(
    rows: list[dict[str, Any]],
    cohort_by_user: dict[str, str],
    *,
    min_cohort_size: int,
    window_days: int,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        user_id = str(row.get("user_id") or "")
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        if bool((data.get("data_quality") or {}).get("insufficient_data")):
            continue

        mean_value = _as_float(((data.get("baseline") or {}).get("posterior_mean")))
        if mean_value is None:
            continue

        confidence = _as_float(((data.get("dynamics") or {}).get("readiness") or {}).get("confidence"))
        if confidence is None:
            confidence = 0.5

        cohort_key = cohort_by_user.get(user_id, "tm:unknown|el:unknown")
        _add_aggregate_sample(
            groups,
            cohort_key=cohort_key,
            target_key=READINESS_TARGET_KEY,
            user_id=user_id,
            value=mean_value,
            weight=confidence,
        )

    out: list[dict[str, Any]] = []
    for (cohort_key, target_key), bucket in groups.items():
        participants_count = len(bucket["users"])
        sample_size = len(bucket["values"])
        if participants_count < min_cohort_size or sample_size < min_cohort_size:
            continue

        mean, var = _weighted_stats(bucket["values"], bucket["weights"])
        prior_payload = {
            "parameter": "readiness_baseline",
            "mean": round(mean, 8),
            "var": round(var, 8),
            "std": round(math.sqrt(var), 8),
            "min": round(min(bucket["values"]), 8),
            "max": round(max(bucket["values"]), 8),
            "privacy_gate_passed": True,
        }
        out.append(
            {
                "projection_type": "readiness_inference",
                "target_key": target_key,
                "cohort_key": cohort_key,
                "prior_payload": prior_payload,
                "participants_count": participants_count,
                "sample_size": sample_size,
                "min_cohort_size": min_cohort_size,
                "source_window_days": window_days,
            }
        )

    return out


async def refresh_population_prior_profiles(
    conn: psycopg.AsyncConnection[Any],
) -> dict[str, Any]:
    """Refresh aggregated population priors from opted-in users."""
    started_at = datetime.now(timezone.utc)
    min_cohort_size = population_prior_min_cohort_size()
    window_days = population_prior_window_days()

    if not await _table_exists(conn, "population_prior_profiles"):
        await _safe_record_refresh_run(
            conn,
            status="skipped",
            users_opted_in=0,
            cohorts_considered=0,
            priors_written=0,
            details={"skip_reason": "population_prior_tables_missing"},
            started_at=started_at,
        )
        return {
            "status": "skipped",
            "skip_reason": "population_prior_tables_missing",
            "users_opted_in": 0,
            "priors_written": 0,
        }

    if not population_priors_enabled():
        await _safe_record_refresh_run(
            conn,
            status="skipped",
            users_opted_in=0,
            cohorts_considered=0,
            priors_written=0,
            details={"skip_reason": "population_priors_disabled"},
            started_at=started_at,
        )
        return {
            "status": "skipped",
            "skip_reason": "population_priors_disabled",
            "users_opted_in": 0,
            "priors_written": 0,
        }

    try:
        retracted_ids = await _global_retracted_event_ids(conn)
        opted_in_users = await _load_opted_in_users(conn, retracted_event_ids=retracted_ids)
        user_ids = sorted(opted_in_users)
        cohort_by_user = await _load_user_cohorts(conn, user_ids)

        strength_rows = await _load_strength_projection_rows(
            conn,
            user_ids=user_ids,
            window_days=window_days,
        )
        readiness_rows = await _load_readiness_projection_rows(
            conn,
            user_ids=user_ids,
            window_days=window_days,
        )

        prior_rows: list[dict[str, Any]] = []
        prior_rows.extend(
            _build_strength_prior_rows(
                strength_rows,
                cohort_by_user,
                min_cohort_size=min_cohort_size,
                window_days=window_days,
            )
        )
        prior_rows.extend(
            _build_readiness_prior_rows(
                readiness_rows,
                cohort_by_user,
                min_cohort_size=min_cohort_size,
                window_days=window_days,
            )
        )

        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM population_prior_profiles")
            for row in prior_rows:
                await cur.execute(
                    """
                    INSERT INTO population_prior_profiles (
                        projection_type, target_key, cohort_key, prior_payload,
                        participants_count, sample_size, min_cohort_size, source_window_days,
                        computed_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """,
                    (
                        row["projection_type"],
                        row["target_key"],
                        row["cohort_key"],
                        Json(row["prior_payload"]),
                        row["participants_count"],
                        row["sample_size"],
                        row["min_cohort_size"],
                        row["source_window_days"],
                    ),
                )

        cohorts_considered = len({row["cohort_key"] for row in prior_rows})
        await _safe_record_refresh_run(
            conn,
            status="success",
            users_opted_in=len(opted_in_users),
            cohorts_considered=cohorts_considered,
            priors_written=len(prior_rows),
            details={
                "strength_candidates": len(strength_rows),
                "readiness_candidates": len(readiness_rows),
                "min_cohort_size": min_cohort_size,
                "window_days": window_days,
            },
            started_at=started_at,
        )

        summary = {
            "status": "success",
            "users_opted_in": len(opted_in_users),
            "cohorts_considered": cohorts_considered,
            "priors_written": len(prior_rows),
            "min_cohort_size": min_cohort_size,
            "window_days": window_days,
        }
        logger.info(
            "Refreshed population priors: users_opted_in=%d priors_written=%d",
            summary["users_opted_in"],
            summary["priors_written"],
        )
        return summary
    except Exception as exc:
        await _safe_record_refresh_run(
            conn,
            status="failed",
            users_opted_in=0,
            cohorts_considered=0,
            priors_written=0,
            details={"error": str(exc)},
            started_at=started_at,
        )
        raise


async def is_population_prior_opted_in(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    *,
    retracted_ids: set[str] | None = None,
) -> bool:
    """Return latest per-user opt-in preference for population prior usage."""
    if retracted_ids is None:
        from .utils import get_retracted_event_ids  # local import avoids cycles

        retracted_ids = await get_retracted_event_ids(conn, user_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'preference.set'
              AND lower(trim(data->>'key')) = %s
            ORDER BY timestamp DESC, id DESC
            """,
            (user_id, POPULATION_OPT_IN_KEY),
        )
        rows = await cur.fetchall()

    for row in rows:
        if str(row["id"]) in retracted_ids:
            continue
        data = row.get("data") or {}
        parsed = _bool_from_any(data.get("value"))
        if parsed is not None:
            return parsed
    return False


async def _user_cohort_key(conn: psycopg.AsyncConnection[Any], user_id: str) -> str:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data
            FROM projections
            WHERE user_id = %s
              AND projection_type = 'user_profile'
              AND key = 'me'
            LIMIT 1
            """,
            (user_id,),
        )
        row = await cur.fetchone()

    if row is None or not isinstance(row.get("data"), dict):
        return "tm:unknown|el:unknown"
    return _cohort_key_from_user_profile(row["data"])


async def resolve_population_prior(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_type: str,
    target_key: str,
    retracted_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """Resolve a cohort prior for the current user and projection target."""
    if projection_type not in {"strength_inference", "readiness_inference"}:
        return None
    if not population_priors_enabled():
        return None
    if not await _table_exists(conn, "population_prior_profiles"):
        return None
    if not await is_population_prior_opted_in(conn, user_id, retracted_ids=retracted_ids):
        return None

    cohort_key = await _user_cohort_key(conn, user_id)
    min_cohort_size = population_prior_min_cohort_size()

    lookup_targets = [_normalize(target_key)]
    if projection_type == "strength_inference":
        if STRENGTH_FALLBACK_TARGET_KEY not in lookup_targets:
            lookup_targets.append(STRENGTH_FALLBACK_TARGET_KEY)
    else:
        lookup_targets = [READINESS_TARGET_KEY]

    try:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT target_key, cohort_key, prior_payload, participants_count,
                       sample_size, computed_at
                FROM population_prior_profiles
                WHERE projection_type = %s
                  AND cohort_key = %s
                  AND target_key = ANY(%s)
                  AND participants_count >= %s
                ORDER BY
                    CASE WHEN target_key = %s THEN 0 ELSE 1 END,
                    computed_at DESC
                LIMIT 1
                """,
                (
                    projection_type,
                    cohort_key,
                    lookup_targets,
                    min_cohort_size,
                    lookup_targets[0],
                ),
            )
            row = await cur.fetchone()
    except Exception as exc:
        logger.warning("Population prior lookup skipped: %s", exc)
        return None

    if row is None:
        return None

    prior_payload = row.get("prior_payload")
    if not isinstance(prior_payload, dict):
        return None

    mean = _as_float(prior_payload.get("mean"))
    var = _as_float(prior_payload.get("var"))
    if mean is None or var is None:
        return None
    if var <= 0.0:
        return None

    return {
        "projection_type": projection_type,
        "target_key": row["target_key"],
        "cohort_key": row["cohort_key"],
        "mean": mean,
        "var": var,
        "participants_count": int(row["participants_count"]),
        "sample_size": int(row["sample_size"]),
        "blend_weight": population_prior_blend_weight(),
        "computed_at": (
            row["computed_at"].isoformat()
            if hasattr(row["computed_at"], "isoformat")
            else str(row["computed_at"])
        ),
    }
