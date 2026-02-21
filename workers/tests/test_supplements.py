from __future__ import annotations

from datetime import date

from kura_workers.handlers.supplements import (
    _build_daily_status,
    _manifest_contribution,
    _normalize_days_of_week,
    _regimen_state_on_day,
    _truncate_pause_windows_from,
)


def test_manifest_contribution_exposes_active_count_and_adherence() -> None:
    rows = [
        {
            "data": {
                "active_stack": [{"name": "creatine"}, {"name": "magnesium"}],
                "adherence_summary": {"adherence_rate_30d": 0.93},
            }
        }
    ]
    result = _manifest_contribution(rows)
    assert result["active_regimens"] == 2
    assert result["adherence_rate_30d"] == 0.93


def test_normalize_days_of_week_accepts_tokens_and_reports_invalid() -> None:
    days, invalid = _normalize_days_of_week("mon, wed, foo")
    assert days == [0, 2]
    assert invalid == ["foo"]


def test_regimen_state_on_day_respects_pause_and_stop_windows() -> None:
    regimen = {
        "start_date": date(2026, 2, 1),
        "pause_start": date(2026, 2, 5),
        "pause_until": date(2026, 2, 7),
        "stopped_date": date(2026, 2, 10),
    }
    assert _regimen_state_on_day(regimen, date(2026, 2, 4)) == "active"
    assert _regimen_state_on_day(regimen, date(2026, 2, 6)) == "paused"
    assert _regimen_state_on_day(regimen, date(2026, 2, 11)) == "stopped"


def test_resume_truncation_preserves_historical_pause_window() -> None:
    regimen = {
        "start_date": date(2026, 2, 1),
        "pause_windows": [{"start": date(2026, 2, 5), "until": date(2026, 2, 7)}],
        "pause_start": date(2026, 2, 5),
        "pause_until": date(2026, 2, 7),
        "stopped_date": None,
    }
    _truncate_pause_windows_from(regimen, date(2026, 2, 8))

    assert _regimen_state_on_day(regimen, date(2026, 2, 6)) == "paused"
    assert _regimen_state_on_day(regimen, date(2026, 2, 8)) == "active"


def test_daily_status_summary_counts_expected_and_missing() -> None:
    regimens = {
        "creatine": {
            "display_name": "Creatine",
            "cadence": "daily",
            "times_per_day": 1,
            "days_of_week": None,
            "dose_amount": 5,
            "dose_unit": "g",
            "start_date": date(2026, 2, 1),
            "assume_taken_by_default": False,
            "pause_windows": [],
            "pause_start": None,
            "pause_until": None,
            "stopped_date": None,
            "notes": None,
        }
    }
    per_supplement = {
        "creatine": {
            "name": "creatine",
            "display_name": "Creatine",
            "expected_30d": 0,
            "taken_explicit_30d": 0,
            "taken_assumed_30d": 0,
            "skipped_30d": 0,
            "missing_30d": 0,
            "paused_days_30d": 0,
            "last_taken_date": None,
            "last_skipped_date": None,
        }
    }
    daily_status, summary = _build_daily_status(
        regimens=regimens,
        taken_by_day={date(2026, 2, 3): {"creatine"}},
        skipped_by_day={},
        per_supplement=per_supplement,
        window_start=date(2026, 2, 1),
        summary_start=date(2026, 2, 1),
    )
    row_by_day = {row["date"]: row for row in daily_status}
    assert "creatine" in row_by_day["2026-02-03"]["taken_explicit"]
    assert "creatine" in row_by_day["2026-02-04"]["missing"]
    assert summary["expected_30d"] == 60
    assert summary["taken_explicit_30d"] == 1
