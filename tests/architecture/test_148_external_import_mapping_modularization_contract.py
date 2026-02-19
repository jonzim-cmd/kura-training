from __future__ import annotations

from kura_workers.external_import_mapping_profiles_v2 import (
    CORE_IMPORT_FIELDS_V2,
    FORMAT_FIELD_MATRIX_V2,
    FORMAT_MODALITY_MATRIX_V2,
    MODALITY_PROFILES_V2,
    PROVIDER_FIELD_MATRIX_V2,
    PROVIDER_MODALITY_MATRIX_V2,
)
from kura_workers.external_import_mapping_v2 import import_mapping_contract_v2


def test_external_import_mapping_contract_is_composed_from_profile_module() -> None:
    contract = import_mapping_contract_v2()
    assert contract["required_core_fields"] == list(CORE_IMPORT_FIELDS_V2)
    assert contract["modality_profiles"] == MODALITY_PROFILES_V2
    assert contract["provider_field_matrix"] == PROVIDER_FIELD_MATRIX_V2
    assert contract["format_field_matrix"] == FORMAT_FIELD_MATRIX_V2
    assert contract["provider_modality_matrix"] == PROVIDER_MODALITY_MATRIX_V2
    assert contract["format_modality_matrix"] == FORMAT_MODALITY_MATRIX_V2
