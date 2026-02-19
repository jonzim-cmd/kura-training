from __future__ import annotations

from datetime import UTC, datetime

from kura_workers import eval_harness, readiness_signals
from kura_workers.handlers import causal_inference, strength_inference
from kura_workers.training_signal_normalization import normalize_training_signal_rows


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


def test_shared_training_signal_normalization_applies_corrections_and_expansion() -> None:
    ts = datetime(2026, 2, 14, 8, 0, tzinfo=UTC)
    rows = [
        _row(
            row_id="set-1",
            ts=ts,
            event_type="set.logged",
            data={"exercise_id": "squat", "reps": 5, "weight_kg": 100},
        ),
        _row(
            row_id="corr-1",
            ts=ts,
            event_type="set.corrected",
            data={
                "target_event_id": "set-1",
                "changed_fields": {"weight_kg": {"value": 120}},
            },
        ),
        _row(
            row_id="legacy-set-1",
            ts=ts,
            event_type="set.logged",
            data={"exercise_id": "bench", "reps": 5, "weight_kg": 90},
        ),
        _row(
            row_id="session-legacy-1",
            ts=ts,
            event_type="session.logged",
            data={
                "blocks": [
                    {
                        "block_type": "strength_set",
                        "dose": {"work": {"reps": 5}, "repeats": 1},
                        "metrics": {"weight_kg": {"measurement_state": "measured", "value": 80}},
                    }
                ]
            },
            metadata={"compat_mode": "legacy_set_backfill", "legacy_event_id": "legacy-set-1"},
        ),
        _row(
            row_id="session-1",
            ts=ts,
            event_type="session.logged",
            data={
                "blocks": [
                    {
                        "block_type": "interval_endurance",
                        "dose": {
                            "work": {"duration_seconds": 600, "distance_meters": 2000},
                            "repeats": 1,
                        },
                        "metrics": {},
                    }
                ]
            },
        ),
        _row(
            row_id="sleep-1",
            ts=ts,
            event_type="sleep.logged",
            data={"duration_hours": 8},
        ),
    ]
    normalized = normalize_training_signal_rows(rows)

    set_rows = [row for row in normalized if row.get("event_type") == "set.logged"]
    session_rows = [row for row in normalized if row.get("event_type") == "session.logged"]
    assert any(row["id"] == "set-1" and row["data"]["weight_kg"] == 120 for row in set_rows)
    assert not any(row["id"] == "legacy-set-1" for row in set_rows)
    assert any(row["data"].get("block_type") == "interval_endurance" for row in session_rows)
    assert any(row.get("event_type") == "sleep.logged" for row in normalized)


def test_inference_paths_bind_the_same_shared_normalizer() -> None:
    assert readiness_signals.normalize_training_signal_rows is normalize_training_signal_rows
    assert causal_inference.normalize_training_signal_rows is normalize_training_signal_rows
    assert strength_inference.normalize_training_signal_rows is normalize_training_signal_rows
    assert eval_harness.normalize_training_signal_rows is normalize_training_signal_rows
