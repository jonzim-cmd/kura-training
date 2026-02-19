from __future__ import annotations

from datetime import datetime, timezone

from kura_workers.readiness_signals import build_readiness_daily_scores


def _row(event_type: str, data: dict, *, timestamp: str) -> dict:
    return {
        "event_type": event_type,
        "timestamp": datetime.fromisoformat(timestamp).astimezone(timezone.utc),
        "data": data,
        "metadata": {},
    }


def test_external_imported_endurance_load_contributes_to_readiness_load_score() -> None:
    rows = [
        _row(
            "external.activity_imported",
            {
                "workout": {
                    "duration_seconds": 3600,
                    "distance_meters": 12000,
                },
                "sets": [],
            },
            timestamp="2026-02-15T07:00:00+00:00",
        ),
        _row(
            "sleep.logged",
            {"duration_hours": 7.2},
            timestamp="2026-02-15T08:00:00+00:00",
        ),
    ]

    result = build_readiness_daily_scores(rows, timezone_name="UTC")
    daily = result["daily_scores"]
    assert len(daily) == 1
    assert daily[0]["signals"]["load_score"] > 0.0
    assert daily[0]["components"]["load_penalty"] > 0.0


def test_missing_signals_are_explicitly_exposed_with_uncertainty_metadata() -> None:
    rows = [
        _row(
            "energy.logged",
            {"level": 6.0},
            timestamp="2026-02-12T08:00:00+00:00",
        )
    ]

    result = build_readiness_daily_scores(rows, timezone_name="UTC")
    day = result["daily_scores"][0]
    assert set(day["missing_signals"]) == {"sleep", "soreness"}
    assert day["observation_weight"] < 1.0
    assert day["observation_variance"] > 0.01


def test_day_offsets_and_gap_days_follow_calendar_distance() -> None:
    rows = [
        _row(
            "sleep.logged",
            {"duration_hours": 7.0},
            timestamp="2026-02-01T08:00:00+00:00",
        ),
        _row(
            "sleep.logged",
            {"duration_hours": 7.1},
            timestamp="2026-02-04T08:00:00+00:00",
        ),
    ]

    result = build_readiness_daily_scores(rows, timezone_name="UTC")
    daily = result["daily_scores"]
    assert [entry["day_offset"] for entry in daily] == [0, 3]
    assert [entry["gap_days"] for entry in daily] == [1, 3]


def test_readiness_load_uses_relative_intensity_and_stale_reference_fallback() -> None:
    rows = [
        _row(
            "external.activity_imported",
            {
                "workout": {
                    "duration_seconds": 1800,
                    "distance_meters": 5000,
                    "session_rpe": 5,
                    "relative_intensity": {
                        "value_pct": 95.0,
                        "reference_type": "critical_speed",
                        "reference_value": 4.3,
                        "reference_measured_at": "2026-02-14T08:00:00+00:00",
                        "reference_confidence": 0.82,
                    },
                },
                "sets": [],
            },
            timestamp="2026-02-15T07:00:00+00:00",
        ),
        _row(
            "external.activity_imported",
            {
                "workout": {
                    "duration_seconds": 1800,
                    "distance_meters": 5000,
                    "session_rpe": 5,
                    "relative_intensity": {
                        "value_pct": 95.0,
                        "reference_type": "critical_speed",
                        "reference_value": 4.3,
                        "reference_measured_at": "2020-01-01T08:00:00+00:00",
                        "reference_confidence": 0.82,
                    },
                },
                "sets": [],
            },
            timestamp="2026-02-16T07:00:00+00:00",
        ),
    ]

    result = build_readiness_daily_scores(rows, timezone_name="UTC")
    daily = result["daily_scores"]
    assert len(daily) == 2
    assert daily[0]["signals"]["load_score"] > daily[1]["signals"]["load_score"]
