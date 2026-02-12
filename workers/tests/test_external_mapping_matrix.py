from __future__ import annotations

from kura_workers.external_activity_contract import validate_external_activity_contract_v1
from kura_workers.external_mapping_matrix import (
    map_external_payload_v1,
    provider_mapping_matrices_v1,
)


def test_mapping_matrix_v1_contains_all_three_providers():
    matrices = provider_mapping_matrices_v1()

    assert set(matrices.keys()) == {"garmin", "strava", "trainingpeaks"}


def test_strava_mapping_converts_kj_to_kcal_and_marks_unsupported_fields():
    payload = {
        "type": "run",
        "moving_time": 1800,
        "distance": 5000,
        "kilojoules": 1000,
        "start_date": "2026-02-12T06:30:00+00:00",
        "timezone": "UTC",
        "suffer_score": 77,
    }
    result = map_external_payload_v1(
        provider="strava",
        provider_user_id="u-1",
        external_activity_id="strava-1",
        external_event_version="3",
        raw_payload=payload,
    )

    draft = result.canonical_draft
    assert draft["workout"]["duration_seconds"] == 1800
    assert draft["workout"]["distance_meters"] == 5000
    assert round(draft["workout"]["calories_kcal"], 2) == 239.01
    assert "suffer_score" in result.unsupported_fields

    validated = validate_external_activity_contract_v1(draft)
    assert validated.source.provider == "strava"


def test_trainingpeaks_mapping_applies_minutes_and_km_conversions():
    payload = {
        "workout": {
            "type": "bike",
            "totalTimeMinutes": 45,
            "distanceKm": 32.5,
            "energyKj": 820,
            "startTime": "2026-02-11T18:00:00+00:00",
            "endTime": "2026-02-11T18:45:00+00:00",
            "timezone": "UTC",
            "normalizedPower": 265,
        }
    }
    result = map_external_payload_v1(
        provider="trainingpeaks",
        provider_user_id="u-2",
        external_activity_id="tp-42",
        raw_payload=payload,
    )

    draft = result.canonical_draft
    assert draft["workout"]["duration_seconds"] == 2700
    assert draft["workout"]["distance_meters"] == 32500
    assert round(draft["workout"]["calories_kcal"], 2) == 195.98
    assert "workout.normalizedPower" in result.unsupported_fields

    formulas = {entry["formula"] for entry in result.unit_conversions}
    assert "minutes_to_seconds" in formulas
    assert "km_to_meters" in formulas
    assert "kj_to_kcal" in formulas

    validated = validate_external_activity_contract_v1(draft)
    assert validated.source.provider == "trainingpeaks"


def test_garmin_mapping_keeps_seconds_and_meters_identity():
    payload = {
        "activity": {
            "type": "ride",
            "start_time": "2026-02-10T07:00:00+00:00",
            "end_time": "2026-02-10T08:00:00+00:00",
            "timezone": "UTC",
        },
        "summary": {
            "duration_s": 3600,
            "distance_m": 28000,
            "energy_kj": 1500,
            "ground_contact_balance": 49.8,
        },
    }
    result = map_external_payload_v1(
        provider="garmin",
        provider_user_id="u-3",
        external_activity_id="garmin-8",
        raw_payload=payload,
    )

    draft = result.canonical_draft
    assert draft["workout"]["duration_seconds"] == 3600
    assert draft["workout"]["distance_meters"] == 28000
    assert "summary.ground_contact_balance" in result.unsupported_fields

    validated = validate_external_activity_contract_v1(draft)
    assert validated.source.provider == "garmin"
