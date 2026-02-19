"""Shared normalization for training-related signal rows.

Unifies set correction overlays, session block expansion, and legacy backfill
deduplication so inference/replay paths can consume one consistent signal view.
"""

from __future__ import annotations

from typing import Any

from .session_block_expansion import expand_session_logged_rows
from .set_corrections import apply_set_correction_chain
from .training_legacy_compat import extract_backfilled_set_event_ids


def normalize_training_signal_rows(
    rows: list[dict[str, Any]],
    *,
    include_passthrough: bool = True,
) -> list[dict[str, Any]]:
    set_rows: list[dict[str, Any]] = []
    session_rows: list[dict[str, Any]] = []
    correction_rows: list[dict[str, Any]] = []
    passthrough_rows: list[dict[str, Any]] = []

    for row in rows:
        event_type = str(row.get("event_type") or "").strip()
        if event_type == "set.logged":
            set_rows.append(row)
        elif event_type == "session.logged":
            session_rows.append(row)
        elif event_type == "set.corrected":
            correction_rows.append(row)
        elif include_passthrough:
            passthrough_rows.append(row)

    legacy_backfilled_set_ids = extract_backfilled_set_event_ids(session_rows)
    if legacy_backfilled_set_ids:
        set_rows = [
            row for row in set_rows if str(row.get("id") or "") not in legacy_backfilled_set_ids
        ]

    corrected_set_rows = apply_set_correction_chain(set_rows, correction_rows)
    normalized: list[dict[str, Any]] = []

    for row in corrected_set_rows:
        normalized.append(
            {
                **row,
                "event_type": "set.logged",
                "data": row.get("effective_data") or row.get("data") or {},
            }
        )

    for row in expand_session_logged_rows(session_rows):
        normalized.append(
            {
                **row,
                "event_type": "session.logged",
                "data": row.get("effective_data") or row.get("data") or {},
            }
        )

    normalized.extend(passthrough_rows)
    normalized.sort(
        key=lambda row: (
            row.get("timestamp"),
            str(row.get("id") or ""),
        )
    )
    return normalized

