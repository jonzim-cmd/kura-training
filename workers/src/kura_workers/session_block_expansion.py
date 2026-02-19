"""Utilities to expand session.logged block payloads into projection-friendly rows."""

from __future__ import annotations

from typing import Any

_MEASURED_STATES = {"measured", "estimated", "inferred"}
_RPE_UNITS = {"rpe", "lifting_rpe", "borg_cr10", "borg_6_20"}
_WEIGHT_KEYS = (
    "weight_kg",
    "load_kg",
    "external_load_kg",
    "resistance_kg",
)


def _to_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _to_positive_int(value: Any, *, fallback: int = 1) -> int:
    parsed = _to_float(value)
    if parsed is None:
        return fallback
    value_int = int(round(parsed))
    if value_int < 1:
        return fallback
    return value_int


def _measurement_numeric(entry: Any) -> float | None:
    if not isinstance(entry, dict):
        return None
    state = str(entry.get("measurement_state") or "").strip().lower()
    if state and state not in _MEASURED_STATES:
        return None
    return _to_float(entry.get("value"))


def _extract_weight_kg(block: dict[str, Any]) -> float | None:
    metrics = block.get("metrics")
    if not isinstance(metrics, dict):
        return None

    for key in _WEIGHT_KEYS:
        value = _measurement_numeric(metrics.get(key))
        if value is not None and value >= 0:
            return value
    return None


def _extract_rpe_anchor(block: dict[str, Any]) -> float | None:
    anchors = block.get("intensity_anchors")
    if not isinstance(anchors, list):
        return None

    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        unit = str(anchor.get("unit") or "").strip().lower()
        if unit not in _RPE_UNITS:
            continue
        value = _measurement_numeric(anchor)
        if value is not None:
            return value
    return None


def _extract_relative_intensity(block: dict[str, Any]) -> dict[str, Any] | None:
    raw = block.get("relative_intensity")
    if not isinstance(raw, dict):
        return None
    value = _to_float(raw.get("value_pct"))
    if value is None or value <= 0:
        return None
    result: dict[str, Any] = {"value_pct": value}
    reference_type = str(raw.get("reference_type") or "").strip().lower()
    if reference_type:
        result["reference_type"] = reference_type
    reference_value = _to_float(raw.get("reference_value"))
    if reference_value is not None and reference_value > 0:
        result["reference_value"] = reference_value
    reference_measured_at = str(raw.get("reference_measured_at") or "").strip()
    if reference_measured_at:
        result["reference_measured_at"] = reference_measured_at
    reference_confidence = _to_float(raw.get("reference_confidence"))
    if reference_confidence is not None:
        result["reference_confidence"] = reference_confidence
    return result


def expand_session_logged_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand one session.logged row into repeat-level synthetic rows.

    The expanded rows preserve timestamp and session metadata and are compatible
    with existing set-oriented projections.
    """
    data = row.get("effective_data") or row.get("data") or {}
    if not isinstance(data, dict):
        return []

    blocks = data.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return []

    session_meta = data.get("session_meta")
    if not isinstance(session_meta, dict):
        session_meta = {}

    base_metadata = dict(row.get("metadata") or {})
    if not base_metadata.get("session_id"):
        session_id = str(session_meta.get("session_id") or "").strip()
        if session_id:
            base_metadata["session_id"] = session_id

    expanded: list[dict[str, Any]] = []
    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue

        block_type = str(block.get("block_type") or "").strip().lower()
        if not block_type:
            block_type = "session_block"

        dose = block.get("dose")
        if not isinstance(dose, dict):
            dose = {}
        work = dose.get("work")
        if not isinstance(work, dict):
            work = {}
        recovery = dose.get("recovery")
        if not isinstance(recovery, dict):
            recovery = {}

        repeats = _to_positive_int(dose.get("repeats"), fallback=1)
        reps_per_repeat = _to_positive_int(work.get("reps"), fallback=0)
        contacts_per_repeat = _to_positive_int(work.get("contacts"), fallback=0)

        duration_seconds = _to_float(work.get("duration_seconds"))
        distance_meters = _to_float(work.get("distance_meters"))
        rest_seconds = _to_float(recovery.get("duration_seconds"))
        weight_kg = _extract_weight_kg(block)
        rpe = _extract_rpe_anchor(block)
        relative_intensity = _extract_relative_intensity(block)

        capability_target = str(block.get("capability_target") or "").strip().lower()
        if not capability_target:
            capability_target = None

        reps_value = reps_per_repeat if reps_per_repeat > 0 else contacts_per_repeat

        for repeat_index in range(repeats):
            synthetic_data: dict[str, Any] = {
                "exercise": block_type,
                "exercise_id": block_type,
                "block_type": block_type,
                "reps": reps_value,
            }
            if contacts_per_repeat > 0:
                synthetic_data["contacts"] = contacts_per_repeat
            if duration_seconds is not None and duration_seconds >= 0:
                synthetic_data["duration_seconds"] = duration_seconds
            if distance_meters is not None and distance_meters >= 0:
                synthetic_data["distance_meters"] = distance_meters
            if rest_seconds is not None and rest_seconds >= 0:
                synthetic_data["rest_seconds"] = rest_seconds
            if weight_kg is not None and weight_kg >= 0:
                synthetic_data["weight_kg"] = weight_kg
            if rpe is not None:
                synthetic_data["rpe"] = rpe
            if relative_intensity is not None:
                synthetic_data["relative_intensity"] = dict(relative_intensity)
            if capability_target is not None:
                synthetic_data["capability_target"] = capability_target

            metadata = dict(base_metadata)
            metadata["session_block_index"] = block_index
            metadata["session_block_repeat"] = repeat_index + 1

            expanded.append(
                {
                    "id": row.get("id"),
                    "timestamp": row["timestamp"],
                    "data": synthetic_data,
                    "metadata": metadata,
                    "_source_type": "session_logged",
                    "_source_event_type": "session.logged",
                }
            )

    return expanded


def expand_session_logged_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for row in rows:
        expanded.extend(expand_session_logged_row(row))
    return expanded
