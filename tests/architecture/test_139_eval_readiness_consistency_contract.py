from __future__ import annotations

from datetime import UTC, datetime

from kura_workers import eval_harness


def test_eval_contract_uses_shared_readiness_signal_builder(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_build(rows, *, timezone_name):  # type: ignore[no-untyped-def]
        captured["rows"] = rows
        captured["timezone_name"] = timezone_name
        return {"daily_scores": [{"date": "2026-02-14", "score": 0.5}]}

    monkeypatch.setattr(eval_harness, "build_readiness_daily_scores", _fake_build)
    rows = [
        {
            "id": "pref-1",
            "timestamp": datetime(2026, 2, 14, 8, 0, tzinfo=UTC),
            "event_type": "preference.set",
            "data": {"key": "timezone", "value": "Europe/Berlin"},
            "metadata": {},
        }
    ]
    result = eval_harness.build_readiness_daily_scores_from_event_rows(rows)
    assert result == [{"date": "2026-02-14", "score": 0.5}]
    assert captured["rows"] is rows
    assert captured["timezone_name"] == "Europe/Berlin"


def test_eval_contract_replays_readiness_with_calendar_day_offsets(monkeypatch) -> None:
    captured_offsets: list[float] = []

    def _fake_run(
        observations, *, day_offsets=None, observation_variances=None, population_prior=None
    ):  # type: ignore[no-untyped-def]
        del observations, observation_variances, population_prior
        if day_offsets is not None:
            captured_offsets[:] = list(day_offsets)
        return {
            "status": "ok",
            "readiness_today": {"mean": 0.6, "ci95": [0.5, 0.7], "state": "moderate"},
            "dynamics": {"velocity_per_day": 0.01, "direction": "up"},
        }

    monkeypatch.setattr(eval_harness, "run_readiness_inference", _fake_run)
    daily_scores = [
        {"date": "2026-02-10", "score": 0.45, "observation_variance": 0.01},
        {"date": "2026-02-11", "score": 0.5, "observation_variance": 0.01},
        {"date": "2026-02-13", "score": 0.54, "observation_variance": 0.01},
        {"date": "2026-02-14", "score": 0.58, "observation_variance": 0.01},
        {"date": "2026-02-16", "score": 0.61, "observation_variance": 0.01},
    ]

    result = eval_harness.evaluate_readiness_daily_scores("overview", daily_scores)
    assert result["status"] == "ok"
    assert captured_offsets == [0.0, 1.0, 3.0, 4.0, 6.0]
