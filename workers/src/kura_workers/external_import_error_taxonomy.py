"""Stable error taxonomy for external import pipeline monitoring."""

from __future__ import annotations

from typing import Literal

ImportErrorClass = Literal[
    "parse",
    "mapping",
    "validation",
    "dedup",
    "other",
]

IMPORT_ERROR_CLASS_BY_CODE: dict[str, ImportErrorClass] = {
    "parse_error": "parse",
    "unsupported_format": "parse",
    "mapping_error": "mapping",
    "validation_error": "validation",
    "stale_version": "dedup",
    "version_conflict": "dedup",
    "partial_overlap": "dedup",
}


def classify_import_error_code(error_code: str | None) -> ImportErrorClass:
    normalized = str(error_code or "").strip().lower()
    if not normalized:
        return "other"
    return IMPORT_ERROR_CLASS_BY_CODE.get(normalized, "other")


def is_import_parse_quality_failure(error_code: str | None) -> bool:
    return classify_import_error_code(error_code) in {"parse", "mapping", "validation"}


def external_import_error_taxonomy_v1() -> dict[str, object]:
    return {
        "schema_version": "external_import_error_taxonomy.v1",
        "classes": ["parse", "mapping", "validation", "dedup", "other"],
        "code_to_class": dict(IMPORT_ERROR_CLASS_BY_CODE),
        "parse_quality_classes": ["parse", "mapping", "validation"],
    }
