"""System config — deployment-static configuration for the agent.

Builds the complete system layer from handler declarations, event conventions,
interview guide, and normalization conventions. Written to the system_config
table on worker startup. Changes only when code is deployed.

The agent reads this once per session (or the MCP server caches it at startup)
to understand: what dimensions exist, what events are available, how to log
data correctly, and how to conduct onboarding interviews.
"""

import json
import logging
from typing import Any

import psycopg
from psycopg.types.json import Json

from .event_conventions import get_event_conventions
from .interview_guide import get_interview_guide
from .registry import get_dimension_metadata

logger = logging.getLogger(__name__)


def _get_conventions() -> dict[str, Any]:
    """Return normalization conventions for the agent.

    These tell the agent HOW to log data correctly, preventing
    fragmentation issues like exercises without exercise_id.
    """
    return {
        "exercise_normalization": {
            "rules": [
                "ALWAYS set exercise_id when you recognize the exercise.",
                "When setting both exercise + exercise_id for a user term the first time, "
                "also create exercise.alias_created in the same batch.",
                "When uncertain about the canonical name, ask the user.",
                "Only omit exercise_id when the exercise is truly unknown to you.",
                "Check user.aliases for existing mappings before creating new ones.",
            ],
            "example_batch": [
                {
                    "event_type": "set.logged",
                    "data": {
                        "exercise": "Kniebeuge",
                        "exercise_id": "barbell_back_squat",
                        "weight_kg": 100,
                        "reps": 5,
                    },
                },
                {
                    "event_type": "exercise.alias_created",
                    "data": {
                        "alias": "Kniebeuge",
                        "exercise_id": "barbell_back_squat",
                        "confidence": "confirmed",
                    },
                },
            ],
        },
        "data_correction": {
            "rules": [
                "To correct a wrong event: retract it with event.retracted and "
                "log the correct replacement in the same batch.",
                "Always include retracted_event_type so the system can process "
                "the retraction efficiently.",
                "To clear a profile field, send profile.updated with the field "
                "set to null.",
            ],
            "example_batch": [
                {
                    "event_type": "event.retracted",
                    "data": {
                        "retracted_event_id": "01956abc-def0-7000-8000-000000000001",
                        "retracted_event_type": "bodyweight.logged",
                        "reason": "Typo: entered 150kg instead of 85kg",
                    },
                },
                {
                    "event_type": "bodyweight.logged",
                    "data": {
                        "weight_kg": 85.0,
                        "time_of_day": "morning",
                    },
                },
            ],
        },
    }


def build_dimensions(dimension_metadata: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build the dimensions section from registry declarations.

    Strips non-serializable fields (manifest_contribution callable).
    Includes context_seeds for interview guidance (Decision 8).
    """
    dimensions = {}
    for name, meta in dimension_metadata.items():
        entry: dict[str, Any] = {
            "description": meta.get("description", ""),
            "key_structure": meta.get("key_structure", ""),
            "projection_key": meta.get("projection_key", "overview"),
            "granularity": meta.get("granularity", []),
            "event_types": meta.get("event_types", []),
            "relates_to": meta.get("relates_to", {}),
        }
        if "context_seeds" in meta:
            entry["context_seeds"] = meta["context_seeds"]
        dimensions[name] = entry
    return dimensions


def build_system_config() -> dict[str, Any]:
    """Build the complete system config from all registered sources.

    This is deployment-static: same output for same code version.
    """
    dimension_metadata = get_dimension_metadata()
    return {
        "dimensions": build_dimensions(dimension_metadata),
        "event_conventions": get_event_conventions(),
        "conventions": _get_conventions(),
        "time_conventions": {
            "week": "ISO 8601 (2026-W06)",
            "date": "ISO 8601 (2026-02-08)",
            "timestamp": "ISO 8601 with timezone",
        },
        "interview_guide": get_interview_guide(),
    }


async def ensure_system_config(conn: psycopg.AsyncConnection[Any]) -> None:
    """Write system_config to DB. Called once on worker startup.

    Uses UPSERT — safe to call multiple times. Version increments
    on each write so clients can detect staleness.
    """
    data = build_system_config()

    await conn.execute(
        """
        INSERT INTO system_config (key, data, version, updated_at)
        VALUES ('global', %s, 1, NOW())
        ON CONFLICT (key) DO UPDATE SET
            data = EXCLUDED.data,
            version = system_config.version + 1,
            updated_at = NOW()
        """,
        (Json(data),),
    )
    await conn.commit()
    logger.info("System config written (dimensions=%d, event_conventions=%d)",
                len(data["dimensions"]), len(data["event_conventions"]))
