"""Tests for open_observations projection helpers (PDC.21)."""

from datetime import datetime

from kura_workers.handlers.open_observations import (
    _build_dimension_projection,
    _normalize_observation_entry,
)


def _row(event_id: str, data: dict) -> dict:
    return {
        "id": event_id,
        "timestamp": datetime.fromisoformat("2026-02-12T10:00:00+00:00"),
        "data": data,
    }


def test_normalize_known_dimension_motivation_pre():
    normalized = _normalize_observation_entry(
        _row(
            "obs-1",
            {
                "dimension": "motivation_pre",
                "value": 4,
                "scale": {"min": 1, "max": 5},
                "context_text": "Motivation bei 4 von 5",
                "confidence": 0.92,
                "provenance": {"source_type": "explicit"},
                "scope": {"level": "session", "session_id": "sess-1"},
            },
        )
    )

    assert normalized is not None
    dimension, entry = normalized
    assert dimension == "motivation_pre"
    assert entry["tier"] == "known"
    assert entry["value"] == 4.0
    assert entry["context_text"] == "Motivation bei 4 von 5"
    assert entry["confidence"] == 0.92
    assert entry["quality_flags"] == []


def test_normalize_known_dimension_jump_baseline_converts_meters_to_cm():
    normalized = _normalize_observation_entry(
        _row(
            "obs-2",
            {
                "dimension": "jump_baseline",
                "value": 0.48,
                "unit": "m",
                "context_text": "Countermovement jump 0.48m",
                "confidence": 0.77,
                "provenance": {"source_type": "inferred"},
                "scope": {"level": "exercise", "exercise_id": "box_jump"},
            },
        )
    )

    assert normalized is not None
    _, entry = normalized
    assert entry["tier"] == "known"
    assert entry["value"] == 48.0
    assert entry["unit"] == "cm"
    assert "unit_converted_to_cm" in entry["quality_flags"]


def test_unknown_dimension_is_stored_with_quality_flags_and_context():
    normalized = _normalize_observation_entry(
        _row(
            "obs-3",
            {
                "dimension": "coach.note.experimental",
                "value": {"text": "ankle stiffness seen"},
                "context_text": "Coach note: ankle stiffness in warmup.",
                "confidence": 1.4,
                "tags": ["Coach", "Warmup", "coach"],
            },
        )
    )

    assert normalized is not None
    dimension, entry = normalized
    assert dimension == "coach.note.experimental"
    assert entry["tier"] == "unknown"
    assert entry["context_text"] == "Coach note: ankle stiffness in warmup."
    assert entry["tags"] == ["coach", "warmup"]
    assert entry["confidence"] == 1.0
    assert "unknown_dimension" in entry["quality_flags"]
    assert "confidence_clamped_high" in entry["quality_flags"]
    assert "missing_provenance" in entry["quality_flags"]


def test_projection_builder_keeps_latest_and_quality_counts():
    entries = [
        {
            "event_id": "obs-1",
            "timestamp": "2026-02-12T10:00:00+00:00",
            "dimension": "discomfort_signal",
            "tier": "known",
            "value": 2.0,
            "unit": None,
            "scale": None,
            "context_text": "Leichtes Ziehen",
            "tags": [],
            "confidence": 0.7,
            "provenance": {"source_type": "explicit"},
            "scope": {"level": "session"},
            "quality_flags": ["scale_out_of_bounds"],
        },
        {
            "event_id": "obs-2",
            "timestamp": "2026-02-12T11:00:00+00:00",
            "dimension": "discomfort_signal",
            "tier": "known",
            "value": 4.0,
            "unit": None,
            "scale": None,
            "context_text": "Nach Belastung stärker",
            "tags": [],
            "confidence": 0.82,
            "provenance": {"source_type": "explicit"},
            "scope": {"level": "session"},
            "quality_flags": [],
        },
    ]

    projection = _build_dimension_projection("discomfort_signal", entries)
    assert projection["dimension"] == "discomfort_signal"
    assert projection["summary"]["total_entries"] == 2
    assert projection["summary"]["latest_value"] == 4.0
    assert projection["summary"]["latest_context_text"] == "Nach Belastung stärker"
    assert projection["summary"]["quality_flags_count"]["scale_out_of_bounds"] == 1
