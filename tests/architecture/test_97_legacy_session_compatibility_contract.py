from __future__ import annotations

from datetime import UTC, datetime

from kura_workers.system_config import _get_conventions
from kura_workers.training_legacy_compat import (
    build_set_to_session_backfill_plan,
    extract_backfilled_set_event_ids,
    set_logged_to_session_logged_payload,
)
from kura_workers.training_session_contract import validate_session_logged_payload


def test_legacy_compatibility_contract_is_declared() -> None:
    conventions = _get_conventions()
    compat = conventions["session_legacy_compatibility_v1"]
    contract = compat["contract"]
    assert contract["migration_strategy"]["mode"] == "append_only_backfill"
    assert contract["migration_strategy"]["replay_safe"] is True
    assert contract["coexistence_policy"]["allow_parallel_event_types"] is True


def test_set_logged_to_session_logged_adapter_is_contract_valid() -> None:
    payload = set_logged_to_session_logged_payload(
        set_data={"exercise_id": "barbell_back_squat", "reps": 5, "weight_kg": 100},
        set_timestamp=datetime(2026, 2, 14, 10, 0, tzinfo=UTC),
        session_id="legacy-s1",
    )
    model = validate_session_logged_payload(payload)
    assert model.blocks[0].block_type == "strength_set"


def test_backfill_plan_is_idempotent_and_supports_dedup_markers() -> None:
    set_events = [
        {
            "id": "set-1",
            "timestamp": datetime(2026, 2, 14, 9, 0, tzinfo=UTC),
            "data": {"exercise_id": "barbell_back_squat", "reps": 5, "weight_kg": 100},
            "metadata": {},
        },
        {
            "id": "set-2",
            "timestamp": datetime(2026, 2, 14, 9, 10, tzinfo=UTC),
            "data": {"exercise_id": "barbell_bench_press", "reps": 5, "weight_kg": 80},
            "metadata": {},
        },
    ]
    plan = build_set_to_session_backfill_plan(
        set_events=set_events,
        already_backfilled_set_event_ids={"set-1"},
    )
    assert len(plan) == 1
    assert plan[0]["source_event_id"] == "set-2"
    assert plan[0]["metadata"]["compat_mode"] == "legacy_set_backfill"

    dedup_ids = extract_backfilled_set_event_ids(
        [{"metadata": plan[0]["metadata"]}]
    )
    assert dedup_ids == {"set-2"}
