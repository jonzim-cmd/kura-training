"""Tests for repair provenance contract helpers (PDC.9)."""

from kura_workers.repair_provenance import (
    build_repair_provenance,
    summarize_repair_provenance,
)


def test_build_repair_provenance_explicit_path():
    provenance = build_repair_provenance(
        source_type="explicit",
        confidence=1.0,
        applies_scope="single_set",
        reason="User explicitly confirmed correction.",
    )
    assert provenance["source_type"] == "explicit"
    assert provenance["confidence_band"] == "high"
    assert provenance["applies_scope"] == "single_set"


def test_build_repair_provenance_inferred_path():
    provenance = build_repair_provenance(
        source_type="inferred",
        confidence=0.8,
        applies_scope="exercise_session",
        reason="Context-derived from deterministic mention mapping.",
    )
    assert provenance["source_type"] == "inferred"
    assert provenance["confidence_band"] == "medium"
    assert provenance["applies_scope"] == "exercise_session"


def test_build_repair_provenance_user_confirmed_path():
    provenance = build_repair_provenance(
        source_type="user_confirmed",
        confidence=0.95,
        applies_scope="session",
        reason="User confirmed repair in follow-up question.",
    )
    assert provenance["source_type"] == "user_confirmed"
    assert provenance["confidence_band"] == "high"
    assert provenance["applies_scope"] == "session"


def test_summarize_repair_provenance_counts_bands_and_sources():
    summary = summarize_repair_provenance(
        [
            build_repair_provenance(
                source_type="explicit",
                confidence=1.0,
                applies_scope="single_set",
                reason="A",
            ),
            build_repair_provenance(
                source_type="estimated",
                confidence=0.45,
                applies_scope="session",
                reason="B",
            ),
        ]
    )
    assert summary["entries"] == 2
    assert summary["by_source_type"]["explicit"] == 1
    assert summary["by_source_type"]["estimated"] == 1
    assert summary["by_confidence_band"]["high"] == 1
    assert summary["by_confidence_band"]["low"] == 1
    assert summary["low_confidence_entries"] == 1

