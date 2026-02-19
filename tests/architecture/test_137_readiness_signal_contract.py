from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kura_workers.inference_event_registry import READINESS_SIGNAL_EVENT_TYPES
from kura_workers.readiness_signals import build_readiness_daily_scores


def _row(
    *,
    row_id: str,
    ts: datetime,
    event_type: str,
    data: dict,
    metadata: dict | None = None,
) -> dict:
    return {
        "id": row_id,
        "timestamp": ts,
        "event_type": event_type,
        "data": data,
        "metadata": metadata or {},
    }


def test_readiness_contract_consumes_external_imported_load_signals() -> None:
    assert "external.activity_imported" in READINESS_SIGNAL_EVENT_TYPES
    ts = datetime(2026, 2, 14, 8, 0, tzinfo=UTC)
    rows = [
        _row(
            row_id="ext-1",
            ts=ts,
            event_type="external.activity_imported",
            data={
                "workout": {
                    "duration_seconds": 1800,
                    "distance_meters": 5000,
                    "power_watt": 260,
                }
            },
        ),
        _row(row_id="sleep-1", ts=ts, event_type="sleep.logged", data={"duration_hours": 8}),
        _row(row_id="energy-1", ts=ts, event_type="energy.logged", data={"level": 7}),
        _row(row_id="sore-1", ts=ts, event_type="soreness.logged", data={"severity": 2}),
    ]
    built = build_readiness_daily_scores(rows, timezone_name="UTC")
    daily = list(built["daily_scores"])
    assert len(daily) == 1
    assert daily[0]["signals"]["load_score"] > 0


def test_readiness_contract_uses_modality_aware_intensity_for_load() -> None:
    ts = datetime(2026, 2, 14, 8, 0, tzinfo=UTC)

    def _load_for_power(power_watt: float) -> float:
        rows = [
            _row(
                row_id=f"ext-{int(power_watt)}",
                ts=ts,
                event_type="external.activity_imported",
                data={
                    "workout": {
                        "duration_seconds": 1800,
                        "distance_meters": 5000,
                        "power_watt": power_watt,
                    }
                },
            ),
            _row(row_id="sleep", ts=ts, event_type="sleep.logged", data={"duration_hours": 8}),
            _row(row_id="energy", ts=ts, event_type="energy.logged", data={"level": 7}),
            _row(row_id="sore", ts=ts, event_type="soreness.logged", data={"severity": 2}),
        ]
        built = build_readiness_daily_scores(rows, timezone_name="UTC")
        return float(built["daily_scores"][0]["signals"]["load_score"])

    low = _load_for_power(120)
    high = _load_for_power(320)
    assert high > low


def test_readiness_contract_tracks_missingness_instead_of_fixed_defaults() -> None:
    day1 = datetime(2026, 2, 12, 7, 0, tzinfo=UTC)
    day2 = day1 + timedelta(days=1)
    rows = [
        _row(row_id="sleep-1", ts=day1, event_type="sleep.logged", data={"duration_hours": 9}),
        _row(row_id="energy-1", ts=day1, event_type="energy.logged", data={"level": 8}),
        _row(row_id="sore-1", ts=day1, event_type="soreness.logged", data={"severity": 1}),
        _row(row_id="energy-2", ts=day2, event_type="energy.logged", data={"level": 7}),
        _row(row_id="sore-2", ts=day2, event_type="soreness.logged", data={"severity": 2}),
    ]

    built = build_readiness_daily_scores(rows, timezone_name="UTC")
    by_date = {entry["date"]: entry for entry in built["daily_scores"]}
    second_day = by_date["2026-02-13"]
    assert "sleep" in second_day["missing_signals"]
    assert second_day["signals"]["sleep_hours"] == 9.0
    assert built["missing_signal_counts"]["sleep"] == 1
