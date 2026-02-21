"""Supplements dimension handler.

Native supplement lifecycle support:
- regimen definitions (daily defaults)
- temporary pauses/resume/stop
- explicit taken/skipped exceptions
- adherence windows with assumed-vs-explicit transparency
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, timedelta
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

_WINDOW_DAYS = 60
_SUMMARY_WINDOW_DAYS = 30

_WEEKDAY_MAP = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

_KNOWN_FIELDS: dict[str, set[str]] = {
    "supplement.regimen.set": {
        "name",
        "dose_amount",
        "dose",
        "dose_unit",
        "unit",
        "cadence",
        "frequency",
        "times_per_day",
        "days_of_week",
        "start_date",
        "date",
        "assume_taken_by_default",
        "assume_taken",
        "notes",
    },
    "supplement.regimen.paused": {
        "name",
        "start_date",
        "until_date",
        "duration_days",
        "date",
        "reason",
    },
    "supplement.regimen.resumed": {
        "name",
        "effective_date",
        "date",
        "reason",
    },
    "supplement.regimen.stopped": {
        "name",
        "effective_date",
        "date",
        "reason",
    },
    "supplement.taken": {
        "name",
        "date",
        "dose_amount",
        "dose",
        "dose_unit",
        "unit",
        "notes",
    },
    "supplement.skipped": {
        "name",
        "date",
        "reason",
        "notes",
    },
    "supplement.logged": {
        "name",
        "status",
        "date",
        "dose_amount",
        "dose",
        "dose_unit",
        "unit",
        "timing",
        "reason",
        "notes",
    },
    "recovery.daily_checkin": {
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
    },
}


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not projection_rows:
        return {}
    data = projection_rows[0]["data"]
    adherence = data.get("adherence_summary") or {}
    active_stack = data.get("active_stack") or []
    result: dict[str, Any] = {}
    if isinstance(active_stack, list) and active_stack:
        result["active_regimens"] = len(active_stack)
    adherence_rate = adherence.get("adherence_rate_30d")
    if isinstance(adherence_rate, (int, float)):
        result["adherence_rate_30d"] = float(adherence_rate)
    return result


def _parse_iso_date(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _canonical_name(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    normalized = " ".join(raw.strip().lower().split())
    return normalized or None


def _to_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return float(raw) > 0.0
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    if normalized in {"true", "yes", "y", "1"}:
        return True
    if normalized in {"false", "no", "n", "0"}:
        return False
    return None


def _to_positive_int(raw: Any) -> int | None:
    try:
        if raw is None:
            return None
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _to_float(raw: Any) -> float | None:
    try:
        if raw is None:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _resolve_effective_day(
    data: dict[str, Any],
    *,
    fallback_day: date,
    field_candidates: tuple[str, ...],
) -> tuple[date, str | None]:
    for field in field_candidates:
        if field not in data:
            continue
        parsed = _parse_iso_date(data.get(field))
        if parsed is None:
            return fallback_day, field
        return parsed, None
    return fallback_day, None


def _normalize_days_of_week(raw: Any) -> tuple[list[int] | None, list[str]]:
    if raw is None:
        return None, []
    tokens: list[str] = []
    if isinstance(raw, str):
        tokens = [token.strip().lower() for token in raw.replace(";", ",").split(",")]
    elif isinstance(raw, list):
        tokens = [str(token).strip().lower() for token in raw]
    else:
        return None, [str(raw)]

    result: list[int] = []
    invalid: list[str] = []
    for token in tokens:
        if not token:
            continue
        if token in _WEEKDAY_MAP:
            result.append(_WEEKDAY_MAP[token])
        else:
            invalid.append(token)
    if not result:
        return None, invalid
    return sorted(set(result)), invalid


def _is_expected_day(regimen: dict[str, Any], local_day: date) -> bool:
    cadence = str(regimen.get("cadence") or "daily")
    if cadence == "daily":
        return True
    days = regimen.get("days_of_week")
    if isinstance(days, list) and days:
        return local_day.weekday() in {int(v) for v in days}
    start_day = regimen.get("start_date")
    if isinstance(start_day, date):
        return local_day.weekday() == start_day.weekday()
    return True


def _iter_pause_windows(regimen: dict[str, Any]) -> list[dict[str, date | None]]:
    windows: list[dict[str, date | None]] = []
    raw_windows = regimen.get("pause_windows")
    if isinstance(raw_windows, list):
        for item in raw_windows:
            if not isinstance(item, dict):
                continue
            start = item.get("start")
            if not isinstance(start, date):
                continue
            until = item.get("until")
            windows.append(
                {
                    "start": start,
                    "until": until if isinstance(until, date) else None,
                }
            )
    if windows:
        return windows

    # Legacy single-window fallback.
    pause_start = regimen.get("pause_start")
    if isinstance(pause_start, date):
        pause_until = regimen.get("pause_until")
        windows.append(
            {
                "start": pause_start,
                "until": pause_until if isinstance(pause_until, date) else None,
            }
        )
    return windows


def _sync_pause_fields(regimen: dict[str, Any]) -> None:
    windows = _iter_pause_windows(regimen)
    regimen["pause_windows"] = windows
    if not windows:
        regimen["pause_start"] = None
        regimen["pause_until"] = None
        return
    latest = windows[-1]
    regimen["pause_start"] = latest["start"]
    regimen["pause_until"] = latest["until"]


def _append_pause_window(
    regimen: dict[str, Any], *, start_day: date, until_day: date | None
) -> None:
    windows = _iter_pause_windows(regimen)
    windows.append({"start": start_day, "until": until_day})
    windows.sort(key=lambda item: item["start"])
    regimen["pause_windows"] = windows
    _sync_pause_fields(regimen)


def _truncate_pause_windows_from(regimen: dict[str, Any], effective_day: date) -> None:
    windows = _iter_pause_windows(regimen)
    for idx in range(len(windows) - 1, -1, -1):
        start = windows[idx]["start"]
        until = windows[idx]["until"]
        if start > effective_day:
            windows.pop(idx)
            continue
        if until is None or until >= effective_day:
            closed_until = effective_day - timedelta(days=1)
            if closed_until < start:
                windows.pop(idx)
            else:
                windows[idx] = {"start": start, "until": closed_until}
            break
    regimen["pause_windows"] = windows
    _sync_pause_fields(regimen)


def _pause_window_on_day(regimen: dict[str, Any], local_day: date) -> dict[str, date | None] | None:
    for window in _iter_pause_windows(regimen):
        start = window["start"]
        until = window["until"]
        if local_day < start:
            continue
        if until is None or local_day <= until:
            return window
    return None


def _regimen_state_on_day(regimen: dict[str, Any], local_day: date) -> str:
    start_day = regimen.get("start_date")
    if isinstance(start_day, date) and local_day < start_day:
        return "not_started"

    stopped = regimen.get("stopped_date")
    if isinstance(stopped, date) and local_day >= stopped:
        return "stopped"

    if _pause_window_on_day(regimen, local_day) is not None:
        return "paused"

    return "active"


def _default_regimen(name: str, *, start_day: date, display_name: str | None) -> dict[str, Any]:
    return {
        "name": name,
        "display_name": display_name or name,
        "dose_amount": None,
        "dose_unit": None,
        "cadence": "daily",
        "times_per_day": 1,
        "days_of_week": None,
        "start_date": start_day,
        "assume_taken_by_default": True,
        "notes": None,
        "pause_windows": [],
        "pause_start": None,
        "pause_until": None,
        "stopped_date": None,
    }


def _init_per_supplement_stats(
    regimens: dict[str, dict[str, Any]],
    taken_by_day: dict[date, set[str]],
    skipped_by_day: dict[date, set[str]],
) -> dict[str, dict[str, Any]]:
    names = set(regimens.keys())
    for day_names in taken_by_day.values():
        names.update(day_names)
    for day_names in skipped_by_day.values():
        names.update(day_names)

    stats: dict[str, dict[str, Any]] = {}
    for name in names:
        regimen = regimens.get(name)
        stats[name] = {
            "name": name,
            "display_name": regimen.get("display_name", name) if regimen else name,
            "expected_30d": 0,
            "taken_explicit_30d": 0,
            "taken_assumed_30d": 0,
            "skipped_30d": 0,
            "missing_30d": 0,
            "paused_days_30d": 0,
            "last_taken_date": None,
            "last_skipped_date": None,
        }
    return stats


def _update_last_event_dates(
    per_supplement: dict[str, dict[str, Any]],
    *,
    taken_by_day: dict[date, set[str]],
    skipped_by_day: dict[date, set[str]],
) -> None:
    for day, names in taken_by_day.items():
        for name in names:
            stats = per_supplement.get(name)
            if stats is None:
                continue
            prev = stats.get("last_taken_date")
            if not isinstance(prev, date) or day > prev:
                stats["last_taken_date"] = day

    for day, names in skipped_by_day.items():
        for name in names:
            stats = per_supplement.get(name)
            if stats is None:
                continue
            prev = stats.get("last_skipped_date")
            if not isinstance(prev, date) or day > prev:
                stats["last_skipped_date"] = day


def _build_daily_status(
    *,
    regimens: dict[str, dict[str, Any]],
    taken_by_day: dict[date, set[str]],
    skipped_by_day: dict[date, set[str]],
    per_supplement: dict[str, dict[str, Any]],
    window_start: date,
    summary_start: date,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    summary = {
        "expected_30d": 0,
        "taken_explicit_30d": 0,
        "taken_assumed_30d": 0,
        "skipped_30d": 0,
        "missing_30d": 0,
        "paused_days_30d": 0,
    }
    daily_status: list[dict[str, Any]] = []

    for day_offset in range(_WINDOW_DAYS):
        local_day = window_start + timedelta(days=day_offset)
        taken_today = set(taken_by_day.get(local_day, set()))
        skipped_today = set(skipped_by_day.get(local_day, set()))

        expected: list[str] = []
        taken_explicit: list[str] = []
        taken_assumed: list[str] = []
        skipped: list[str] = []
        missing: list[str] = []
        paused: list[str] = []

        regimen_active_count = 0
        for name, regimen in sorted(regimens.items()):
            state = _regimen_state_on_day(regimen, local_day)
            expected_day = state == "active" and _is_expected_day(regimen, local_day)
            if state == "active":
                regimen_active_count += 1

            if local_day >= summary_start and state == "paused":
                per_supplement[name]["paused_days_30d"] += 1
                summary["paused_days_30d"] += 1

            if state == "paused":
                paused.append(name)
                continue
            if state in {"not_started", "stopped"}:
                continue
            if not expected_day:
                continue

            expected.append(name)
            if local_day >= summary_start:
                per_supplement[name]["expected_30d"] += 1
                summary["expected_30d"] += 1

            if name in skipped_today:
                skipped.append(name)
                if local_day >= summary_start:
                    per_supplement[name]["skipped_30d"] += 1
                    summary["skipped_30d"] += 1
            elif name in taken_today:
                taken_explicit.append(name)
                if local_day >= summary_start:
                    per_supplement[name]["taken_explicit_30d"] += 1
                    summary["taken_explicit_30d"] += 1
            elif bool(regimen.get("assume_taken_by_default", True)):
                taken_assumed.append(name)
                if local_day >= summary_start:
                    per_supplement[name]["taken_assumed_30d"] += 1
                    summary["taken_assumed_30d"] += 1
            else:
                missing.append(name)
                if local_day >= summary_start:
                    per_supplement[name]["missing_30d"] += 1
                    summary["missing_30d"] += 1

        expected_set = set(expected)
        extra_taken = sorted(taken_today - expected_set)
        extra_skipped = sorted(skipped_today - expected_set)
        if local_day >= summary_start:
            for name in extra_taken:
                per_supplement[name]["taken_explicit_30d"] += 1
            for name in extra_skipped:
                per_supplement[name]["skipped_30d"] += 1

        expected_count = len(expected)
        adherence_rate: float | None = None
        if expected_count > 0:
            adherence_rate = (len(taken_explicit) + len(taken_assumed)) / expected_count

        daily_status.append(
            {
                "date": local_day.isoformat(),
                "expected": sorted(expected),
                "taken_explicit": sorted(taken_explicit),
                "taken_assumed": sorted(taken_assumed),
                "skipped": sorted(skipped),
                "missing": sorted(missing),
                "paused": sorted(paused),
                "extra_taken": extra_taken,
                "extra_skipped": extra_skipped,
                "expected_count": expected_count,
                "adherence_rate": round(adherence_rate, 3) if adherence_rate is not None else None,
                "regimen_active_count": regimen_active_count,
            }
        )

    return daily_status, summary


def _build_regimen_views(
    regimens: dict[str, dict[str, Any]],
    *,
    window_end: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    regimen_catalog: list[dict[str, Any]] = []
    active_stack: list[dict[str, Any]] = []
    for name, regimen in sorted(regimens.items()):
        state = _regimen_state_on_day(regimen, window_end)
        active_pause_window = _pause_window_on_day(regimen, window_end)
        entry = {
            "name": name,
            "display_name": regimen.get("display_name", name),
            "state": state,
            "cadence": regimen.get("cadence", "daily"),
            "times_per_day": regimen.get("times_per_day", 1),
            "days_of_week": regimen.get("days_of_week"),
            "dose_amount": regimen.get("dose_amount"),
            "dose_unit": regimen.get("dose_unit"),
            "start_date": regimen["start_date"].isoformat()
            if isinstance(regimen.get("start_date"), date)
            else None,
            "pause_until": active_pause_window["until"].isoformat()
            if isinstance(active_pause_window, dict) and isinstance(active_pause_window.get("until"), date)
            else None,
            "stopped_date": regimen["stopped_date"].isoformat()
            if isinstance(regimen.get("stopped_date"), date)
            else None,
            "assume_taken_by_default": bool(regimen.get("assume_taken_by_default", True)),
            "notes": regimen.get("notes"),
        }
        regimen_catalog.append(entry)
        if state in {"active", "paused"}:
            active_stack.append(entry)
    return regimen_catalog, active_stack


def _build_per_supplement_rows(
    *,
    per_supplement: dict[str, dict[str, Any]],
    regimens: dict[str, dict[str, Any]],
    window_end: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, stats in sorted(per_supplement.items(), key=lambda item: item[0]):
        regimen = regimens.get(name)
        current_state = _regimen_state_on_day(regimen, window_end) if regimen else "ad_hoc"
        expected_30d = int(stats["expected_30d"])
        taken_30d = int(stats["taken_explicit_30d"]) + int(stats["taken_assumed_30d"])
        adherence_rate_30d = (taken_30d / expected_30d) if expected_30d > 0 else None
        rows.append(
            {
                "name": name,
                "display_name": stats["display_name"],
                "state": current_state,
                "expected_30d": expected_30d,
                "taken_explicit_30d": int(stats["taken_explicit_30d"]),
                "taken_assumed_30d": int(stats["taken_assumed_30d"]),
                "skipped_30d": int(stats["skipped_30d"]),
                "missing_30d": int(stats["missing_30d"]),
                "paused_days_30d": int(stats["paused_days_30d"]),
                "adherence_rate_30d": round(adherence_rate_30d, 3)
                if adherence_rate_30d is not None
                else None,
                "last_taken_date": stats["last_taken_date"].isoformat()
                if isinstance(stats.get("last_taken_date"), date)
                else None,
                "last_skipped_date": stats["last_skipped_date"].isoformat()
                if isinstance(stats.get("last_skipped_date"), date)
                else None,
            }
        )
    return rows


@projection_handler(
    "supplement.regimen.set",
    "supplement.regimen.paused",
    "supplement.regimen.resumed",
    "supplement.regimen.stopped",
    "supplement.taken",
    "supplement.skipped",
    "supplement.logged",
    "recovery.daily_checkin",
    dimension_meta={
        "name": "supplements",
        "description": "Supplement regimen lifecycle, daily adherence, and exception tracking",
        "key_structure": "single overview per user",
        "projection_key": "overview",
        "granularity": ["regimen", "day", "window"],
        "relates_to": {
            "recovery": {"join": "day", "why": "check-in supplement context and readiness signals"},
            "nutrition": {"join": "day", "why": "intake behavior context"},
            "training_timeline": {"join": "day", "why": "adherence vs training/recovery patterns"},
            "causal_inference": {"join": "day", "why": "supplement adherence as intervention/confounder"},
        },
        "context_seeds": [
            "supplement_stack",
            "daily_routine",
            "recovery_habits",
        ],
        "output_schema": {
            "timezone_context": {
                "timezone": "IANA timezone used for day grouping",
                "source": "preference|assumed_default",
                "assumed": "boolean",
                "assumption_disclosure": "string|null",
            },
            "window": {
                "from": "ISO 8601 date",
                "to": "ISO 8601 date",
                "days": "integer",
            },
            "active_stack": [{
                "name": "string",
                "display_name": "string",
                "state": "active|paused",
                "cadence": "string",
                "dose_amount": "number (optional)",
                "dose_unit": "string (optional)",
                "start_date": "ISO 8601 date",
                "pause_until": "ISO 8601 date|null",
                "assume_taken_by_default": "boolean",
            }],
            "regimen_catalog": [{
                "name": "string",
                "display_name": "string",
                "state": "active|paused|stopped",
                "cadence": "string",
                "days_of_week": ["integer 0..6"],
                "start_date": "ISO 8601 date",
                "stopped_date": "ISO 8601 date|null",
            }],
            "daily_status": [{
                "date": "ISO 8601 date",
                "expected": ["string"],
                "taken_explicit": ["string"],
                "taken_assumed": ["string"],
                "skipped": ["string"],
                "missing": ["string"],
                "paused": ["string"],
                "extra_taken": ["string"],
                "extra_skipped": ["string"],
                "expected_count": "integer",
                "adherence_rate": "number|null",
                "regimen_active_count": "integer",
            }],
            "adherence_summary": {
                "window_days": "integer",
                "expected_30d": "integer",
                "taken_explicit_30d": "integer",
                "taken_assumed_30d": "integer",
                "skipped_30d": "integer",
                "missing_30d": "integer",
                "paused_days_30d": "integer",
                "adherence_rate_30d": "number|null",
                "explicit_confirmation_rate_30d": "number|null",
            },
            "per_supplement": [{
                "name": "string",
                "display_name": "string",
                "state": "string",
                "expected_30d": "integer",
                "taken_explicit_30d": "integer",
                "taken_assumed_30d": "integer",
                "skipped_30d": "integer",
                "missing_30d": "integer",
                "adherence_rate_30d": "number|null",
                "last_taken_date": "ISO 8601 date|null",
                "last_skipped_date": "ISO 8601 date|null",
            }],
            "recent_events": [{
                "date": "ISO 8601 date",
                "event_type": "string",
                "name": "string",
            }],
            "data_quality": {
                "anomalies": [{
                    "event_id": "string",
                    "field": "string",
                    "value": "any",
                    "message": "string",
                }],
                "observed_attributes": {"<event_type>": {"<field>": "integer"}},
                "temporal_conflicts": {"<conflict_type>": "integer"},
            },
        },
        "manifest_contribution": _manifest_contribution,
    },
)
async def update_supplements(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)
    timezone_pref = await load_timezone_preference(conn, user_id, retracted_ids)
    timezone_context = resolve_timezone_context(timezone_pref)
    timezone_name = timezone_context["timezone"]

    tracked_event_types = [
        "supplement.regimen.set",
        "supplement.regimen.paused",
        "supplement.regimen.resumed",
        "supplement.regimen.stopped",
        "supplement.taken",
        "supplement.skipped",
        "supplement.logged",
        "recovery.daily_checkin",
    ]

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id, tracked_event_types),
        )
        rows = await cur.fetchall()

    rows = [row for row in rows if str(row["id"]) not in retracted_ids]
    if not rows:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM projections WHERE user_id = %s AND projection_type = 'supplements' AND key = 'overview'",
                (user_id,),
            )
        return

    regimens: dict[str, dict[str, Any]] = {}
    taken_by_day: dict[date, set[str]] = defaultdict(set)
    skipped_by_day: dict[date, set[str]] = defaultdict(set)
    observed_attr_counts: dict[str, dict[str, int]] = {}
    temporal_conflicts: dict[str, int] = {}
    anomalies: list[dict[str, Any]] = []
    recent_events: list[dict[str, Any]] = []
    all_days: set[date] = set()

    for row in rows:
        event_type = str(row["event_type"])
        data = row["data"] if isinstance(row.get("data"), dict) else {}
        temporal = normalize_temporal_point(
            row["timestamp"],
            timezone_name=timezone_name,
            data=data,
            metadata=row.get("metadata") or {},
        )
        local_day = temporal.local_date
        all_days.add(local_day)
        for conflict in temporal.conflicts:
            temporal_conflicts[conflict] = temporal_conflicts.get(conflict, 0) + 1

        known_fields = _KNOWN_FIELDS.get(event_type, set())
        _known, unknown = separate_known_unknown(data, known_fields)
        merge_observed_attributes(observed_attr_counts, event_type, unknown)

        if event_type == "recovery.daily_checkin":
            normalized = normalize_daily_checkin_payload(data)
            supplements = normalized.get("supplements")
            if isinstance(supplements, list):
                for raw_name in supplements:
                    name = _canonical_name(raw_name)
                    if name:
                        taken_by_day[local_day].add(name)
                        recent_events.append(
                            {
                                "date": local_day.isoformat(),
                                "event_type": "recovery.daily_checkin",
                                "name": name,
                            }
                        )
            continue

        name = _canonical_name(data.get("name"))
        if not name:
            anomalies.append(
                {
                    "event_id": str(row["id"]),
                    "field": "name",
                    "value": data.get("name"),
                    "message": f"{event_type} requires a non-empty name",
                }
            )
            continue

        display_name = str(data.get("name")).strip() if isinstance(data.get("name"), str) else name
        recent_events.append(
            {
                "date": local_day.isoformat(),
                "event_type": event_type,
                "name": name,
            }
        )

        if event_type in {
            "supplement.regimen.set",
            "supplement.regimen.paused",
            "supplement.regimen.resumed",
            "supplement.regimen.stopped",
        }:
            regimen = regimens.get(name)
            if regimen is None:
                regimen = _default_regimen(name, start_day=local_day, display_name=display_name)
                regimens[name] = regimen
            regimen["display_name"] = display_name

            if event_type == "supplement.regimen.set":
                start_day, invalid_field = _resolve_effective_day(
                    data,
                    fallback_day=local_day,
                    field_candidates=("start_date", "date"),
                )
                if invalid_field:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": invalid_field,
                            "value": data.get(invalid_field),
                            "message": "Invalid ISO date, falling back to event day",
                        }
                    )
                regimen["start_date"] = start_day
                all_days.add(start_day)

                cadence = str(data.get("cadence", data.get("frequency", regimen["cadence"]))).strip().lower()
                if cadence in {"daily", "weekly", "custom"}:
                    regimen["cadence"] = cadence
                else:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": "cadence",
                            "value": data.get("cadence", data.get("frequency")),
                            "message": "Unsupported cadence; keeping previous value",
                        }
                    )

                times_per_day = _to_positive_int(data.get("times_per_day"))
                if data.get("times_per_day") is not None and times_per_day is None:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": "times_per_day",
                            "value": data.get("times_per_day"),
                            "message": "times_per_day must be a positive integer",
                        }
                    )
                if times_per_day is not None:
                    regimen["times_per_day"] = min(times_per_day, 8)

                days_of_week, invalid_days = _normalize_days_of_week(data.get("days_of_week"))
                if invalid_days:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": "days_of_week",
                            "value": invalid_days,
                            "message": "Some weekday tokens are invalid and were ignored",
                        }
                    )
                if days_of_week is not None:
                    regimen["days_of_week"] = days_of_week
                elif regimen["cadence"] in {"weekly", "custom"} and regimen.get("days_of_week") is None:
                    regimen["days_of_week"] = [start_day.weekday()]

                dose_amount = _to_float(data.get("dose_amount", data.get("dose")))
                if data.get("dose_amount", data.get("dose")) is not None and dose_amount is None:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": "dose_amount",
                            "value": data.get("dose_amount", data.get("dose")),
                            "message": "dose_amount must be numeric",
                        }
                    )
                if dose_amount is not None:
                    regimen["dose_amount"] = dose_amount

                dose_unit = data.get("dose_unit", data.get("unit"))
                if isinstance(dose_unit, str) and dose_unit.strip():
                    regimen["dose_unit"] = dose_unit.strip().lower()

                assume = _to_bool(
                    data.get("assume_taken_by_default", data.get("assume_taken"))
                )
                if assume is not None:
                    regimen["assume_taken_by_default"] = assume

                if isinstance(data.get("notes"), str) and data["notes"].strip():
                    regimen["notes"] = data["notes"].strip()

                regimen["pause_windows"] = []
                _sync_pause_fields(regimen)
                regimen["stopped_date"] = None

            elif event_type == "supplement.regimen.paused":
                pause_start, invalid_start = _resolve_effective_day(
                    data,
                    fallback_day=local_day,
                    field_candidates=("start_date", "date"),
                )
                if invalid_start:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": invalid_start,
                            "value": data.get(invalid_start),
                            "message": "Invalid pause start date, falling back to event day",
                        }
                    )

                pause_until, invalid_until = _resolve_effective_day(
                    data,
                    fallback_day=pause_start,
                    field_candidates=("until_date",),
                )
                has_until = "until_date" in data
                if invalid_until and has_until:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": "until_date",
                            "value": data.get("until_date"),
                            "message": "Invalid until_date, ignoring explicit bound",
                        }
                    )
                    has_until = False

                duration_days = _to_positive_int(data.get("duration_days"))
                if data.get("duration_days") is not None and duration_days is None:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": "duration_days",
                            "value": data.get("duration_days"),
                            "message": "duration_days must be a positive integer",
                        }
                    )

                if not has_until and duration_days is not None:
                    pause_until = pause_start + timedelta(days=duration_days - 1)
                    has_until = True

                if has_until and pause_until < pause_start:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": "until_date",
                            "value": pause_until.isoformat(),
                            "message": "until_date earlier than start_date; clamping to start_date",
                        }
                    )
                    pause_until = pause_start

                _append_pause_window(
                    regimen,
                    start_day=pause_start,
                    until_day=pause_until if has_until else None,
                )
                regimen["stopped_date"] = None
                all_days.add(pause_start)
                if has_until:
                    all_days.add(pause_until)

            elif event_type == "supplement.regimen.resumed":
                effective_day, invalid_field = _resolve_effective_day(
                    data,
                    fallback_day=local_day,
                    field_candidates=("effective_date", "date"),
                )
                if invalid_field:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": invalid_field,
                            "value": data.get(invalid_field),
                            "message": "Invalid effective_date, falling back to event day",
                        }
                    )
                _truncate_pause_windows_from(regimen, effective_day)
                regimen["stopped_date"] = None
                if isinstance(regimen.get("start_date"), date) and effective_day < regimen["start_date"]:
                    regimen["start_date"] = effective_day
                all_days.add(effective_day)

            elif event_type == "supplement.regimen.stopped":
                effective_day, invalid_field = _resolve_effective_day(
                    data,
                    fallback_day=local_day,
                    field_candidates=("effective_date", "date"),
                )
                if invalid_field:
                    anomalies.append(
                        {
                            "event_id": str(row["id"]),
                            "field": invalid_field,
                            "value": data.get(invalid_field),
                            "message": "Invalid effective_date, falling back to event day",
                        }
                    )
                regimen["stopped_date"] = effective_day
                _truncate_pause_windows_from(regimen, effective_day)
                all_days.add(effective_day)
            continue

        effective_day, invalid_day = _resolve_effective_day(
            data,
            fallback_day=local_day,
            field_candidates=("date",),
        )
        if invalid_day:
            anomalies.append(
                {
                    "event_id": str(row["id"]),
                    "field": invalid_day,
                    "value": data.get(invalid_day),
                    "message": "Invalid ISO date, falling back to event day",
                }
            )
        all_days.add(effective_day)

        if event_type == "supplement.skipped":
            skipped_by_day[effective_day].add(name)
            continue

        if event_type == "supplement.logged":
            status = str(data.get("status") or "taken").strip().lower()
            if status == "skipped":
                skipped_by_day[effective_day].add(name)
                continue

        taken_by_day[effective_day].add(name)

    if not all_days:
        all_days.add(date.today())
    window_end = max(all_days)
    window_start = window_end - timedelta(days=_WINDOW_DAYS - 1)
    summary_start = window_end - timedelta(days=_SUMMARY_WINDOW_DAYS - 1)

    per_supplement = _init_per_supplement_stats(regimens, taken_by_day, skipped_by_day)
    _update_last_event_dates(
        per_supplement,
        taken_by_day=taken_by_day,
        skipped_by_day=skipped_by_day,
    )
    daily_status, summary = _build_daily_status(
        regimens=regimens,
        taken_by_day=taken_by_day,
        skipped_by_day=skipped_by_day,
        per_supplement=per_supplement,
        window_start=window_start,
        summary_start=summary_start,
    )
    regimen_catalog, active_stack = _build_regimen_views(regimens, window_end=window_end)
    per_supplement_rows = _build_per_supplement_rows(
        per_supplement=per_supplement,
        regimens=regimens,
        window_end=window_end,
    )

    total_taken = summary["taken_explicit_30d"] + summary["taken_assumed_30d"]
    adherence_summary = {
        "window_days": _SUMMARY_WINDOW_DAYS,
        "expected_30d": summary["expected_30d"],
        "taken_explicit_30d": summary["taken_explicit_30d"],
        "taken_assumed_30d": summary["taken_assumed_30d"],
        "skipped_30d": summary["skipped_30d"],
        "missing_30d": summary["missing_30d"],
        "paused_days_30d": summary["paused_days_30d"],
        "adherence_rate_30d": (
            round(total_taken / summary["expected_30d"], 3)
            if summary["expected_30d"] > 0
            else None
        ),
        "explicit_confirmation_rate_30d": (
            round(summary["taken_explicit_30d"] / total_taken, 3) if total_taken > 0 else None
        ),
    }

    projection_data = {
        "timezone_context": timezone_context,
        "window": {
            "from": window_start.isoformat(),
            "to": window_end.isoformat(),
            "days": _WINDOW_DAYS,
        },
        "active_stack": active_stack,
        "regimen_catalog": regimen_catalog,
        "daily_status": daily_status,
        "adherence_summary": adherence_summary,
        "per_supplement": per_supplement_rows,
        "recent_events": recent_events[-40:],
        "data_quality": {
            "anomalies": anomalies,
            "observed_attributes": observed_attr_counts,
            "temporal_conflicts": temporal_conflicts,
        },
    }

    last_event_id = str(rows[-1]["id"])
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'supplements', 'overview', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), last_event_id),
        )

    logger.info(
        "Updated supplements for user=%s (active=%d, regimens=%d, window=%s..%s)",
        user_id,
        len(active_stack),
        len(regimen_catalog),
        window_start.isoformat(),
        window_end.isoformat(),
    )
