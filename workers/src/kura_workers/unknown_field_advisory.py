"""Unknown-field advisory contract and mapping hints.

This module is the single source of truth for suggestive field mappings
used by both runtime quality checks and the published system contract.
"""

from __future__ import annotations

from typing import Any

_UNKNOWN_FIELD_MAPPING_HINTS: tuple[tuple[str, str, str], ...] = (
    ("session.completed", "overall_feeling", "enjoyment"),
    ("session.completed", "feeling", "enjoyment"),
    ("soreness.logged", "overall_level", "severity"),
    ("soreness.logged", "soreness_level", "severity"),
    ("energy.logged", "energy_level", "level"),
    ("set.logged", "notes", "load_context"),
)


def _normalize(value: str) -> str:
    return value.strip().lower()


def unknown_field_mapping_hints() -> dict[tuple[str, str], str]:
    """Return normalized `(event_type, field) -> mapped_field` hints."""
    hints: dict[tuple[str, str], str] = {}
    for event_type, field, mapped_field in _UNKNOWN_FIELD_MAPPING_HINTS:
        normalized_event_type = _normalize(event_type)
        normalized_field = _normalize(field)
        mapped = mapped_field.strip()
        if not normalized_event_type or not normalized_field or not mapped:
            continue
        hints[(normalized_event_type, normalized_field)] = mapped
    return hints


_UNKNOWN_FIELD_HINTS = unknown_field_mapping_hints()


def unknown_field_mapping_hint(event_type: str, field: str) -> str | None:
    """Resolve a mapping hint for one field, if known."""
    return _UNKNOWN_FIELD_HINTS.get((_normalize(event_type), _normalize(field)))


def unknown_field_advisory_contract_v1() -> dict[str, Any]:
    """Published contract for unknown-field advisory behavior."""
    mapping_hints = [
        {
            "event_type": event_type,
            "field": field,
            "mapped_field": mapped_field,
        }
        for event_type, field, mapped_field in _UNKNOWN_FIELD_MAPPING_HINTS
    ]
    return {
        "schema_version": "unknown_field_advisory.v1",
        "policy_role": "advisory_only",
        "write_policy": {
            "mode": "warn_only",
            "accept_unknown_fields": True,
            "block_unknown_fields": False,
        },
        "mapping_hints": mapping_hints,
        "fallback_hint": "Check system_config.event_conventions for canonical field names.",
    }
