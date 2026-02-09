"""Shared utility functions for Kura workers."""

from typing import Any

import psycopg
from psycopg.rows import dict_row


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
    conn: psycopg.AsyncConnection[Any], user_id: str
) -> dict[str, str]:
    """Build alias → canonical target map from exercise.alias_created events.

    Returns {alias_lower: target_lower}. Direct event query, no cross-projection dependency.
    Confidence field intentionally omitted — this is for resolution only.
    See user_profile projection for full alias metadata (target + confidence).
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data
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
