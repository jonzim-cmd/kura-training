"""Shared readiness signal reconstruction utilities.

This module centralizes day-level readiness signal construction so production
handlers and evaluation tooling use identical preprocessing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from .recovery_daily_checkin import normalize_daily_checkin_payload
from .training_load_calibration_v1 import (
    active_calibration_version,
    calibration_profile_for_version,
    compute_row_load_components_v2,
)
from .training_signal_normalization import normalize_training_signal_rows
from .utils import normalize_temporal_point

_CHECKIN_TRAINING_LOAD_SCORE: dict[str, float] = {
    "rest": 0.0,
    "easy": 0.45,
    "average": 0.9,
    "hard": 1.35,
}


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _iter_event_load_rows(event_type: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    if event_type in {"set.logged", "session.logged"}:
        return [data]

    if event_type != "external.activity_imported":
        return []

    sets_payload = data.get("sets")
    rows: list[dict[str, Any]] = []
    workout_payload = data.get("workout")
    if not isinstance(workout_payload, dict):
        workout_payload = {}

    workout_fallback: dict[str, Any] = {}
    for key in (
        "duration_seconds",
        "distance_meters",
        "contacts",
        "heart_rate_avg",
        "heart_rate_max",
        "power_watt",
        "pace_min_per_km",
        "session_rpe",
        "rpe",
    ):
        value = _as_float(workout_payload.get(key))
        if value is not None and value > 0.0:
            workout_fallback[key] = value
    relative_intensity = workout_payload.get("relative_intensity")
    if isinstance(relative_intensity, dict):
        value_pct = _as_float(relative_intensity.get("value_pct"))
        if value_pct is not None and value_pct > 0.0:
            workout_fallback["relative_intensity"] = dict(relative_intensity)

    if isinstance(sets_payload, list):
        for entry in sets_payload:
            if isinstance(entry, dict):
                merged = dict(entry)
                for key, value in workout_fallback.items():
                    merged.setdefault(key, value)
                rows.append(merged)
    if rows:
        return rows

    fallback: dict[str, Any] = {}
    for key, value in workout_fallback.items():
        fallback[key] = value

    return [fallback] if fallback else []


def build_readiness_daily_scores(
    rows: list[dict[str, Any]],
    *,
    timezone_name: str,
) -> dict[str, Any]:
    """Build day-level readiness scores and uncertainty metadata.

    Returns a dict with:
    - daily_scores: day-level score rows
    - temporal_conflicts: conflict counters from temporal normalization
    - component_priors: user-level priors used for missing components
    - load_baseline: normalization baseline for load penalty
    - missing_signal_counts: aggregate missing counters
    """
    per_day: dict[date, dict[str, Any]] = defaultdict(
        lambda: {
            "sleep_sum": 0.0,
            "sleep_entries": 0,
            "energy_sum": 0.0,
            "energy_entries": 0,
            "soreness_sum": 0.0,
            "soreness_entries": 0,
            "load_score": 0.0,
            "load_entries": 0,
        }
    )
    temporal_conflicts: dict[str, int] = {}

    profile = calibration_profile_for_version(active_calibration_version())

    normalized_rows = normalize_training_signal_rows(rows)
    for row in normalized_rows:
        event_type = str(row.get("event_type") or "").strip()
        if event_type not in {
            "set.logged",
            "session.logged",
            "sleep.logged",
            "soreness.logged",
            "energy.logged",
            "recovery.daily_checkin",
            "external.activity_imported",
        }:
            continue

        timestamp = row.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue

        data = row.get("data")
        if not isinstance(data, dict):
            data = {}
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        temporal = normalize_temporal_point(
            timestamp,
            timezone_name=timezone_name,
            data=data,
            metadata=metadata,
        )
        local_day = temporal.local_date
        for conflict in temporal.conflicts:
            temporal_conflicts[conflict] = temporal_conflicts.get(conflict, 0) + 1

        bucket = per_day[local_day]

        if event_type == "sleep.logged":
            duration = _as_float(data.get("duration_hours"))
            if duration is not None and duration > 0.0:
                bucket["sleep_sum"] += duration
                bucket["sleep_entries"] += 1
        elif event_type == "energy.logged":
            level = _as_float(data.get("level"))
            if level is not None and level > 0.0:
                bucket["energy_sum"] += level
                bucket["energy_entries"] += 1
        elif event_type == "soreness.logged":
            severity = _as_float(data.get("severity"))
            if severity is not None and severity >= 0.0:
                bucket["soreness_sum"] += severity
                bucket["soreness_entries"] += 1
        elif event_type == "recovery.daily_checkin":
            normalized = normalize_daily_checkin_payload(data if isinstance(data, dict) else {})

            sleep_hours = _as_float(normalized.get("sleep_hours"))
            if sleep_hours is not None and sleep_hours > 0.0:
                bucket["sleep_sum"] += sleep_hours
                bucket["sleep_entries"] += 1

            motivation = _as_float(normalized.get("motivation"))
            if motivation is not None and motivation > 0.0:
                bucket["energy_sum"] += motivation
                bucket["energy_entries"] += 1

            soreness = _as_float(normalized.get("soreness"))
            if soreness is not None and soreness >= 0.0:
                bucket["soreness_sum"] += soreness
                bucket["soreness_entries"] += 1

            training_tag = normalized.get("training_yesterday")
            if isinstance(training_tag, str):
                mapped_load = _CHECKIN_TRAINING_LOAD_SCORE.get(training_tag.strip().lower())
                if mapped_load is not None and mapped_load > 0.0:
                    bucket["load_score"] += mapped_load
                    bucket["load_entries"] += 1

        for load_row in _iter_event_load_rows(event_type, data):
            components = compute_row_load_components_v2(data=load_row, profile=profile)
            load_score = max(0.0, float(components.get("load_score", 0.0) or 0.0))
            if load_score > 0.0:
                bucket["load_score"] += load_score
                bucket["load_entries"] += 1

    if not per_day:
        return {
            "daily_scores": [],
            "temporal_conflicts": temporal_conflicts,
            "component_priors": {
                "sleep_hours": 7.0,
                "energy_level": 6.0,
                "soreness_level": 2.0,
            },
            "load_baseline": 1.0,
            "missing_signal_counts": {"sleep": 0, "energy": 0, "soreness": 0},
        }

    daily_buckets = sorted(per_day.items(), key=lambda item: item[0])

    sleep_values = [
        bucket["sleep_sum"] / bucket["sleep_entries"]
        for _, bucket in daily_buckets
        if bucket["sleep_entries"] > 0
    ]
    energy_values = [
        bucket["energy_sum"] / bucket["energy_entries"]
        for _, bucket in daily_buckets
        if bucket["energy_entries"] > 0
    ]
    soreness_values = [
        bucket["soreness_sum"] / bucket["soreness_entries"]
        for _, bucket in daily_buckets
        if bucket["soreness_entries"] > 0
    ]

    sleep_prior = _median(sleep_values) if sleep_values else 7.0
    energy_prior = _median(energy_values) if energy_values else 6.0
    soreness_prior = _median(soreness_values) if soreness_values else 2.0

    load_values = [
        float(bucket["load_score"])
        for _, bucket in daily_buckets
        if float(bucket["load_score"]) > 0.0
    ]
    load_baseline = max(1.0, _median(load_values))

    daily_scores: list[dict[str, Any]] = []
    missing_signal_counts = {"sleep": 0, "energy": 0, "soreness": 0}
    first_day = daily_buckets[0][0]
    previous_day: date | None = None

    for local_day, bucket in daily_buckets:
        sleep_missing = bucket["sleep_entries"] == 0
        energy_missing = bucket["energy_entries"] == 0
        soreness_missing = bucket["soreness_entries"] == 0

        sleep_hours = (
            (bucket["sleep_sum"] / bucket["sleep_entries"])
            if not sleep_missing
            else sleep_prior
        )
        energy_level = (
            (bucket["energy_sum"] / bucket["energy_entries"])
            if not energy_missing
            else energy_prior
        )
        soreness_level = (
            (bucket["soreness_sum"] / bucket["soreness_entries"])
            if not soreness_missing
            else soreness_prior
        )

        missing_signals: list[str] = []
        if sleep_missing:
            missing_signals.append("sleep")
            missing_signal_counts["sleep"] += 1
        if energy_missing:
            missing_signals.append("energy")
            missing_signal_counts["energy"] += 1
        if soreness_missing:
            missing_signals.append("soreness")
            missing_signal_counts["soreness"] += 1

        sleep_score = _clamp(float(sleep_hours) / 8.0, 0.0, 1.2)
        energy_score = _clamp(float(energy_level) / 10.0, 0.0, 1.0)
        soreness_penalty = _clamp(float(soreness_level) / 10.0, 0.0, 1.0)
        load_penalty = _clamp(float(bucket["load_score"]) / load_baseline, 0.0, 1.4)

        score = (
            (0.45 * sleep_score)
            + (0.35 * energy_score)
            - (0.20 * soreness_penalty)
            - (0.15 * load_penalty)
            + 0.25
        )
        score = _clamp(score, 0.0, 1.0)

        missing_fraction = len(missing_signals) / 3.0
        day_offset = (local_day - first_day).days
        gap_days = (
            (local_day - previous_day).days
            if previous_day is not None
            else 1
        )
        observation_weight = max(0.35, 1.0 - (0.55 * missing_fraction))
        observation_variance = 0.01 * (
            1.0 + (0.9 * missing_fraction) + (0.1 * max(0, gap_days - 1))
        )

        daily_scores.append(
            {
                "date": local_day.isoformat(),
                "day_offset": day_offset,
                "gap_days": gap_days,
                "score": round(score, 3),
                "observation_weight": round(observation_weight, 3),
                "observation_variance": round(observation_variance, 6),
                "missing_signals": missing_signals,
                "components": {
                    "sleep": round(sleep_score, 3),
                    "energy": round(energy_score, 3),
                    "soreness_penalty": round(soreness_penalty, 3),
                    "load_penalty": round(load_penalty, 3),
                },
                "signals": {
                    "sleep_hours": round(float(sleep_hours), 2),
                    "energy_level": round(float(energy_level), 2),
                    "soreness_level": round(float(soreness_level), 2),
                    "load_score": round(float(bucket["load_score"]), 3),
                },
            }
        )
        previous_day = local_day

    return {
        "daily_scores": daily_scores,
        "temporal_conflicts": temporal_conflicts,
        "component_priors": {
            "sleep_hours": round(float(sleep_prior), 3),
            "energy_level": round(float(energy_prior), 3),
            "soreness_level": round(float(soreness_prior), 3),
        },
        "load_baseline": round(float(load_baseline), 3),
        "missing_signal_counts": missing_signal_counts,
    }
