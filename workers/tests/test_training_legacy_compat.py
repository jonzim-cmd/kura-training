from __future__ import annotations

from datetime import UTC, datetime

from kura_workers.training_legacy_compat import (
    build_set_to_session_backfill_plan,
    extract_backfilled_set_event_ids,
    legacy_backfill_idempotency_key,
    session_logged_to_legacy_set_rows,
    set_logged_to_session_logged_payload,
)
from kura_workers.training_session_contract import validate_session_logged_payload


def test_set_logged_to_session_payload_is_contract_valid_without_hr() -> None:
    payload = set_logged_to_session_logged_payload(
        set_data={
            "exercise_id": "barbell_back_squat",
            "reps": 5,
            "weight_kg": 100,
            "rest_seconds": 120,
        },
        set_timestamp=datetime(2026, 2, 14, 10, 0, tzinfo=UTC),
        session_id="legacy-session-1",
        timezone="Europe/Berlin",
    )

    model = validate_session_logged_payload(payload)
    assert model.blocks[0].block_type == "strength_set"
    assert payload["blocks"][0]["metrics"]["weight_kg"]["measurement_state"] == "measured"
    assert payload["blocks"][0]["intensity_anchors_status"] == "not_applicable"


def test_session_payload_can_expand_back_to_legacy_set_rows() -> None:
    payload = set_logged_to_session_logged_payload(
        set_data={"exercise": "bench press", "reps": 8, "weight_kg": 75, "rpe": 8},
        set_timestamp=datetime(2026, 2, 14, 12, 0, tzinfo=UTC),
    )

    rows = session_logged_to_legacy_set_rows(payload)
    assert len(rows) == 1
    assert rows[0]["exercise_id"] == "strength_set"
    assert rows[0]["reps"] == 8
    assert rows[0]["weight_kg"] == 75
    assert rows[0]["rpe"] == 8


def test_backfill_plan_skips_already_backfilled_rows_and_is_stable() -> None:
    set_events = [
        {
            "id": "set-1",
            "timestamp": datetime(2026, 2, 14, 9, 0, tzinfo=UTC),
            "data": {"exercise_id": "barbell_back_squat", "reps": 5, "weight_kg": 100},
            "metadata": {"session_id": "s1"},
        },
        {
            "id": "set-2",
            "timestamp": datetime(2026, 2, 14, 9, 5, tzinfo=UTC),
            "data": {"exercise_id": "barbell_bench_press", "reps": 5, "weight_kg": 80},
            "metadata": {"session_id": "s1"},
        },
    ]

    plan = build_set_to_session_backfill_plan(
        set_events=set_events,
        already_backfilled_set_event_ids={"set-1"},
    )

    assert len(plan) == 1
    assert plan[0]["source_event_id"] == "set-2"
    assert plan[0]["metadata"]["compat_mode"] == "legacy_set_backfill"
    assert plan[0]["metadata"]["legacy_event_id"] == "set-2"
    assert plan[0]["metadata"]["idempotency_key"] == legacy_backfill_idempotency_key("set-2")


def test_extract_backfilled_set_event_ids_filters_on_compat_mode() -> None:
    rows = [
        {"metadata": {"compat_mode": "legacy_set_backfill", "legacy_event_id": "set-1"}},
        {"metadata": {"compat_mode": "manual", "legacy_event_id": "set-2"}},
        {"metadata": {"compat_mode": "legacy_set_backfill"}},
    ]
    assert extract_backfilled_set_event_ids(rows) == {"set-1"}
