"""Tests for set.corrected chain overlay behavior (PDC.10)."""

from datetime import datetime

from kura_workers.set_corrections import apply_set_correction_chain


def _set_row(event_id: str, data: dict) -> dict:
    return {
        "id": event_id,
        "timestamp": datetime.fromisoformat("2026-02-11T10:00:00+00:00"),
        "data": data,
    }


def _correction_row(
    event_id: str,
    *,
    target_event_id: str,
    changed_fields: dict,
    timestamp: str,
    reason: str = "fix",
) -> dict:
    return {
        "id": event_id,
        "timestamp": datetime.fromisoformat(timestamp),
        "data": {
            "target_event_id": target_event_id,
            "changed_fields": changed_fields,
            "reason": reason,
            "repair_provenance": {
                "source_type": "inferred",
                "confidence": 0.9,
                "confidence_band": "high",
                "applies_scope": "single_set",
                "reason": "deterministic mapping",
            },
        },
    }


def test_additive_correction_adds_new_field():
    base_rows = [_set_row("set-1", {"exercise_id": "squat", "reps": 5})]
    corrections = [
        _correction_row(
            "corr-1",
            target_event_id="set-1",
            changed_fields={"rest_seconds": 90},
            timestamp="2026-02-11T10:01:00+00:00",
        )
    ]
    corrected = apply_set_correction_chain(base_rows, corrections)
    assert corrected[0]["effective_data"]["rest_seconds"] == 90
    assert corrected[0]["effective_data"]["reps"] == 5


def test_overwrite_correction_updates_existing_field():
    base_rows = [_set_row("set-1", {"exercise_id": "squat", "reps": 5})]
    corrections = [
        _correction_row(
            "corr-1",
            target_event_id="set-1",
            changed_fields={"reps": 6},
            timestamp="2026-02-11T10:01:00+00:00",
        )
    ]
    corrected = apply_set_correction_chain(base_rows, corrections)
    assert corrected[0]["effective_data"]["reps"] == 6


def test_chain_resolution_uses_latest_valid_correction():
    base_rows = [_set_row("set-1", {"exercise_id": "squat", "rest_seconds": 60})]
    corrections = [
        _correction_row(
            "corr-1",
            target_event_id="set-1",
            changed_fields={"rest_seconds": 90},
            timestamp="2026-02-11T10:01:00+00:00",
        ),
        _correction_row(
            "corr-2",
            target_event_id="set-1",
            changed_fields={"rest_seconds": 120},
            timestamp="2026-02-11T10:02:00+00:00",
        ),
    ]
    corrected = apply_set_correction_chain(base_rows, corrections)
    assert corrected[0]["effective_data"]["rest_seconds"] == 120
    assert len(corrected[0]["correction_history"]) == 2

