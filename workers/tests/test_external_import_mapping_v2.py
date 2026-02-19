from __future__ import annotations

import json
from pathlib import Path

from kura_workers.external_activity_contract import validate_external_activity_contract_v1
from kura_workers.external_import_mapping_v2 import (
    import_mapping_contract_v2,
    map_external_activity_to_session_logged_v2,
)
from kura_workers.training_session_contract import validate_session_logged_payload

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "external_import_mapping_v2"


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


def _load_fixture(name: str) -> dict:
    with (_FIXTURE_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_import_mapping_contract_v2_exposes_provider_format_and_modality_matrices() -> None:
    contract = import_mapping_contract_v2()
    assert contract["schema_version"] == "external_import_mapping.v2"
    assert set(contract["provider_field_matrix"]) == {"garmin", "strava", "trainingpeaks"}
    assert set(contract["format_field_matrix"]) == {"fit", "tcx", "gpx"}
    assert {
        "running",
        "cycling",
        "strength",
        "hybrid",
        "swimming",
        "rowing",
        "team_sport",
    } <= set(contract["modalities"])
    assert set(contract["provider_modality_matrix"]) == {"garmin", "strava", "trainingpeaks"}
    assert set(contract["format_modality_matrix"]) == {"fit", "tcx", "gpx"}

    allowed_states = {"supported", "partial", "not_available"}
    for matrix in contract["provider_field_matrix"].values():
        assert set(matrix.values()) <= allowed_states
    for matrix in contract["format_field_matrix"].values():
        assert set(matrix.values()) <= allowed_states
    for matrix in contract["provider_modality_matrix"].values():
        assert set(matrix.values()) <= allowed_states
    for matrix in contract["format_modality_matrix"].values():
        assert set(matrix.values()) <= allowed_states


def test_import_mapping_contract_v2_required_fields_are_provider_agnostic() -> None:
    contract = import_mapping_contract_v2()
    required = set(contract["required_core_fields"])
    assert "source.provider" not in required
    assert "source.external_event_version" not in required
    assert {"session.started_at", "workout.workout_type", "dose.work"} <= required


def test_external_contract_accepts_relative_intensity_on_workout_and_set() -> None:
    canonical = validate_external_activity_contract_v1(
        _canonical_payload(
            workout_type="run",
            sets=[
                {
                    "sequence": 1,
                    "exercise": "Sprint",
                    "distance_meters": 100,
                    "rpe": 9,
                    "relative_intensity": {
                        "value_pct": 96.0,
                        "reference_type": "mss",
                        "reference_value": 9.2,
                        "reference_measured_at": "2026-02-10T08:00:00+00:00",
                        "reference_confidence": 0.8,
                    },
                }
            ],
        )
        | {
            "workout": {
                "workout_type": "run",
                "duration_seconds": 1800,
                "distance_meters": 5000,
                "relative_intensity": {
                    "value_pct": 88.0,
                    "reference_type": "critical_speed",
                    "reference_value": 4.3,
                    "reference_measured_at": "2026-02-01T08:00:00+00:00",
                    "reference_confidence": 0.7,
                },
            }
        }
    )
    session_payload = map_external_activity_to_session_logged_v2(canonical)
    block = session_payload["blocks"][0]
    assert block["relative_intensity"]["reference_type"] in {"mss", "critical_speed"}


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


def test_workout_relative_intensity_enriches_block_when_set_specific_value_missing() -> None:
    canonical = validate_external_activity_contract_v1(
        _canonical_payload(workout_type="run")
        | {
            "workout": {
                "workout_type": "run",
                "duration_seconds": 1200,
                "distance_meters": 3500,
                "relative_intensity": {
                    "value_pct": 87.0,
                    "reference_type": "critical_speed",
                    "reference_value": 4.2,
                    "reference_measured_at": "2026-02-10T08:00:00+00:00",
                    "reference_confidence": 0.74,
                },
            }
        }
    )
    session_payload = map_external_activity_to_session_logged_v2(canonical)
    validated = validate_session_logged_payload(session_payload)
    block = validated.blocks[0]
    assert block.relative_intensity is not None
    assert block.relative_intensity.reference_type == "critical_speed"


def test_golden_fixtures_cover_running_cycling_strength_and_hybrid() -> None:
    fixture_names = [
        "running_garmin.json",
        "cycling_trainingpeaks.json",
        "strength_garmin_sets.json",
        "hybrid_strava_brick.json",
    ]

    for fixture_name in fixture_names:
        fixture = _load_fixture(fixture_name)
        canonical = validate_external_activity_contract_v1(fixture["canonical"])
        session_payload = map_external_activity_to_session_logged_v2(canonical)
        validated = validate_session_logged_payload(session_payload)
        first_block = validated.blocks[0]
        expectations = fixture["expectations"]

        assert first_block.block_type in expectations["block_types_any"], fixture_name
        metrics = session_payload["blocks"][0]["metrics"]
        for metric_key in expectations["not_measured_metrics"]:
            assert metrics[metric_key]["measurement_state"] == "not_measured", fixture_name

        anchors = session_payload["blocks"][0].get("intensity_anchors") or []
        if anchors:
            units = {str(anchor.get("unit") or "") for anchor in anchors}
            assert units & set(expectations["anchor_units_any"]), fixture_name
