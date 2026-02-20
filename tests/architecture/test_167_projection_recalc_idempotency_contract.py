from __future__ import annotations

from pathlib import Path

from kura_workers.handlers.objective_state import _objective_from_legacy_goal

INFERENCE_NIGHTLY = Path("workers/src/kura_workers/handlers/inference_nightly.py")
OBJECTIVE_STATE = Path("workers/src/kura_workers/handlers/objective_state.py")


def test_legacy_goal_to_objective_mapping_is_deterministic() -> None:
    payload = {
        "goal_type": "strength",
        "target_exercise": "barbell_back_squat",
        "target_1rm_kg": 180,
        "timeframe_weeks": 16,
    }
    first = _objective_from_legacy_goal(payload)
    second = _objective_from_legacy_goal(payload)
    assert first == second


def test_projection_update_backfill_queue_uses_dedup_contract() -> None:
    src = INFERENCE_NIGHTLY.read_text(encoding="utf-8")
    assert "WHERE NOT EXISTS" in src
    assert "status IN ('pending', 'processing')" in src
    assert "payload->>'source'" in src
    assert "payload->>'event_type'" in src


def test_objective_state_recompute_reads_ordered_event_stream_and_upserts_projection() -> None:
    src = OBJECTIVE_STATE.read_text(encoding="utf-8")
    assert "ORDER BY timestamp ASC, id ASC" in src
    assert "ON CONFLICT (user_id, projection_type, key) DO UPDATE SET" in src
