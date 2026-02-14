from __future__ import annotations

from kura_workers.external_import_error_taxonomy import (
    classify_import_error_code,
    is_import_parse_quality_failure,
)


def test_classify_import_error_code_maps_known_classes() -> None:
    assert classify_import_error_code("parse_error") == "parse"
    assert classify_import_error_code("mapping_error") == "mapping"
    assert classify_import_error_code("validation_error") == "validation"
    assert classify_import_error_code("version_conflict") == "dedup"


def test_classify_import_error_code_defaults_to_other() -> None:
    assert classify_import_error_code(None) == "other"
    assert classify_import_error_code("unknown_error_code") == "other"


def test_parse_quality_failure_includes_parse_mapping_validation_only() -> None:
    assert is_import_parse_quality_failure("parse_error") is True
    assert is_import_parse_quality_failure("mapping_error") is True
    assert is_import_parse_quality_failure("validation_error") is True
    assert is_import_parse_quality_failure("version_conflict") is False
