"""Shared utility functions for Kura workers."""

from typing import Any


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
