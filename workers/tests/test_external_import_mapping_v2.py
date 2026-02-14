from __future__ import annotations

from kura_workers.external_activity_contract import validate_external_activity_contract_v1
from kura_workers.external_import_mapping_v2 import (
    import_mapping_contract_v2,
    map_external_activity_to_session_logged_v2,
)
from kura_workers.training_session_contract import validate_session_logged_payload


def _canonical_payload(*, workout_type: str = "run", sets: list[dict] | None = None) -> dict:
    return {
        "contract_version": "external_activity.v1",
        "source": {
            "provider": "garmin",
            "provider_user_id": "athlete-1",
            "external_activity_id": "activity-1",
            "ingestion_method": "file_import",
        },
        "workout": {
            "workout_type": workout_type,
            "duration_seconds": 1800,
            "distance_meters": 5000,
        },
        "session": {
            "started_at": "2026-02-14T08:00:00+00:00",
            "ended_at": "2026-02-14T08:30:00+00:00",
            "timezone": "UTC",
        },
        "sets": sets or [],
        "provenance": {
            "mapping_version": "garmin-v2-spec",
            "mapped_at": "2026-02-14T08:31:00+00:00",
        },
    }


def test_import_mapping_contract_v2_exposes_provider_and_format_matrix() -> None:
    contract = import_mapping_contract_v2()
    assert contract["schema_version"] == "external_import_mapping.v2"
    assert set(contract["provider_field_matrix"]) == {"garmin", "strava", "trainingpeaks"}
    assert set(contract["format_field_matrix"]) == {"fit", "tcx", "gpx"}

    allowed_states = {"supported", "partial", "not_available"}
    for matrix in contract["provider_field_matrix"].values():
        assert set(matrix.values()) <= allowed_states
    for matrix in contract["format_field_matrix"].values():
        assert set(matrix.values()) <= allowed_states


def test_import_mapping_contract_v2_required_fields_are_provider_agnostic() -> None:
    contract = import_mapping_contract_v2()
    required = set(contract["required_core_fields"])
    assert "source.provider" not in required
    assert "source.external_event_version" not in required
    assert {"session.started_at", "workout.workout_type", "dose.work"} <= required


def test_endurance_import_maps_to_session_logged_block_model() -> None:
    canonical = validate_external_activity_contract_v1(_canonical_payload(workout_type="run"))
    session_payload = map_external_activity_to_session_logged_v2(canonical)

    validated = validate_session_logged_payload(session_payload)
    assert validated.contract_version == "session.logged.v1"
    assert validated.blocks[0].block_type in {
        "continuous_endurance",
        "interval_endurance",
    }


def test_strength_sets_map_to_strength_blocks() -> None:
    canonical = validate_external_activity_contract_v1(
        _canonical_payload(
            workout_type="strength",
            sets=[
                {
                    "sequence": 1,
                    "exercise": "Back Squat",
                    "reps": 5,
                    "weight_kg": 100,
                    "rpe": 8,
                }
            ],
        )
    )
    session_payload = map_external_activity_to_session_logged_v2(canonical)

    validated = validate_session_logged_payload(session_payload)
    assert validated.blocks[0].block_type == "strength_set"
    anchor = validated.blocks[0].intensity_anchors[0]
    assert anchor.unit == "rpe"
    assert float(anchor.value) == 8.0
