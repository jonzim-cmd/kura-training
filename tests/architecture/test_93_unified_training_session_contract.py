from __future__ import annotations

import pytest
from pydantic import ValidationError

from kura_workers.event_conventions import get_event_conventions
from kura_workers.system_config import _get_conventions
from kura_workers.training_session_contract import (
    CONTRACT_VERSION_V1,
    MEASUREMENT_STATES,
    validate_session_logged_payload,
)
from tests.architecture.conftest import assert_kura_api_test_passes


SESSION_RUNTIME_TESTS: tuple[str, ...] = (
    "routes::events::tests::session_logged_contract_accepts_strength_without_hr",
    "routes::events::tests::session_logged_contract_rejects_missing_intensity_anchors_for_performance_block",
    "routes::events::tests::session_logged_contract_accepts_not_applicable_anchor_status",
    "routes::events::tests::session_logged_contract_rejects_metric_without_measurement_state",
    "routes::events::tests::session_logged_contract_accepts_hybrid_mixed_blocks",
)


def _session_payload(blocks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION_V1,
        "session_meta": {"sport": "hybrid", "timezone": "Europe/Berlin"},
        "blocks": blocks,
        "provenance": {"source_type": "manual"},
    }


def test_session_logged_event_convention_declares_unified_block_contract() -> None:
    conventions = get_event_conventions()
    session = conventions["session.logged"]

    assert session["schema_version"] == CONTRACT_VERSION_V1
    assert "session_meta" in session["fields"]
    assert "blocks" in session["fields"]
    assert "subjective_response" in session["fields"]
    assert "provenance" in session["fields"]
    assert "no global hr" in session["completeness_policy"].lower()


def test_system_conventions_expose_training_session_block_model() -> None:
    conventions = _get_conventions()
    block_model = conventions["training_session_block_model_v1"]
    contract = block_model["contract"]

    assert block_model["event_type"] == "session.logged"
    assert contract["contract_version"] == CONTRACT_VERSION_V1
    assert set(MEASUREMENT_STATES) == set(contract["measurement_state_values"])
    assert contract["intensity_policy"]["global_hr_requirement"] is False


def test_session_logged_contract_accepts_strength_without_heart_rate() -> None:
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

    validate_session_logged_payload(payload)


def test_session_logged_contract_requires_anchor_or_explicit_not_applicable() -> None:
    missing_anchor = _session_payload(
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
    with pytest.raises(ValidationError):
        validate_session_logged_payload(missing_anchor)

    explicit_not_applicable = _session_payload(
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
    validate_session_logged_payload(explicit_not_applicable)


def test_session_logged_contract_requires_measurement_state_in_metrics() -> None:
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
                    }
                ],
                "metrics": {
                    "heart_rate_avg": {"unit": "bpm", "value": 160},
                },
            }
        ]
    )
    with pytest.raises(ValidationError):
        validate_session_logged_payload(payload)


def test_session_logged_contract_accepts_hybrid_mixed_blocks() -> None:
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
            },
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
            },
            {
                "block_type": "plyometric_reactive",
                "dose": {
                    "work": {"contacts": 60},
                    "recovery": {"duration_seconds": 90},
                    "repeats": 3,
                },
                "intensity_anchors": [
                    {
                        "measurement_state": "measured",
                        "unit": "rpe",
                        "value": 6,
                    }
                ],
            },
        ]
    )

    model = validate_session_logged_payload(payload)
    assert len(model.blocks) == 3


def test_session_logged_runtime_contract_cases_pass() -> None:
    for test_name in SESSION_RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
