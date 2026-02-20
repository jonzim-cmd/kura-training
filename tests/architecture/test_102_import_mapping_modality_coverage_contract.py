from __future__ import annotations

from kura_workers.external_activity_contract import validate_external_activity_contract_v1
from kura_workers.external_import_mapping_v2 import (
    import_mapping_contract_v2,
    map_external_activity_to_session_logged_v2,
)
from kura_workers.system_config import _get_conventions
from kura_workers.training_session_contract import validate_session_logged_payload


def _canonical_payload(workout_type: str, sport: str) -> dict:
    return {
        "contract_version": "external_activity.v1",
        "source": {
            "provider": "trainingpeaks",
            "provider_user_id": "athlete-1",
            "external_activity_id": f"{workout_type}-1",
            "ingestion_method": "file_import",
        },
        "workout": {
            "workout_type": workout_type,
            "duration_seconds": 1800,
            "distance_meters": 5000,
            "sport": sport,
        },
        "session": {
            "started_at": "2026-02-14T08:00:00+00:00",
            "ended_at": "2026-02-14T08:30:00+00:00",
            "timezone": "UTC",
        },
        "provenance": {
            "mapping_version": "trainingpeaks-v2-spec",
            "mapped_at": "2026-02-14T08:31:00+00:00",
        },
    }


def test_import_mapping_modality_contract_is_exposed() -> None:
    conventions = _get_conventions()
    contract = conventions["external_import_mapping_v2"]["contract"]
    assert {
        "running",
        "cycling",
        "strength",
        "hybrid",
        "swimming",
        "rowing",
        "team_sport",
        "unknown",
    } <= set(contract["modalities"])
    assert {
        "running",
        "cycling",
        "strength",
        "hybrid",
        "swimming",
        "rowing",
        "team_sport",
        "unknown",
    } <= set(contract["modality_profiles"])
    assert "provider_modality_matrix" in contract


def test_import_mapping_modality_contract_keeps_provider_specific_fields_optional() -> None:
    contract = import_mapping_contract_v2()
    required = set(contract["required_core_fields"])
    assert "source.provider" not in required
    assert "source.external_event_version" not in required
    assert {"session.started_at", "workout.workout_type", "dose.work"} <= required


def test_import_mapping_modality_contract_maps_multiple_modalities_to_block_model() -> None:
    payloads = [
        _canonical_payload("run", "running"),
        _canonical_payload("bike", "cycling"),
        _canonical_payload("strength", "strength"),
        _canonical_payload("brick", "triathlon"),
    ]
    for payload in payloads:
        canonical = validate_external_activity_contract_v1(payload)
        session_payload = map_external_activity_to_session_logged_v2(canonical)
        model = validate_session_logged_payload(session_payload)
        assert len(model.blocks) >= 1
