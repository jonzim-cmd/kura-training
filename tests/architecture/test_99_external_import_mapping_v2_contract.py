from __future__ import annotations

from kura_workers.external_activity_contract import validate_external_activity_contract_v1
from kura_workers.external_import_mapping_v2 import (
    import_mapping_contract_v2,
    map_external_activity_to_session_logged_v2,
)
from kura_workers.system_config import _get_conventions
from kura_workers.training_session_contract import validate_session_logged_payload


def _canonical_payload() -> dict:
    return {
        "contract_version": "external_activity.v1",
        "source": {
            "provider": "strava",
            "provider_user_id": "u-1",
            "external_activity_id": "a-1",
            "ingestion_method": "file_import",
        },
        "workout": {
            "workout_type": "run",
            "duration_seconds": 1800,
            "distance_meters": 5000,
        },
        "session": {
            "started_at": "2026-02-14T08:00:00+00:00",
            "ended_at": "2026-02-14T08:30:00+00:00",
            "timezone": "UTC",
        },
        "provenance": {
            "mapping_version": "strava-v2-spec",
            "mapped_at": "2026-02-14T08:31:00+00:00",
        },
    }


def test_external_import_mapping_v2_is_declared() -> None:
    conventions = _get_conventions()
    contract = conventions["external_import_mapping_v2"]["contract"]
    assert contract["schema_version"] == "external_import_mapping.v2"
    assert set(contract["provider_field_matrix"]) == {"garmin", "strava", "trainingpeaks"}
    assert set(contract["format_field_matrix"]) == {"fit", "tcx", "gpx"}


def test_external_import_mapping_v2_keeps_provider_fields_optional() -> None:
    contract = import_mapping_contract_v2()
    required = set(contract["required_core_fields"])
    assert "source.provider" not in required
    assert "source.external_event_version" not in required
    assert {"session.started_at", "workout.workout_type", "dose.work"} <= required


def test_external_import_mapping_v2_outputs_session_logged_blocks() -> None:
    canonical = validate_external_activity_contract_v1(_canonical_payload())
    session_payload = map_external_activity_to_session_logged_v2(canonical)
    model = validate_session_logged_payload(session_payload)
    assert len(model.blocks) >= 1
