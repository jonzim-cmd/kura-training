"""Helpers to apply set.corrected overlays over immutable set.logged events."""

from __future__ import annotations

from typing import Any


def _changed_field_value_and_provenance(
    raw_value: Any,
    bundle_provenance: dict[str, Any] | None,
) -> tuple[Any, dict[str, Any] | None]:
    if isinstance(raw_value, dict) and "value" in raw_value:
        field_provenance = raw_value.get("repair_provenance")
        if isinstance(field_provenance, dict):
            return raw_value.get("value"), field_provenance
        return raw_value.get("value"), bundle_provenance
    return raw_value, bundle_provenance


def apply_set_correction_chain(
    set_rows: list[dict[str, Any]],
    correction_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return set rows with effective_data + correction history applied in order."""
    corrected_rows: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}

    for row in set_rows:
        row_copy = dict(row)
        row_copy["effective_data"] = dict(row.get("data") or {})
        row_copy["correction_history"] = []
        row_copy["field_provenance"] = {}
        row_id = str(row.get("id", "")).strip()
        if row_id:
            by_id[row_id] = row_copy
        corrected_rows.append(row_copy)

    ordered_corrections = sorted(
        correction_rows,
        key=lambda row: (row.get("timestamp"), str(row.get("id", ""))),
    )
    for correction in ordered_corrections:
        data = correction.get("data") or {}
        target_event_id = str(data.get("target_event_id", "")).strip()
        if not target_event_id:
            continue
        target_row = by_id.get(target_event_id)
        if target_row is None:
            continue

        changed_fields = data.get("changed_fields") or {}
        if not isinstance(changed_fields, dict):
            continue

        bundle_provenance = data.get("repair_provenance")
        if not isinstance(bundle_provenance, dict):
            bundle_provenance = None

        for field, raw_value in changed_fields.items():
            field_name = str(field).strip()
            if not field_name:
                continue
            value, field_provenance = _changed_field_value_and_provenance(
                raw_value,
                bundle_provenance,
            )
            target_row["effective_data"][field_name] = value
            if isinstance(field_provenance, dict):
                target_row["field_provenance"][field_name] = field_provenance
            applied_at = correction.get("timestamp")
            if hasattr(applied_at, "isoformat"):
                applied_at = applied_at.isoformat()
            target_row["correction_history"].append(
                {
                    "correction_event_id": str(correction.get("id", "")),
                    "target_event_id": target_event_id,
                    "field": field_name,
                    "value": value,
                    "applied_at": applied_at,
                    "reason": data.get("reason"),
                    "repair_provenance": field_provenance,
                }
            )

    return corrected_rows
