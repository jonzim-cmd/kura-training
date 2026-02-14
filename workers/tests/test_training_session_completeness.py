from __future__ import annotations

from kura_workers.training_session_completeness import evaluate_session_completeness


def _session_payload(blocks: list[dict]) -> dict:
    return {
        "contract_version": "session.logged.v1",
        "session_meta": {"sport": "hybrid", "timezone": "Europe/Berlin"},
        "blocks": blocks,
        "provenance": {"source_type": "manual"},
    }


def test_strength_with_rpe_without_hr_is_analysis_basic() -> None:
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
    assert result["analysis_advanced"] is False
    assert result["tier"] == "analysis_basic"


def test_interval_with_pace_and_borg_without_hr_is_analysis_basic() -> None:
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
    assert result["analysis_advanced"] is True
    assert result["tier"] == "analysis_advanced"


def test_interval_with_explicit_not_applicable_anchor_is_still_basic() -> None:
    payload = _session_payload(
        [
            {
                "block_type": "interval_endurance",
                "dose": {
                    "work": {"duration_seconds": 120},
                    "recovery": {"duration_seconds": 60},
                    "repeats": 8,
                },
                "intensity_anchors_status": "not_applicable",
            }
        ]
    )

    result = evaluate_session_completeness(payload)
    assert result["log_valid"] is True
    assert result["analysis_basic"] is True
    assert result["analysis_advanced"] is False
    assert result["tier"] == "analysis_basic"


def test_invalid_payload_returns_invalid_tier() -> None:
    payload = {
        "contract_version": "session.logged.v1",
        "session_meta": {"sport": "running"},
        "blocks": [
            {
                "block_type": "interval_endurance",
                "dose": {
                    "work": {"duration_seconds": 120},
                    "recovery": {"duration_seconds": 60},
                    "repeats": 8,
                },
            }
        ],
    }

    result = evaluate_session_completeness(payload)
    assert result["log_valid"] is False
    assert result["tier"] == "invalid"
    assert result["errors"]
