from __future__ import annotations

from kura_workers.handlers.deep_analysis import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    build_deep_analysis_result,
)


def test_build_deep_analysis_result_marks_low_data_uncertainty() -> None:
    result = build_deep_analysis_result(
        objective="improve consistency",
        horizon_days=30,
        focus=["recovery", "sleep"],
        event_type_counts=[{"event_type": "set.logged", "count": 7}],
        quality_health=None,
    )

    assert result["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    assert result["window_days"] == 30
    assert result["focus"] == ["recovery", "sleep"]
    assert "low_data_density" in result["uncertainty"]
    assert "quality_health_projection_missing" in result["uncertainty"]
    assert result["evidence_refs"][0]["kind"] == "events.aggregate"


def test_build_deep_analysis_result_uses_event_totals_and_top_labels() -> None:
    result = build_deep_analysis_result(
        objective="optimize weekly load",
        horizon_days=90,
        focus=[],
        event_type_counts=[
            {"event_type": "set.logged", "count": 40},
            {"event_type": "session.logged", "count": 12},
        ],
        quality_health={"updated_at": "2026-03-01T00:00:00Z"},
    )

    assert "52 events" in result["summary"]
    assert result["highlights"][0]["value"] == 52
    assert not result["uncertainty"]
    assert any(
        ref.get("kind") == "projection.quality_health" for ref in result["evidence_refs"]
    )
