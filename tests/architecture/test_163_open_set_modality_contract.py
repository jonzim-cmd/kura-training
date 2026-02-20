from __future__ import annotations

from kura_workers.external_activity_contract import validate_external_activity_contract_v1
from kura_workers.external_import_mapping_v2 import (
    _infer_modality,
    import_mapping_contract_v2,
    map_external_activity_to_session_logged_v2,
)


def _unknown_modality_payload() -> dict:
    return {
        "contract_version": "external_activity.v1",
        "source": {
            "provider": "strava",
            "provider_user_id": "u-1",
            "external_activity_id": "a-unknown",
            "ingestion_method": "file_import",
        },
        "workout": {
            "workout_type": "custom_workout",
            "duration_seconds": 2100,
            "distance_meters": 4200,
        },
        "session": {
            "started_at": "2026-02-14T08:00:00+00:00",
            "ended_at": "2026-02-14T08:35:00+00:00",
            "timezone": "UTC",
        },
        "provenance": {
            "mapping_version": "strava-v2-spec",
            "mapped_at": "2026-02-14T08:36:00+00:00",
        },
    }


def test_open_set_modality_keeps_unknown_instead_of_forced_running() -> None:
    modality, confidence, source = _infer_modality(
        workout_type="custom_workout",
        sport=None,
        sets_data=[{"duration_seconds": 1200}],
    )
    assert modality == "unknown"
    assert confidence < 0.5
    assert "open_set" in source or "unknown" in source


def test_unknown_modality_propagates_into_session_meta_surface() -> None:
    canonical = validate_external_activity_contract_v1(_unknown_modality_payload())
    mapped = map_external_activity_to_session_logged_v2(canonical)
    assert mapped["session_meta"]["modality"] == "unknown"
    assert mapped["session_meta"]["modality_source"]
    assert mapped["session_meta"]["modality"] != "running"


def test_import_mapping_contract_declares_open_set_rule_and_unknown_modality() -> None:
    contract = import_mapping_contract_v2()
    assert "unknown" in contract["modalities"]
    assert "unknown" in contract["modality_profiles"]
    assert any("Open-set routing" in rule for rule in contract["rules"])
