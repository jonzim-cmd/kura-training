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

from ..recovery_daily_checkin import normalize_daily_checkin_payload
from ..registry import projection_handler
from ..utils import (
    get_retracted_event_ids,
    load_timezone_preference,
    merge_observed_attributes,
    normalize_temporal_point,
    resolve_timezone_context,
    separate_known_unknown,
)

logger = logging.getLogger(__name__)

_KNOWN_FIELDS_SLEEP: set[str] = {
    "duration_hours",
    "quality",
    "bed_time",
    "bedtime",
    "wake_time",
    "hrv_rmssd",
    "sleep_quality",
}
_KNOWN_FIELDS_SORENESS: set[str] = {"area", "severity", "notes"}
_KNOWN_FIELDS_ENERGY: set[str] = {"level", "time_of_day"}
_KNOWN_FIELDS_DAILY_CHECKIN: set[str] = {
    "bodyweight_kg",
    "sleep_hours",
    "soreness",
    "motivation",
    "hrv_rmssd",
    "sleep_quality",
    "physical_condition",
    "lifestyle_stability",
    "traveling_yesterday",
    "sick_today",
    "alcohol_last_night",
    "training_yesterday",
    "supplements",
    "notes",
    "compact_input",
}


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


@projection_handler(
    "sleep.logged",
    "soreness.logged",
    "energy.logged",
    "recovery.daily_checkin",
    "sleep_target.set",
    dimension_meta={
    "name": "recovery",
    "description": "Recovery signals: sleep, soreness, energy levels",
    "key_structure": "single overview per user",
    "projection_key": "overview",
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
    "output_schema": {
        "timezone_context": {
            "timezone": "IANA timezone used for day/week grouping (e.g. Europe/Berlin)",
            "source": "preference|assumed_default",
            "assumed": "boolean",
            "assumption_disclosure": "string|null",
        },
        "sleep": {
            "recent_entries": [{"date": "ISO 8601 date", "duration_hours": "number", "quality": "string (optional)", "quality_score": "number (optional, 1-10)", "hrv_rmssd": "number (optional)", "bed_time": "string (optional)", "wake_time": "string (optional)"}],
            "weekly_average": [{"week": "ISO 8601 week", "avg_duration_hours": "number", "entries": "integer"}],
            "overall": {"avg_duration_hours": "number", "total_entries": "integer"},
        },
        "soreness": {
            "total_entries": "integer",
            "current": [{"area": "string", "severity": "integer (0-10)", "date": "ISO 8601 date", "notes": "string (optional)"}],
            "recent_entries": [{"date": "ISO 8601 date", "area": "string", "severity": "integer (0-10)", "notes": "string (optional)"}],
        },
        "energy": {
            "recent_entries": [{"date": "ISO 8601 date", "level": "number (1-10)", "time_of_day": "string (optional)"}],
            "weekly_average": [{"week": "ISO 8601 week", "avg_level": "number", "entries": "integer"}],
            "overall": {"avg_level": "number", "total_entries": "integer"},
        },
        "daily_checkins": {
            "recent_entries": [{
                "date": "ISO 8601 date",
                "bodyweight_kg": "number (optional)",
                "sleep_hours": "number (optional)",
                "soreness": "number (optional)",
                "motivation": "number (optional)",
                "hrv_rmssd": "number (optional)",
                "sleep_quality": "number (optional)",
                "physical_condition": "number (optional)",
                "lifestyle_stability": "number (optional)",
                "traveling_yesterday": "boolean (optional)",
                "sick_today": "boolean (optional)",
                "alcohol_last_night": "string (optional)",
                "training_yesterday": "string (optional)",
                "quality_flags": ["string"],
            }],
        },
        "targets": {"sleep": "object — from sleep_target.set event data (optional)"},
        "data_quality": {
            "anomalies": [{"event_id": "string", "field": "string", "value": "any", "expected_range": "[min, max]", "message": "string"}],
            "observed_attributes": {"<event_type>": {"<field>": "integer — count"}},
            "temporal_conflicts": {"<conflict_type>": "integer — number of events with that conflict"},
        },
    },
    "manifest_contribution": _manifest_contribution,
    },
)
async def update_recovery(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Full recompute of recovery projection."""
    user_id = payload["user_id"]
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
              AND event_type IN ('sleep.logged', 'soreness.logged', 'energy.logged', 'recovery.daily_checkin')
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    # Filter retracted events
    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    # Fetch latest non-retracted sleep target
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'sleep_target.set'
            ORDER BY timestamp DESC
            """,
            (user_id,),
        )
        target_rows = await cur.fetchall()

    sleep_target: dict[str, Any] | None = None
    for tr in target_rows:
        if str(tr["id"]) not in retracted_ids:
            sleep_target = tr["data"]
            break

    if not rows and not sleep_target:
        # Clean up: delete any existing projection (all events retracted)
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM projections WHERE user_id = %s AND projection_type = 'recovery' AND key = 'overview'",
                (user_id,),
            )
        return

    if rows:
        last_event_id = rows[-1]["id"]
    else:
        # Only target remains
        last_event_id = next(tr["id"] for tr in target_rows if str(tr["id"]) not in retracted_ids)

    # Sleep data
    sleep_entries: list[dict[str, Any]] = []
    sleep_by_week: dict[str, list[float]] = defaultdict(list)

    # Soreness data
    soreness_entries: list[dict[str, Any]] = []

    # Energy data
    energy_entries: list[dict[str, Any]] = []
    energy_by_week: dict[str, list[float]] = defaultdict(list)
    daily_checkin_entries: list[dict[str, Any]] = []

    anomalies: list[dict[str, Any]] = []
    observed_attr_counts: dict[str, dict[str, int]] = {}
    temporal_conflicts: dict[str, int] = {}

    for row in rows:
        data = row["data"]
        temporal = normalize_temporal_point(
            row["timestamp"],
            timezone_name=timezone_name,
            data=data,
            metadata=row.get("metadata") or {},
        )
        d = temporal.local_date
        for conflict in temporal.conflicts:
            temporal_conflicts[conflict] = temporal_conflicts.get(conflict, 0) + 1
        event_type = row["event_type"]

        if event_type == "sleep.logged":
            _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS_SLEEP)
            merge_observed_attributes(observed_attr_counts, event_type, unknown)
            try:
                duration = float(data["duration_hours"])
            except (KeyError, ValueError, TypeError):
                logger.warning("Skipping sleep event %s: invalid duration_hours", row["id"])
                continue

            # Anomaly detection: sleep bounds
            if duration < 0 or duration > 20:
                anomalies.append({
                    "event_id": str(row["id"]),
                    "field": "duration_hours",
                    "value": duration,
                    "expected_range": [0, 20],
                    "message": f"Sleep duration {duration}h outside plausible range on {d.isoformat()}",
                })

            entry: dict[str, Any] = {
                "date": d.isoformat(),
                "duration_hours": duration,
            }
            if "quality" in data:
                entry["quality"] = data["quality"]
            if "bed_time" in data:
                entry["bed_time"] = data["bed_time"]
            elif "bedtime" in data:
                entry["bed_time"] = data["bedtime"]
            if "wake_time" in data:
                entry["wake_time"] = data["wake_time"]
            sleep_entries.append(entry)
            sleep_by_week[_iso_week(d)].append(duration)

        elif event_type == "soreness.logged":
            _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS_SORENESS)
            merge_observed_attributes(observed_attr_counts, event_type, unknown)
            area = data.get("area", "").strip().lower()
            try:
                severity = int(data["severity"])
            except (KeyError, ValueError, TypeError):
                logger.warning("Skipping soreness event %s: invalid severity", row["id"])
                continue
            if not area:
                continue

            # Anomaly detection: severity bounds
            if severity < 0 or severity > 10:
                anomalies.append({
                    "event_id": str(row["id"]),
                    "field": "severity",
                    "value": severity,
                    "expected_range": [0, 10],
                    "message": f"Soreness severity {severity} outside 0-10 scale on {d.isoformat()}",
                })

            sentry: dict[str, Any] = {
                "date": d.isoformat(),
                "area": area,
                "severity": severity,
            }
            if "notes" in data:
                sentry["notes"] = data["notes"]
            soreness_entries.append(sentry)

        elif event_type == "energy.logged":
            _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS_ENERGY)
            merge_observed_attributes(observed_attr_counts, event_type, unknown)
            try:
                level = float(data["level"])
            except (KeyError, ValueError, TypeError):
                logger.warning("Skipping energy event %s: invalid level", row["id"])
                continue

            # Anomaly detection: energy bounds
            if level < 1 or level > 10:
                anomalies.append({
                    "event_id": str(row["id"]),
                    "field": "level",
                    "value": level,
                    "expected_range": [1, 10],
                    "message": f"Energy level {level} outside 1-10 scale on {d.isoformat()}",
                })

            eentry: dict[str, Any] = {
                "date": d.isoformat(),
                "level": level,
            }
            if "time_of_day" in data:
                eentry["time_of_day"] = data["time_of_day"]
            energy_entries.append(eentry)
            energy_by_week[_iso_week(d)].append(level)

        elif event_type == "recovery.daily_checkin":
            _known, unknown = separate_known_unknown(data, _KNOWN_FIELDS_DAILY_CHECKIN)
            merge_observed_attributes(observed_attr_counts, event_type, unknown)
            normalized = normalize_daily_checkin_payload(data if isinstance(data, dict) else {})

            checkin_entry: dict[str, Any] = {"date": d.isoformat()}
            for key in (
                "bodyweight_kg",
                "sleep_hours",
                "soreness",
                "motivation",
                "hrv_rmssd",
                "sleep_quality",
                "physical_condition",
                "lifestyle_stability",
                "traveling_yesterday",
                "sick_today",
                "alcohol_last_night",
                "training_yesterday",
                "supplements",
            ):
                value = normalized.get(key)
                if value is not None:
                    checkin_entry[key] = value

            quality_flags = list(normalized.get("quality_flags") or [])
            if quality_flags:
                checkin_entry["quality_flags"] = quality_flags
            if normalized.get("parsed_from_compact"):
                checkin_entry["parsed_from_compact"] = True
                checkin_entry["compact_input_mode"] = normalized.get("compact_input_mode")

            notes = normalized.get("notes")
            if isinstance(notes, str) and notes.strip():
                checkin_entry["notes"] = notes.strip()

            daily_checkin_entries.append(checkin_entry)

            sleep_hours = normalized.get("sleep_hours")
            if isinstance(sleep_hours, (int, float)):
                duration = float(sleep_hours)
                if duration < 0 or duration > 20:
                    anomalies.append({
                        "event_id": str(row["id"]),
                        "field": "sleep_hours",
                        "value": duration,
                        "expected_range": [0, 20],
                        "message": (
                            f"Daily check-in sleep_hours {duration} outside plausible range "
                            f"on {d.isoformat()}"
                        ),
                    })
                else:
                    sleep_entry: dict[str, Any] = {
                        "date": d.isoformat(),
                        "duration_hours": round(duration, 2),
                    }
                    sleep_quality = normalized.get("sleep_quality")
                    if isinstance(sleep_quality, (int, float)):
                        sleep_entry["quality_score"] = float(sleep_quality)
                    hrv_rmssd = normalized.get("hrv_rmssd")
                    if isinstance(hrv_rmssd, (int, float)):
                        sleep_entry["hrv_rmssd"] = float(hrv_rmssd)
                    sleep_entries.append(sleep_entry)
                    sleep_by_week[_iso_week(d)].append(duration)

            soreness = normalized.get("soreness")
            if isinstance(soreness, (int, float)):
                severity_float = float(soreness)
                if severity_float < 0 or severity_float > 10:
                    anomalies.append({
                        "event_id": str(row["id"]),
                        "field": "soreness",
                        "value": severity_float,
                        "expected_range": [0, 10],
                        "message": (
                            f"Daily check-in soreness {severity_float} outside plausible range "
                            f"on {d.isoformat()}"
                        ),
                    })
                else:
                    soreness_entry: dict[str, Any] = {
                        "date": d.isoformat(),
                        "area": "overall",
                        "severity": int(round(severity_float)),
                    }
                    if isinstance(notes, str) and notes.strip():
                        soreness_entry["notes"] = notes.strip()
                    soreness_entries.append(soreness_entry)

            motivation = normalized.get("motivation")
            if isinstance(motivation, (int, float)):
                level = float(motivation)
                if level < 1 or level > 10:
                    anomalies.append({
                        "event_id": str(row["id"]),
                        "field": "motivation",
                        "value": level,
                        "expected_range": [1, 10],
                        "message": (
                            f"Daily check-in motivation {level} outside plausible range "
                            f"on {d.isoformat()}"
                        ),
                    })
                else:
                    energy_entries.append(
                        {
                            "date": d.isoformat(),
                            "level": round(level, 2),
                            "time_of_day": "morning",
                        }
                    )
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

    daily_checkins_data: dict[str, Any] = {}
    if daily_checkin_entries:
        daily_checkins_data["recent_entries"] = daily_checkin_entries[-30:]

    projection_data: dict[str, Any] = {
        "timezone_context": timezone_context,
        "sleep": sleep_data,
        "soreness": soreness_data,
        "energy": energy_data,
        "daily_checkins": daily_checkins_data,
        "data_quality": {
            "anomalies": anomalies,
            "observed_attributes": observed_attr_counts,
            "temporal_conflicts": temporal_conflicts,
        },
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
        (
            "Updated recovery for user=%s "
            "(sleep=%d, soreness=%d, energy=%d, daily_checkins=%d, timezone=%s, assumed=%s)"
        ),
        user_id,
        len(sleep_entries),
        len(soreness_entries),
        len(energy_entries),
        len(daily_checkin_entries),
        timezone_name,
        timezone_context["assumed"],
    )
