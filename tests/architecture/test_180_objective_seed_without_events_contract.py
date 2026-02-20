from __future__ import annotations

from pathlib import Path

OBJECTIVE_STATE_HANDLER = Path("workers/src/kura_workers/handlers/objective_state.py")


def test_objective_state_contract_keeps_default_seed_when_event_stream_is_empty() -> None:
    src = OBJECTIVE_STATE_HANDLER.read_text(encoding="utf-8")
    assert "_default_objective_from_profile" in src
    assert "last_event_id=None" in src
    assert "projection_type = 'user_profile'" in src


def test_objective_state_contract_does_not_delete_projection_for_empty_stream() -> None:
    src = OBJECTIVE_STATE_HANDLER.read_text(encoding="utf-8")
    assert (
        "DELETE FROM projections WHERE user_id = %s AND projection_type = 'objective_state' AND key = 'active'"
        not in src
    )
