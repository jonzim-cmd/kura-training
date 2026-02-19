from __future__ import annotations

from datetime import UTC, datetime

from kura_workers.handlers import strength_inference  # noqa: F401
from kura_workers.inference_event_registry import (
    CAUSAL_SIGNAL_EVENT_TYPES,
    READINESS_SIGNAL_EVENT_TYPES,
)
from kura_workers.readiness_signals import build_readiness_daily_scores
from kura_workers.registry import get_dimension_metadata


def _row(
    *,
    row_id: str,
    ts: datetime,
    event_type: str,
    data: dict,
) -> dict:
    return {
        "id": row_id,
        "timestamp": ts,
        "event_type": event_type,
        "data": data,
        "metadata": {},
    }


def test_readiness_and_causal_contracts_cover_session_correction_and_alias_events() -> None:
    assert "session.logged" in READINESS_SIGNAL_EVENT_TYPES
    assert "set.corrected" in READINESS_SIGNAL_EVENT_TYPES

    assert "session.logged" in CAUSAL_SIGNAL_EVENT_TYPES
    assert "set.corrected" in CAUSAL_SIGNAL_EVENT_TYPES
    assert "exercise.alias_created" in CAUSAL_SIGNAL_EVENT_TYPES


def test_strength_contract_includes_session_and_correction_signal_paths() -> None:
    metadata = get_dimension_metadata()
    strength_events = set(metadata["strength_inference"]["event_types"])
    assert {"set.logged", "session.logged", "set.corrected"} <= strength_events


def test_readiness_signal_builder_applies_set_corrections_before_scoring() -> None:
    ts = datetime(2026, 2, 14, 8, 0, tzinfo=UTC)
    base_rows = [
        _row(
            row_id="set-1",
            ts=ts,
            event_type="set.logged",
            data={"exercise": "squat", "exercise_id": "squat", "reps": 5, "weight_kg": 100},
        ),
        _row(row_id="sleep", ts=ts, event_type="sleep.logged", data={"duration_hours": 8}),
        _row(row_id="energy", ts=ts, event_type="energy.logged", data={"level": 7}),
        _row(row_id="sore", ts=ts, event_type="soreness.logged", data={"severity": 2}),
    ]
    corrected_rows = [
        *base_rows,
        _row(
            row_id="corr-1",
            ts=ts,
            event_type="set.corrected",
            data={
                "target_event_id": "set-1",
                "changed_fields": {"weight_kg": {"value": 40}},
            },
        ),
    ]

    baseline = build_readiness_daily_scores(base_rows, timezone_name="UTC")
    corrected = build_readiness_daily_scores(corrected_rows, timezone_name="UTC")
    baseline_load = float(baseline["daily_scores"][0]["signals"]["load_score"])
    corrected_load = float(corrected["daily_scores"][0]["signals"]["load_score"])
    assert corrected_load < baseline_load
