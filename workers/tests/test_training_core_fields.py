"""Tests for training core-field registry and mention-bound logic (PDC.7)."""

from datetime import datetime, timezone

from kura_workers.training_core_fields import (
    core_field_registry,
    evaluate_set_context_rows,
    extract_set_context_mentions,
)


def _row(
    event_id: str,
    *,
    data: dict,
    session_id: str = "s1",
    timestamp: str = "2026-02-11T10:00:00+00:00",
) -> dict:
    return {
        "id": event_id,
        "timestamp": datetime.fromisoformat(timestamp),
        "data": data,
        "metadata": {"session_id": session_id},
    }


def test_core_field_registry_contains_modality_classes():
    registry = core_field_registry()
    assert "strength" in registry
    assert "mention_bound" in registry["strength"]
    assert "rest_seconds" in registry["strength"]["mention_bound"]


def test_extract_set_context_mentions_is_deterministic():
    text = "Pause 90 sec, Tempo 3-1-1-0, RIR 2, warmup"
    mentions = extract_set_context_mentions(text)
    assert mentions["rest_seconds"] == 90.0
    assert mentions["tempo"] == "3-1-1-0"
    assert mentions["rir"] == 2.0
    assert mentions["set_type"] == "warmup"


def test_extract_set_context_mentions_supports_mmss_and_minutes():
    mmss = extract_set_context_mentions("rest 1:30 before next set")
    assert mmss["rest_seconds"] == 90.0

    minutes = extract_set_context_mentions("pause 2 min")
    assert minutes["rest_seconds"] == 120.0


def test_session_defaults_apply_until_override_within_session_exercise_scope():
    rows = [
        _row(
            "e1",
            data={
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "notes": "pause 90 sec",
            },
        ),
        _row(
            "e2",
            data={"exercise_id": "barbell_back_squat", "reps": 5},
            timestamp="2026-02-11T10:02:00+00:00",
        ),
        _row(
            "e3",
            data={
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "rest_seconds": 120,
            },
            timestamp="2026-02-11T10:04:00+00:00",
        ),
        _row(
            "e4",
            data={"exercise_id": "barbell_bench_press", "reps": 5},
            timestamp="2026-02-11T10:06:00+00:00",
        ),
    ]

    evaluated = evaluate_set_context_rows(rows)
    by_id = {item["event_id"]: item for item in evaluated}

    assert "rest_seconds" in by_id["e1"]["missing_fields"]
    assert "rest_seconds" in by_id["e2"]["missing_fields"]
    assert by_id["e3"]["missing_fields"] == []
    assert by_id["e4"]["missing_fields"] == []


def test_absent_mentions_do_not_fabricate_optional_fields():
    rows = [
        {
            "id": "e1",
            "timestamp": datetime(2026, 2, 11, 10, 0, 0, tzinfo=timezone.utc),
            "data": {"exercise_id": "barbell_back_squat", "reps": 5},
            "metadata": {"session_id": "s1"},
        }
    ]
    evaluated = evaluate_set_context_rows(rows)
    assert evaluated[0]["mentioned_fields"] == {}
    assert evaluated[0]["effective_defaults"] == {}
    assert evaluated[0]["missing_fields"] == []

