from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from kura_workers.external_activity_contract import (
    CONTRACT_VERSION_V1,
    REQUIRED_FIELDS_V1,
    contract_field_inventory_v1,
    validate_external_activity_contract_v1,
)


def _valid_contract_payload() -> dict:
    return {
        "contract_version": CONTRACT_VERSION_V1,
        "source": {
            "provider": "strava",
            "provider_user_id": "usr-123",
            "external_activity_id": "act-9001",
            "external_event_version": "v7",
            "ingestion_method": "file_import",
            "raw_payload_ref": "s3://imports/act-9001.json",
            "imported_at": "2026-02-12T13:20:00+00:00",
        },
        "workout": {
            "workout_type": "run",
            "title": "Easy aerobic run",
            "sport": "running",
            "duration_seconds": 3678.2,
            "distance_meters": 10020.1,
            "energy_kj": 1900.5,
            "calories_kcal": 452.0,
        },
        "session": {
            "started_at": "2026-02-12T07:00:00+00:00",
            "ended_at": "2026-02-12T08:02:00+00:00",
            "timezone": "Europe/Berlin",
            "local_date": "2026-02-12",
            "local_week": "2026-W07",
            "session_id": "2026-02-12-morning-run",
        },
        "sets": [
            {
                "sequence": 1,
                "exercise": "run_interval",
                "duration_seconds": 600,
                "distance_meters": 2000,
                "rpe": 6,
            },
            {
                "sequence": 2,
                "exercise": "run_interval",
                "duration_seconds": 600,
                "distance_meters": 2000,
                "rpe": 7,
            },
        ],
        "provenance": {
            "mapping_version": "garmin-strava-v1",
            "mapped_at": "2026-02-12T13:25:00+00:00",
            "source_confidence": 0.94,
            "field_provenance": {
                "workout.distance_meters": {
                    "source_path": "$.distance",
                    "confidence": 0.99,
                    "status": "mapped",
                    "unit_original": "m",
                    "unit_normalized": "m",
                }
            },
            "unsupported_fields": ["$.device_metrics.ground_contact_balance"],
            "warnings": ["estimated calories from moving time"],
        },
    }


def test_contract_v1_accepts_valid_payload():
    model = validate_external_activity_contract_v1(_valid_contract_payload())

    assert model.contract_version == CONTRACT_VERSION_V1
    assert model.source.provider == "strava"
    assert model.session.local_week == "2026-W07"
    assert len(model.sets) == 2


def test_contract_v1_rejects_missing_required_source_field():
    payload = _valid_contract_payload()
    del payload["source"]["provider_user_id"]

    with pytest.raises(ValidationError, match="provider_user_id"):
        validate_external_activity_contract_v1(payload)


def test_contract_v1_rejects_inverted_session_time_window():
    payload = _valid_contract_payload()
    payload["session"]["started_at"] = "2026-02-12T08:02:00+00:00"
    payload["session"]["ended_at"] = "2026-02-12T07:00:00+00:00"

    with pytest.raises(ValidationError, match="session.ended_at must be >= session.started_at"):
        validate_external_activity_contract_v1(payload)


def test_contract_v1_rejects_duplicate_set_sequences():
    payload = _valid_contract_payload()
    duplicate = deepcopy(payload["sets"][0])
    duplicate["sequence"] = 1
    payload["sets"].append(duplicate)

    with pytest.raises(ValidationError, match="set.sequence must be unique per activity"):
        validate_external_activity_contract_v1(payload)


def test_contract_field_inventory_includes_required_and_optional_sections():
    inventory = contract_field_inventory_v1()

    assert "required" in inventory
    assert "optional" in inventory
    assert inventory["required"]["root"] == REQUIRED_FIELDS_V1["root"]
    assert "external_event_version" in inventory["optional"]["source"]


def test_contract_v1_accepts_relative_intensity_payloads() -> None:
    payload = _valid_contract_payload()
    payload["workout"]["relative_intensity"] = {
        "value_pct": 88.0,
        "reference_type": "critical_speed",
        "reference_value": 4.3,
        "reference_measured_at": "2026-02-01T08:00:00+00:00",
        "reference_confidence": 0.76,
    }
    payload["sets"][0]["relative_intensity"] = {
        "value_pct": 95.0,
        "reference_type": "mss",
        "reference_value": 9.1,
        "reference_measured_at": "2026-02-10T08:00:00+00:00",
        "reference_confidence": 0.82,
    }

    model = validate_external_activity_contract_v1(payload)
    assert model.workout.relative_intensity is not None
    assert model.workout.relative_intensity.reference_type == "critical_speed"
    assert model.sets[0].relative_intensity is not None
    assert model.sets[0].relative_intensity.reference_type == "mss"
