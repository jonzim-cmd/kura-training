from __future__ import annotations

from datetime import date

from kura_workers.handlers.supplements import (
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
