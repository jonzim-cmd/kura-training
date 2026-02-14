from __future__ import annotations

from kura_workers.system_config import _get_conventions
from kura_workers.training_session_completeness import evaluate_session_completeness


def _session_payload(blocks: list[dict]) -> dict:
    return {
        "contract_version": "session.logged.v1",
        "session_meta": {"sport": "hybrid", "timezone": "Europe/Berlin"},
        "blocks": blocks,
        "provenance": {"source_type": "manual"},
    }


def test_completeness_levels_are_declared_in_public_contract() -> None:
    conventions = _get_conventions()
    policy = conventions["training_session_block_model_v1"]["completeness_policy"]

    assert set(policy["levels"]) == {"log_valid", "analysis_basic", "analysis_advanced"}
    assert policy["global_requirements"]["heart_rate_required"] is False


def test_strength_without_heart_rate_is_log_valid_and_basic() -> None:
    payload = _session_payload(
        [
            {
                "block_type": "strength_set",
                "dose": {
                    "work": {"reps": 5},
                    "recovery": {"duration_seconds": 120},
                    "repeats": 5,
                },
                "intensity_anchors": [
                    {
                        "measurement_state": "measured",
                        "unit": "rpe",
                        "value": 8,
                    }
                ],
            }
        ]
    )

    result = evaluate_session_completeness(payload)
    assert result["log_valid"] is True
    assert result["analysis_basic"] is True


def test_interval_with_pace_and_borg_without_hr_is_complete() -> None:
    payload = _session_payload(
        [
            {
                "block_type": "interval_endurance",
                "dose": {
                    "work": {"duration_seconds": 120},
                    "recovery": {"duration_seconds": 60},
                    "repeats": 8,
                },
                "intensity_anchors": [
                    {
                        "measurement_state": "measured",
                        "unit": "min_per_km",
                        "value": 4.0,
                    },
                    {
                        "measurement_state": "measured",
                        "unit": "borg_cr10",
                        "value": 7,
                    },
                ],
                "metrics": {
                    "heart_rate_avg": {
                        "measurement_state": "not_measured",
                    }
                },
            }
        ]
    )

    result = evaluate_session_completeness(payload)
    assert result["log_valid"] is True
    assert result["analysis_basic"] is True
    assert result["tier"] in {"analysis_basic", "analysis_advanced"}
