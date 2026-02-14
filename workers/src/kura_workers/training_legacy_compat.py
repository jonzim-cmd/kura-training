"""Compatibility adapter between legacy set.logged and session.logged v1."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from .session_block_expansion import expand_session_logged_row
from .training_session_contract import CONTRACT_VERSION_V1, validate_session_logged_payload

LEGACY_COMPAT_VERSION = "set_session_compat.v1"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _anchor_from_legacy_set(set_data: dict[str, Any]) -> list[dict[str, Any]]:
    rpe = _to_float(set_data.get("rpe"))
    if rpe is not None:
        return [
            {
                "measurement_state": "measured",
                "unit": "rpe",
                "value": rpe,
            }
        ]
    return []


def set_logged_to_session_logged_payload(
    *,
    set_data: dict[str, Any],
    set_timestamp: datetime | None = None,
    session_id: str | None = None,
    timezone: str = "UTC",
) -> dict[str, Any]:
    reps = _to_float(set_data.get("reps"))
    weight_kg = _to_float(set_data.get("weight_kg", set_data.get("weight")))
    rest_seconds = _to_float(set_data.get("rest_seconds"))
    duration_seconds = _to_float(set_data.get("duration_seconds"))
    distance_meters = _to_float(set_data.get("distance_meters"))
    contacts = _to_float(set_data.get("contacts"))

    work: dict[str, Any] = {}
    if reps is not None and reps >= 0:
        work["reps"] = int(round(reps))
    if duration_seconds is not None and duration_seconds >= 0:
        work["duration_seconds"] = duration_seconds
    if distance_meters is not None and distance_meters >= 0:
        work["distance_meters"] = distance_meters
    if contacts is not None and contacts >= 0:
        work["contacts"] = int(round(contacts))
    if not work:
        work["reps"] = 1

    recovery: dict[str, Any] | None = None
    if rest_seconds is not None and rest_seconds >= 0:
        recovery = {"duration_seconds": rest_seconds}

    anchors = _anchor_from_legacy_set(set_data)
    metrics: dict[str, Any] = {}
    if weight_kg is not None and weight_kg >= 0:
        metrics["weight_kg"] = {
            "measurement_state": "measured",
            "unit": "kg",
            "value": weight_kg,
        }
    else:
        metrics["weight_kg"] = {"measurement_state": "not_measured"}

    block: dict[str, Any] = {
        "block_type": "strength_set",
        "dose": {
            "work": work,
            "repeats": 1,
        },
        "metrics": metrics,
    }
    if recovery is not None:
        block["dose"]["recovery"] = recovery
    if anchors:
        block["intensity_anchors"] = anchors
    else:
        block["intensity_anchors_status"] = "not_applicable"

    timestamp = set_timestamp or datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION_V1,
        "session_meta": {
            "sport": "strength",
            "started_at": timestamp.astimezone(UTC).isoformat(),
            "timezone": timezone,
        },
        "blocks": [block],
        "provenance": {
            "source_type": "imported",
            "source_ref": LEGACY_COMPAT_VERSION,
        },
    }
    if session_id:
        payload["session_meta"]["session_id"] = session_id
    validate_session_logged_payload(payload)
    return payload


def session_logged_to_legacy_set_rows(
    payload: dict[str, Any],
    *,
    timestamp: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    row = {
        "id": "compat-row",
        "timestamp": timestamp or datetime.now(tz=UTC),
        "data": payload,
        "metadata": metadata or {},
    }
    expanded = expand_session_logged_row(row)
    return [entry["data"] for entry in expanded]


def legacy_backfill_idempotency_key(legacy_event_id: str) -> str:
    stable = legacy_event_id.strip()
    digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]
    return f"legacy-session-backfill-{digest}"


def build_set_to_session_backfill_plan(
    *,
    set_events: list[dict[str, Any]],
    already_backfilled_set_event_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    backfilled = already_backfilled_set_event_ids or set()
    plan: list[dict[str, Any]] = []

    for row in set_events:
        legacy_event_id = str(row.get("id") or "").strip()
        if not legacy_event_id or legacy_event_id in backfilled:
            continue
        data = row.get("effective_data") or row.get("data") or {}
        if not isinstance(data, dict):
            continue
        timestamp = row.get("timestamp")
        session_id = str((row.get("metadata") or {}).get("session_id") or "").strip() or None

        payload = set_logged_to_session_logged_payload(
            set_data=data,
            set_timestamp=timestamp if isinstance(timestamp, datetime) else None,
            session_id=session_id,
        )
        plan.append(
            {
                "source_event_id": legacy_event_id,
                "event_type": "session.logged",
                "data": payload,
                "metadata": {
                    "compat_mode": "legacy_set_backfill",
                    "legacy_event_id": legacy_event_id,
                    "idempotency_key": legacy_backfill_idempotency_key(legacy_event_id),
                },
            }
        )
    return plan


def extract_backfilled_set_event_ids(session_rows: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for row in session_rows:
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        legacy_event_id = str(metadata.get("legacy_event_id") or "").strip()
        compat_mode = str(metadata.get("compat_mode") or "").strip()
        if legacy_event_id and compat_mode == "legacy_set_backfill":
            result.add(legacy_event_id)
    return result


def legacy_compat_contract_v1() -> dict[str, Any]:
    return {
        "version": LEGACY_COMPAT_VERSION,
        "adapter_paths": {
            "set_logged_to_session_logged": "deterministic",
            "session_logged_to_set_rows": "deterministic",
        },
        "migration_strategy": {
            "mode": "append_only_backfill",
            "idempotency_key_prefix": "legacy-session-backfill-",
            "double_count_prevention": "ignore set.logged rows that are represented by legacy backfill session rows",
            "replay_safe": True,
        },
        "coexistence_policy": {
            "allow_parallel_event_types": True,
            "legacy_views_supported": True,
        },
    }
