from __future__ import annotations

from datetime import datetime

from kura_workers.session_block_expansion import (
    expand_session_logged_row,
    expand_session_logged_rows,
)


def _session_row(*, session_id_in_meta: bool = False) -> dict:
    metadata = {}
    if session_id_in_meta:
        metadata["session_id"] = "meta-session"
    return {
        "id": "evt-1",
        "timestamp": datetime.fromisoformat("2026-02-14T10:00:00+00:00"),
        "metadata": metadata,
        "data": {
            "contract_version": "session.logged.v1",
            "session_meta": {
                "sport": "hybrid",
                "session_id": "session-from-meta",
            },
            "blocks": [
                {
                    "block_type": "interval_endurance",
                    "dose": {
                        "work": {"duration_seconds": 120, "distance_meters": 500},
                        "recovery": {"duration_seconds": 60},
                        "repeats": 3,
                    },
                    "intensity_anchors": [
                        {
                            "measurement_state": "measured",
                            "unit": "borg_cr10",
                            "value": 7,
                        }
                    ],
                    "relative_intensity": {
                        "value_pct": 92.0,
                        "reference_type": "critical_speed",
                        "reference_value": 4.3,
                        "reference_measured_at": "2026-02-10T08:00:00+00:00",
                        "reference_confidence": 0.78,
                    },
                },
                {
                    "block_type": "strength_set",
                    "dose": {
                        "work": {"reps": 5},
                        "recovery": {"duration_seconds": 120},
                        "repeats": 2,
                    },
                    "metrics": {
                        "weight_kg": {
                            "measurement_state": "measured",
                            "value": 100,
                            "unit": "kg",
                        }
                    },
                    "intensity_anchors": [
                        {
                            "measurement_state": "measured",
                            "unit": "rpe",
                            "value": 8,
                        }
                    ],
                },
            ],
        },
    }


def test_expand_session_logged_row_expands_repeats_to_rows() -> None:
    expanded = expand_session_logged_row(_session_row())
    assert len(expanded) == 5

    first = expanded[0]
    assert first["data"]["exercise_id"] == "interval_endurance"
    assert first["data"]["reps"] == 0
    assert first["data"]["duration_seconds"] == 120.0
    assert first["data"]["distance_meters"] == 500.0
    assert first["data"]["rest_seconds"] == 60.0
    assert first["data"]["rpe"] == 7.0
    assert first["data"]["relative_intensity"]["value_pct"] == 92.0
    assert first["data"]["relative_intensity"]["reference_type"] == "critical_speed"
    assert first["metadata"]["session_id"] == "session-from-meta"


def test_expand_session_logged_row_prefers_existing_metadata_session_id() -> None:
    expanded = expand_session_logged_row(_session_row(session_id_in_meta=True))
    assert expanded
    assert expanded[0]["metadata"]["session_id"] == "meta-session"


def test_expand_session_logged_row_extracts_strength_weight_and_rpe() -> None:
    expanded = expand_session_logged_row(_session_row())
    strength_rows = [row for row in expanded if row["data"]["exercise_id"] == "strength_set"]
    assert len(strength_rows) == 2
    assert strength_rows[0]["data"]["reps"] == 5
    assert strength_rows[0]["data"]["weight_kg"] == 100.0
    assert strength_rows[0]["data"]["rpe"] == 8.0


def test_expand_session_logged_rows_aggregates_multiple_events() -> None:
    rows = [_session_row(), _session_row()]
    expanded = expand_session_logged_rows(rows)
    assert len(expanded) == 10


def test_expand_session_logged_row_handles_missing_blocks() -> None:
    row = {
        "id": "evt-2",
        "timestamp": datetime.fromisoformat("2026-02-14T10:00:00+00:00"),
        "data": {"contract_version": "session.logged.v1", "session_meta": {}},
        "metadata": {},
    }
    assert expand_session_logged_row(row) == []


def test_expand_session_logged_row_supports_legacy_intensity_percent_max_field() -> None:
    row = {
        "id": "evt-legacy-intensity",
        "timestamp": datetime.fromisoformat("2026-02-14T10:00:00+00:00"),
        "metadata": {},
        "data": {
            "contract_version": "session.logged.v1",
            "session_meta": {"sport": "sprint"},
            "blocks": [
                {
                    "block_type": "sprint_accel_maxv",
                    "dose": {
                        "work": {"distance_meters": 30, "repeats": 4},
                        "recovery": {"duration_seconds": 90},
                    },
                    "intensity_percent_max": 0.8,
                }
            ],
        },
    }

    expanded = expand_session_logged_row(row)
    assert expanded
    relative = expanded[0]["data"].get("relative_intensity")
    assert isinstance(relative, dict)
    assert relative["value_pct"] == 0.8
    assert relative["reference_type"] == "custom"
