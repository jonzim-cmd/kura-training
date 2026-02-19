from __future__ import annotations

from kura_workers.external_activity_contract import contract_field_inventory_v1
from kura_workers.external_import_mapping_v2 import import_mapping_contract_v2


def test_external_activity_contract_declares_workout_intensity_fields() -> None:
    inventory = contract_field_inventory_v1()
    optional_workout_fields = set(inventory["optional"]["workout"])
    assert {
        "heart_rate_avg",
        "heart_rate_max",
        "power_watt",
        "pace_min_per_km",
        "session_rpe",
    } <= optional_workout_fields


def test_external_import_mapping_contract_covers_extended_modalities() -> None:
    contract = import_mapping_contract_v2()
    expected_modalities = {
        "running",
        "cycling",
        "strength",
        "hybrid",
        "swimming",
        "rowing",
        "team_sport",
    }
    assert expected_modalities <= set(contract["modalities"])
    assert expected_modalities <= set(contract["modality_profiles"])


def test_external_import_mapping_contract_tracks_modality_support_matrices_for_all_modalities() -> None:
    contract = import_mapping_contract_v2()
    expected_modalities = set(contract["modalities"])
    for matrix in contract["provider_modality_matrix"].values():
        assert expected_modalities <= set(matrix.keys())
    for matrix in contract["format_modality_matrix"].values():
        assert expected_modalities <= set(matrix.keys())

