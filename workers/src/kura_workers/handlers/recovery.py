"""Recovery dimension handler.

Reacts to sleep.logged, soreness.logged, and energy.logged events.
Computes sleep patterns, soreness tracking, and energy levels.

Full recompute on every event — idempotent by design.
"""

import json
import logging
from collections import defaultdict
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..registry import projection_handler

logger = logging.getLogger(__name__)


def _iso_week(d: date) -> str:
    """Return ISO week string like '2026-W06'."""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract summary for user_profile manifest."""
    if not projection_rows:
        return {}
    data = projection_rows[0]["data"]
    result: dict[str, Any] = {}
    sleep = data.get("sleep", {})
    if sleep.get("overall"):
        result["avg_sleep_hours"] = sleep["overall"]["avg_duration_hours"]
        result["total_sleep_entries"] = sleep["overall"]["total_entries"]
    soreness = data.get("soreness", {})
    if soreness.get("total_entries"):
        result["total_soreness_entries"] = soreness["total_entries"]
    energy = data.get("energy", {})
    if energy.get("overall"):
        result["avg_energy_level"] = energy["overall"]["avg_level"]
    if data.get("targets"):
        result["has_targets"] = True
    return result


@projection_handler("sleep.logged", "soreness.logged", "energy.logged", "sleep_target.set", dimension_meta={
    "name": "recovery",
    "description": "Recovery signals: sleep, soreness, energy levels",
    "key_structure": "single overview per user",
    "granularity": ["day", "week"],
    "relates_to": {
        "training_timeline": {"join": "day", "why": "training load vs recovery"},
        "body_composition": {"join": "day", "why": "weight fluctuations vs sleep/recovery"},
    },
    "context_seeds": [
        "sleep_habits",
        "recovery_priorities",
        "stress_factors",
    ],
    "manifest_contribution": _manifest_contribution,
})
async def update_recovery(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of recovery projection."""
    user_id = payload["user_id"]

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type IN ('sleep.logged', 'soreness.logged', 'energy.logged')
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    # Fetch latest sleep target (latest event wins)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'sleep_target.set'
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (user_id,),
        )
        target_row = await cur.fetchone()

    sleep_target: dict[str, Any] | None = None
    if target_row:
        sleep_target = target_row["data"]

    if not rows and not sleep_target:
        return

    last_event_id = rows[-1]["id"] if rows else target_row["id"]

    # Sleep data
    sleep_entries: list[dict[str, Any]] = []
    sleep_by_week: dict[str, list[float]] = defaultdict(list)

    # Soreness data
    soreness_entries: list[dict[str, Any]] = []

    # Energy data
    energy_entries: list[dict[str, Any]] = []
    energy_by_week: dict[str, list[float]] = defaultdict(list)

    for row in rows:
        data = row["data"]
        ts = row["timestamp"]
        d = ts.date()
        event_type = row["event_type"]

        if event_type == "sleep.logged":
            try:
                duration = float(data["duration_hours"])
            except (KeyError, ValueError, TypeError):
                logger.warning("Skipping sleep event %s: invalid duration_hours", row["id"])
                continue

            entry: dict[str, Any] = {
                "date": d.isoformat(),
                "duration_hours": duration,
            }
            if "quality" in data:
                entry["quality"] = data["quality"]
            if "bedtime" in data:
                entry["bedtime"] = data["bedtime"]
            if "wake_time" in data:
                entry["wake_time"] = data["wake_time"]
            sleep_entries.append(entry)
            sleep_by_week[_iso_week(d)].append(duration)

        elif event_type == "soreness.logged":
            area = data.get("area", "").strip().lower()
            try:
                severity = int(data["severity"])
            except (KeyError, ValueError, TypeError):
                logger.warning("Skipping soreness event %s: invalid severity", row["id"])
                continue
            if not area:
                continue

            sentry: dict[str, Any] = {
                "date": d.isoformat(),
                "area": area,
                "severity": severity,
            }
            if "notes" in data:
                sentry["notes"] = data["notes"]
            soreness_entries.append(sentry)

        elif event_type == "energy.logged":
            try:
                level = float(data["level"])
            except (KeyError, ValueError, TypeError):
                logger.warning("Skipping energy event %s: invalid level", row["id"])
                continue

            eentry: dict[str, Any] = {
                "date": d.isoformat(),
                "level": level,
            }
            if "time_of_day" in data:
                eentry["time_of_day"] = data["time_of_day"]
            energy_entries.append(eentry)
            energy_by_week[_iso_week(d)].append(level)

    # Build sleep section
    sleep_data: dict[str, Any] = {}
    if sleep_entries:
        sleep_data["recent_entries"] = sleep_entries[-30:]

        sorted_weeks = sorted(sleep_by_week.keys(), reverse=True)[:26]
        sorted_weeks.reverse()
        sleep_data["weekly_average"] = [
            {
                "week": wk,
                "avg_duration_hours": round(sum(sleep_by_week[wk]) / len(sleep_by_week[wk]), 1),
                "entries": len(sleep_by_week[wk]),
            }
            for wk in sorted_weeks
        ]

        all_durations = [e["duration_hours"] for e in sleep_entries]
        sleep_data["overall"] = {
            "avg_duration_hours": round(sum(all_durations) / len(all_durations), 1),
            "total_entries": len(sleep_entries),
        }

    # Build soreness section
    soreness_data: dict[str, Any] = {"total_entries": len(soreness_entries)}
    if soreness_entries:
        # Current: most recent entry per area
        current_by_area: dict[str, dict[str, Any]] = {}
        for sentry in soreness_entries:
            current_by_area[sentry["area"]] = sentry
        soreness_data["current"] = list(current_by_area.values())
        soreness_data["recent_entries"] = soreness_entries[-30:]

    # Build energy section
    energy_data: dict[str, Any] = {}
    if energy_entries:
        energy_data["recent_entries"] = energy_entries[-30:]

        sorted_weeks = sorted(energy_by_week.keys(), reverse=True)[:26]
        sorted_weeks.reverse()
        energy_data["weekly_average"] = [
            {
                "week": wk,
                "avg_level": round(sum(energy_by_week[wk]) / len(energy_by_week[wk]), 1),
                "entries": len(energy_by_week[wk]),
            }
            for wk in sorted_weeks
        ]

        all_levels = [e["level"] for e in energy_entries]
        energy_data["overall"] = {
            "avg_level": round(sum(all_levels) / len(all_levels), 1),
            "total_entries": len(energy_entries),
        }

    projection_data: dict[str, Any] = {
        "sleep": sleep_data,
        "soreness": soreness_data,
        "energy": energy_data,
    }

    if sleep_target:
        projection_data["targets"] = {"sleep": sleep_target}

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'recovery', 'overview', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), str(last_event_id)),
        )

    logger.info(
        "Updated recovery for user=%s (sleep=%d, soreness=%d, energy=%d)",
        user_id, len(sleep_entries), len(soreness_entries), len(energy_entries),
    )
