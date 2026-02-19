from __future__ import annotations

import pytest
from pydantic import ValidationError

from kura_workers.training_session_contract import (
    BLOCK_TYPES,
    CONTRACT_VERSION_V1,
    MEASUREMENT_STATES,
    block_catalog_v1,
    validate_session_logged_payload,
)


def _strength_block() -> dict[str, object]:
    return {
        "block_type": "strength_set",
        "dose": {"work": {"reps": 5}, "repeats": 5, "recovery": {"duration_seconds": 120}},
        "intensity_anchors": [
            {
                "measurement_state": "measured",
                "unit": "rpe",
                "value": 8,
            }
        ],
    }


def _base_payload(blocks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION_V1,
        "session_meta": {"sport": "hybrid", "timezone": "Europe/Berlin"},
        "blocks": blocks,
        "provenance": {"source_type": "manual"},
    }


def test_block_catalog_exposes_machine_readable_contract() -> None:
    catalog = block_catalog_v1()
    assert catalog["contract_version"] == CONTRACT_VERSION_V1
    assert set(BLOCK_TYPES).issubset(set(catalog["block_types"]))
    assert set(MEASUREMENT_STATES) == set(catalog["measurement_state_values"])
    assert catalog["intensity_policy"]["global_hr_requirement"] is False
    assert "critical_speed" in set(catalog["relative_intensity_reference_types"])


def test_strength_block_is_valid_without_hr_when_anchor_present() -> None:
    payload = _base_payload([_strength_block()])
    model = validate_session_logged_payload(payload)
    assert model.contract_version == CONTRACT_VERSION_V1


def test_performance_block_requires_anchor_or_explicit_not_applicable() -> None:
    payload_missing_anchor = _base_payload(
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
        validate_session_logged_payload(payload_missing_anchor)

    payload_not_applicable = _base_payload(
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
    validate_session_logged_payload(payload_not_applicable)


def test_metric_entry_requires_measurement_state() -> None:
    payload = _base_payload(
        [
            {
                "block_type": "strength_set",
                "dose": {"work": {"reps": 5}},
                "intensity_anchors": [
                    {"measurement_state": "measured", "unit": "rpe", "value": 8}
                ],
                "metrics": {
                    "heart_rate_avg": {"value": 150, "unit": "bpm"},
                },
            }
        ]
    )
    with pytest.raises(ValidationError):
        validate_session_logged_payload(payload)


def test_hybrid_session_with_multiple_block_types_is_valid() -> None:
    payload = _base_payload(
        [
            _strength_block(),
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


def test_relative_intensity_payload_is_valid_when_reference_is_specified() -> None:
    payload = _base_payload(
        [
            {
                "block_type": "strength_set",
                "dose": {"work": {"reps": 5}},
                "intensity_anchors": [
                    {"measurement_state": "measured", "unit": "rpe", "value": 8}
                ],
                "relative_intensity": {
                    "value_pct": 88.0,
                    "reference_type": "e1rm",
                    "reference_value": 120.0,
                    "reference_measured_at": "2026-02-10T08:00:00+00:00",
                    "reference_confidence": 0.82,
                },
            }
        ]
    )
    model = validate_session_logged_payload(payload)
    assert model.blocks[0].relative_intensity is not None
    assert model.blocks[0].relative_intensity.reference_type == "e1rm"


def test_relative_intensity_rejects_unknown_reference_type() -> None:
    payload = _base_payload(
        [
            {
                "block_type": "strength_set",
                "dose": {"work": {"reps": 5}},
                "intensity_anchors": [
                    {"measurement_state": "measured", "unit": "rpe", "value": 8}
                ],
                "relative_intensity": {
                    "value_pct": 88.0,
                    "reference_type": "unknown_metric",
                },
            }
        ]
    )
    with pytest.raises(ValidationError):
        validate_session_logged_payload(payload)
