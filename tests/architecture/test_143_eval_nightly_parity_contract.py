from __future__ import annotations

from datetime import UTC, datetime

from kura_workers import eval_harness
from kura_workers.handlers import inference_nightly
from kura_workers.inference_event_registry import (
    CAUSAL_SIGNAL_EVENT_TYPES,
    EVAL_CAUSAL_EVENT_TYPES,
    EVAL_READINESS_EVENT_TYPES,
    NIGHTLY_REFIT_TRIGGER_EVENT_TYPES,
    READINESS_SIGNAL_EVENT_TYPES,
)


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


def test_eval_event_store_replay_parity_with_registry_sets() -> None:
    assert set(READINESS_SIGNAL_EVENT_TYPES) <= set(EVAL_READINESS_EVENT_TYPES)
    assert set(CAUSAL_SIGNAL_EVENT_TYPES) <= set(EVAL_CAUSAL_EVENT_TYPES)
    assert {"preference.set", "event.retracted"} <= set(EVAL_READINESS_EVENT_TYPES)
    assert {"preference.set", "event.retracted"} <= set(EVAL_CAUSAL_EVENT_TYPES)
    assert {"session.logged", "set.corrected", "external.activity_imported"} <= set(
        NIGHTLY_REFIT_TRIGGER_EVENT_TYPES
    )


def test_eval_readiness_replay_respects_timezone_preference_events() -> None:
    rows = [
        _row(
            row_id="pref-1",
            ts=datetime(2026, 2, 14, 20, 0, tzinfo=UTC),
            event_type="preference.set",
            data={"key": "timezone", "value": "Europe/Berlin"},
        ),
        _row(
            row_id="sleep-1",
            ts=datetime(2026, 2, 14, 23, 30, tzinfo=UTC),
            event_type="sleep.logged",
            data={"duration_hours": 8.0},
        ),
        _row(
            row_id="energy-1",
            ts=datetime(2026, 2, 14, 23, 35, tzinfo=UTC),
            event_type="energy.logged",
            data={"level": 7},
        ),
        _row(
            row_id="sore-1",
            ts=datetime(2026, 2, 14, 23, 40, tzinfo=UTC),
            event_type="soreness.logged",
            data={"severity": 2},
        ),
    ]
    daily = eval_harness.build_readiness_daily_scores_from_event_rows(rows)
    assert daily
    assert daily[0]["date"] == "2026-02-15"


def test_nightly_refit_uses_shared_trigger_registry() -> None:
    assert (
        inference_nightly.NIGHTLY_REFIT_TRIGGER_EVENT_TYPES
        is NIGHTLY_REFIT_TRIGGER_EVENT_TYPES
    )
