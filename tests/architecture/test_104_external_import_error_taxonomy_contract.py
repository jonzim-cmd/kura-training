from __future__ import annotations

from kura_workers.system_config import _get_conventions


def test_external_import_error_taxonomy_contract_is_declared() -> None:
    conventions = _get_conventions()
    contract = conventions["external_import_error_taxonomy_v1"]["contract"]
    assert contract["schema_version"] == "external_import_error_taxonomy.v1"
    assert set(contract["classes"]) == {
        "parse",
        "mapping",
        "validation",
        "dedup",
        "other",
    }
    assert set(contract["parse_quality_classes"]) == {
        "parse",
        "mapping",
        "validation",
    }


def test_external_import_error_taxonomy_separates_dedup_from_parse_quality() -> None:
    contract = _get_conventions()["external_import_error_taxonomy_v1"]["contract"]
    parse_quality = set(contract["parse_quality_classes"])
    assert "dedup" not in parse_quality
