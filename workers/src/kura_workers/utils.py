"""Shared utility functions for Kura workers."""

import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strength estimation
# ---------------------------------------------------------------------------


def epley_1rm(weight_kg: float, reps: int) -> float:
    """Estimate 1RM using the Epley formula. Returns 0 for invalid inputs."""
    if reps <= 0 or weight_kg <= 0:
        return 0.0
    if reps == 1:
        return weight_kg
    return weight_kg * (1 + reps / 30)


# ---------------------------------------------------------------------------
# Adaptive Projection helpers (Decision 10, Phase 1: Graceful Degradation)
# ---------------------------------------------------------------------------


def separate_known_unknown(
    data: dict[str, Any], known_fields: set[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split event data into known (handler-processed) and unknown (passthrough) fields.

    Returns (known, unknown). Unknown fields are preserved in projections
    so the agent can access them even if no handler logic exists yet.
    """
    known: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    for key, value in data.items():
        if key in known_fields:
            known[key] = value
        else:
            unknown[key] = value
    return known, unknown


def merge_observed_attributes(
    accumulator: dict[str, int], new_unknown: dict[str, Any]
) -> None:
    """Track frequency of unknown fields across events (mutates accumulator)."""
    for key in new_unknown:
        accumulator[key] = accumulator.get(key, 0) + 1


def check_expected_fields(
    data: dict[str, Any], expected: dict[str, str]
) -> list[dict[str, Any]]:
    """Return data_quality hints for missing expected fields.

    ``expected`` maps field names to human-readable hint messages, e.g.
    {"weight_kg": "No weight — bodyweight exercise?"}.
    """
    return [
        {"type": "missing_expected_field", "field": field, "hint": hint}
        for field, hint in expected.items()
        if field not in data
    ]


async def get_retracted_event_ids(
    conn: psycopg.AsyncConnection[Any], user_id: str
) -> set[str]:
    """Return set of event IDs that have been retracted by event.retracted events.

    Called once per handler invocation. Every handler uses this to filter
    retracted events from its full replay. Retractions are rare, so the
    set is typically empty — but filtering must happen on every call to
    handle the case where a retraction occurred between normal events.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data->>'retracted_event_id' AS retracted_id
            FROM events
            WHERE user_id = %s
              AND event_type = 'event.retracted'
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    return {row["retracted_id"] for row in rows if row["retracted_id"]}


def resolve_exercise_key(data: dict[str, Any]) -> str | None:
    """Resolve the canonical exercise key from event data.

    Prefers exercise_id (canonical) over exercise (free text).
    Both are normalized to lowercase with whitespace stripped.
    """
    exercise_id = data.get("exercise_id", "").strip().lower()
    if exercise_id:
        return exercise_id

    exercise = data.get("exercise", "").strip().lower()
    if exercise:
        return exercise

    return None


async def get_alias_map(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    retracted_ids: set[str] | None = None,
) -> dict[str, str]:
    """Build alias → canonical target map from exercise.alias_created events.

    Returns {alias_lower: target_lower}. Direct event query, no cross-projection dependency.
    Confidence field intentionally omitted — this is for resolution only.
    See user_profile projection for full alias metadata (target + confidence).

    If retracted_ids is provided, excludes those events from the map.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'exercise.alias_created'
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    alias_map: dict[str, str] = {}
    for row in rows:
        if retracted_ids and str(row["id"]) in retracted_ids:
            continue
        data = row["data"]
        alias = data.get("alias", "").strip().lower()
        target = data.get("exercise_id", "").strip().lower()
        if alias and target:
            alias_map[alias] = target
    return alias_map


def resolve_through_aliases(key: str, alias_map: dict[str, str]) -> str:
    """Single lookup: return canonical target or key unchanged. No chains."""
    return alias_map.get(key, key)


def find_all_keys_for_canonical(
    canonical: str, alias_map: dict[str, str]
) -> set[str]:
    """Return canonical + all aliases pointing to it."""
    keys = {canonical}
    for alias, target in alias_map.items():
        if target == canonical:
            keys.add(alias)
    return keys
