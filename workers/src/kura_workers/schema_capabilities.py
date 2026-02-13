"""Schema capability helpers for migration-safe worker behavior.

Workers sometimes run against databases that are behind code migrations
(for example during staged rollouts). Handlers should degrade gracefully
when optional relations are missing instead of crashing projection updates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg

_RELATION_SPECS: dict[str, dict[str, Any]] = {
    "external_import_jobs": {
        "required_by": ["quality_health", "training_timeline"],
        "migration": "20260224000001_external_import_jobs.sql",
        "fallback_behavior": (
            "Skip import-job enrichment and continue projection recompute "
            "from events only."
        ),
    }
}


async def relation_exists(
    conn: psycopg.AsyncConnection[Any],
    relation_name: str,
) -> bool:
    """Return True when relation exists in the current DB schema."""
    # to_regclass avoids transaction-aborting undefined_table errors.
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT to_regclass(%s) IS NOT NULL",
            (f"public.{relation_name}",),
        )
        row = await cur.fetchone()
    return bool(row and row[0])


async def detect_relation_capabilities(
    conn: psycopg.AsyncConnection[Any],
    relation_names: list[str],
) -> dict[str, bool]:
    """Return availability map for the requested relation names."""
    availability: dict[str, bool] = {}
    for relation in relation_names:
        availability[relation] = await relation_exists(conn, relation)
    return availability


def build_schema_capability_report(availability: dict[str, bool]) -> dict[str, Any]:
    """Build machine-readable schema capability report payload."""
    relations: dict[str, Any] = {}
    missing_relations: list[str] = []
    for relation_name, available in availability.items():
        spec = _RELATION_SPECS.get(relation_name, {})
        relations[relation_name] = {
            "available": bool(available),
            "required_by": list(spec.get("required_by", [])),
            "migration": spec.get("migration"),
            "fallback_behavior": spec.get("fallback_behavior"),
        }
        if not available:
            missing_relations.append(relation_name)

    return {
        "status": "degraded" if missing_relations else "healthy",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "missing_relations": missing_relations,
        "relations": relations,
    }
