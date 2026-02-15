"""Tests for session_feedback projection helpers (PDC.8)."""

from datetime import datetime

from kura_workers.handlers.session_feedback import (
    _build_session_feedback_projection,
    _compute_enjoyment_trend,
    _compute_load_to_enjoyment_alignment,
    _normalize_session_feedback_payload,
)
from kura_workers.session_block_expansion import expand_session_logged_row


def _feedback_row(event_id: str, timestamp: str, data: dict, session_id: str | None = None) -> dict:
    metadata = {}
    if session_id is not None:
        metadata["session_id"] = session_id
    return {
        "id": event_id,
        "timestamp": datetime.fromisoformat(timestamp),
        "data": data,
        "metadata": metadata,
    }


def _set_row(event_id: str, timestamp: str, data: dict, session_id: str | None = None) -> dict:
    metadata = {}
    if session_id is not None:
        metadata["session_id"] = session_id
    return {
        "id": event_id,
        "timestamp": datetime.fromisoformat(timestamp),
        "data": data,
        "metadata": metadata,
    }


def test_normalize_feedback_maps_legacy_text_without_data_loss():
    normalized = _normalize_session_feedback_payload(
        {
            "summary": "Training felt good and fun.",
        }
    )
    assert normalized["context"] == "Training felt good and fun."
    assert normalized["enjoyment"] == 8.0
    assert normalized["perceived_quality"] is None


def test_normalize_feedback_respects_unresolved_state_contract():
    normalized = _normalize_session_feedback_payload(
        {
            "enjoyment": 4,
            "enjoyment_state": "unresolved",
            "enjoyment_unresolved_reason": "user did not rate enjoyment",
            "summary": "felt good",
        }
    )
    assert normalized["enjoyment"] is None
    assert normalized["enjoyment_state"] == "unresolved"
    assert normalized["enjoyment_unresolved_reason"] == "user did not rate enjoyment"


def test_normalize_feedback_confirmed_state_does_not_auto_infer():
    normalized = _normalize_session_feedback_payload(
        {
            "enjoyment_state": "confirmed",
            "summary": "felt good and fun",
        }
    )
    assert normalized["enjoyment"] is None
    assert normalized["enjoyment_state"] == "confirmed"


def test_normalize_feedback_infers_strong_negative_legacy_text():
    normalized = _normalize_session_feedback_payload(
        {
            "feeling": "Training war heute richtig schlecht und müde",
        }
    )
    assert normalized["context"] == "Training war heute richtig schlecht und müde"
    assert normalized["enjoyment"] == 2.0


def test_normalize_feedback_infers_mild_negative_legacy_text():
    normalized = _normalize_session_feedback_payload(
        {
            "summary": "Heute war ich ziemlich müde.",
        }
    )
    assert normalized["enjoyment"] == 4.0


def test_normalize_feedback_negated_negative_phrase_stays_neutral():
    normalized = _normalize_session_feedback_payload(
        {
            "summary": "Training war nicht schlecht.",
        }
    )
    assert normalized["enjoyment"] is None


def test_normalize_feedback_infers_negative_without_umlaut_variant():
    normalized = _normalize_session_feedback_payload(
        {
            "summary": "Training war heute muede und schlecht.",
        }
    )
    assert normalized["enjoyment"] == 2.0


def test_normalize_feedback_mixed_signals_remain_unresolved():
    normalized = _normalize_session_feedback_payload(
        {
            "summary": "Session was good but tired.",
        }
    )
    assert normalized["enjoyment"] is None


def test_normalize_feedback_does_not_match_partial_word_tokens():
    normalized = _normalize_session_feedback_payload(
        {
            "summary": "Badminton drills liefen ok.",
        }
    )
    assert normalized["enjoyment"] is None


def test_compute_enjoyment_trend_detects_improving():
    entries = [
        {"enjoyment": 2.0},
        {"enjoyment": 2.0},
        {"enjoyment": 2.5},
        {"enjoyment": 2.5},
        {"enjoyment": 3.5},
        {"enjoyment": 4.0},
        {"enjoyment": 4.0},
        {"enjoyment": 4.5},
    ]
    assert _compute_enjoyment_trend(entries) == "improving"


def test_load_to_enjoyment_alignment_positive_correlation():
    entries = [
        {"enjoyment": 2.0, "session_load": {"total_volume_kg": 600.0}},
        {"enjoyment": 3.0, "session_load": {"total_volume_kg": 900.0}},
        {"enjoyment": 4.0, "session_load": {"total_volume_kg": 1200.0}},
    ]
    alignment = _compute_load_to_enjoyment_alignment(entries)
    assert alignment["status"] == "positive"
    assert alignment["correlation"] is not None
    assert alignment["correlation"] > 0


def test_build_projection_includes_ingestion_output_and_trends():
    feedback_rows = [
        _feedback_row(
            "fb-1",
            "2026-02-10T18:00:00+00:00",
            {
                "summary": "Session felt good and fun",
                "perceived_exertion": 7,
            },
            session_id="s1",
        ),
        _feedback_row(
            "fb-2",
            "2026-02-11T18:00:00+00:00",
            {
                "enjoyment": 3,
                "perceived_quality": 4,
                "pain_discomfort": 1,
                "context": "Solid but a bit heavy",
            },
            session_id="s2",
        ),
    ]
    set_rows = [
        _set_row(
            "set-1",
            "2026-02-10T17:30:00+00:00",
            {"exercise_id": "squat", "weight_kg": 100, "reps": 5},
            session_id="s1",
        ),
        _set_row(
            "set-2",
            "2026-02-11T17:30:00+00:00",
            {"exercise_id": "squat", "weight_kg": 110, "reps": 5},
            session_id="s2",
        ),
    ]

    projection = _build_session_feedback_projection(feedback_rows, set_rows)
    recent = projection["recent_sessions"]

    assert projection["counts"]["sessions_with_feedback"] == 2
    assert recent[0]["session_id"] == "s1"
    assert recent[0]["enjoyment"] == 8.0  # inferred from legacy summary text
    assert recent[0]["session_load"]["total_sets"] == 1
    assert recent[1]["perceived_quality"] == 4.0
    assert projection["trends"]["enjoyment_trend"] == "insufficient_data"


def test_build_projection_accepts_session_logged_expanded_rows_for_load():
    feedback_rows = [
        _feedback_row(
            "fb-1",
            "2026-02-14T10:30:00+00:00",
            {"enjoyment": 4, "perceived_exertion": 7},
            session_id="track-1",
        )
    ]
    session_rows = expand_session_logged_row(
        {
            "id": "slog-1",
            "timestamp": datetime.fromisoformat("2026-02-14T10:00:00+00:00"),
            "metadata": {"session_id": "track-1"},
            "data": {
                "contract_version": "session.logged.v1",
                "session_meta": {"session_id": "track-1"},
                "blocks": [
                    {
                        "block_type": "interval_endurance",
                        "dose": {
                            "work": {"duration_seconds": 120},
                            "recovery": {"duration_seconds": 60},
                            "repeats": 8,
                        },
                        "intensity_anchors": [
                            {
                                "measurement_state": "measured",
                                "unit": "min_per_km",
                                "value": 4.0,
                            }
                        ],
                    }
                ],
            },
        }
    )

    projection = _build_session_feedback_projection(feedback_rows, session_rows)
    recent = projection["recent_sessions"][0]
    assert recent["session_id"] == "track-1"
    assert recent["session_load"]["total_sets"] == 8
    assert recent["session_load"]["total_reps"] == 0
