from __future__ import annotations

from kura_workers.system_config import _get_conventions
from kura_workers.training_session_completeness import (
    ERROR_CODE_MEASUREMENT_STATE_MISSING,
    ERROR_CODE_MISSING_INTENSITY_ANCHOR,
    ERROR_SCHEMA_VERSION,
    evaluate_session_completeness,
)


def _session_payload(blocks: list[dict]) -> dict:
    return {
        "contract_version": "session.logged.v1",
        "session_meta": {"sport": "hybrid", "timezone": "Europe/Berlin"},
        "blocks": blocks,
        "provenance": {"source_type": "manual"},
    }


def test_completeness_error_taxonomy_is_declared_in_public_contract() -> None:
    conventions = _get_conventions()
    policy = conventions["training_session_block_model_v1"]["completeness_policy"]
    error_contract = policy["error_contract"]

    assert error_contract["schema_version"] == ERROR_SCHEMA_VERSION
    assert "error_code" in error_contract["fields"]
    assert ERROR_CODE_MISSING_INTENSITY_ANCHOR in error_contract["codes"]
    assert ERROR_CODE_MEASUREMENT_STATE_MISSING in error_contract["codes"]


def test_missing_anchor_returns_structured_error_code_with_block_scope() -> None:
    payload = _session_payload(
        [
            {
                "block_type": "interval_endurance",
                "dose": {
                    "work": {"duration_seconds": 120},
                    "recovery": {"duration_seconds": 60},
                    "repeats": 8,
                },
            }
        ]
    )
    result = evaluate_session_completeness(payload)
    assert result["log_valid"] is False
    assert result["error_schema_version"] == ERROR_SCHEMA_VERSION
    detail = next(
        entry
        for entry in result["error_details"]
        if entry["error_code"] == ERROR_CODE_MISSING_INTENSITY_ANCHOR
    )
    assert detail["block_scope"]["block_index"] == 0
    assert detail["block_scope"]["block_type"] == "interval_endurance"


def test_missing_measurement_state_maps_to_code_without_message_parsing() -> None:
    payload = _session_payload(
        [
            {
                "block_type": "strength_set",
                "dose": {"work": {"reps": 5}},
                "intensity_anchors": [
                    {"measurement_state": "measured", "unit": "rpe", "value": 8}
                ],
                "metrics": {"heart_rate_avg": {"value": 150, "unit": "bpm"}},
            }
        ]
    )
    result = evaluate_session_completeness(payload)
    codes = {entry["error_code"] for entry in result["error_details"]}
    assert ERROR_CODE_MEASUREMENT_STATE_MISSING in codes
