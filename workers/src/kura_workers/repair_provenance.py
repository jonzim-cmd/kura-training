"""Repair provenance contract helpers (PDC.9)."""

from __future__ import annotations

from typing import Any

_SOURCE_TYPES = {"explicit", "inferred", "estimated", "user_confirmed"}
_SCOPES = {"single_set", "exercise_session", "session"}


def normalize_confidence(value: Any) -> tuple[float, str]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    parsed = max(0.0, min(1.0, parsed))
    if parsed >= 0.86:
        band = "high"
    elif parsed >= 0.6:
        band = "medium"
    else:
        band = "low"
    return round(parsed, 3), band


def build_repair_provenance(
    *,
    source_type: str,
    confidence: Any,
    applies_scope: str,
    reason: str,
) -> dict[str, Any]:
    normalized_source = str(source_type).strip().lower() or "estimated"
    if normalized_source not in _SOURCE_TYPES:
        normalized_source = "estimated"

    normalized_scope = str(applies_scope).strip().lower() or "session"
    if normalized_scope not in _SCOPES:
        normalized_scope = "session"

    normalized_confidence, confidence_band = normalize_confidence(confidence)
    return {
        "source_type": normalized_source,
        "confidence": normalized_confidence,
        "confidence_band": confidence_band,
        "applies_scope": normalized_scope,
        "reason": str(reason or "").strip() or "unspecified_repair_reason",
    }


def summarize_repair_provenance(entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        return {
            "entries": 0,
            "by_source_type": {},
            "by_confidence_band": {},
            "low_confidence_entries": 0,
        }

    by_source_type: dict[str, int] = {}
    by_confidence_band: dict[str, int] = {}
    low_confidence_entries = 0

    for entry in entries:
        source = str(entry.get("source_type", "estimated"))
        band = str(entry.get("confidence_band", "low"))
        by_source_type[source] = by_source_type.get(source, 0) + 1
        by_confidence_band[band] = by_confidence_band.get(band, 0) + 1
        if band == "low":
            low_confidence_entries += 1

    return {
        "entries": len(entries),
        "by_source_type": by_source_type,
        "by_confidence_band": by_confidence_band,
        "low_confidence_entries": low_confidence_entries,
    }
