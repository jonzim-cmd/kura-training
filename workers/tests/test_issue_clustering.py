"""Unit tests for cross-user learning issue clustering (2zc.2)."""

from datetime import UTC, datetime

from kura_workers.issue_clustering import (
    IssueClusterSettings,
    LearningSignalSample,
    build_issue_clusters,
    compute_priority_score,
)


def _sample(
    *,
    event_id: str,
    user_ref: str,
    cluster_signature: str = "ls_shared_signature",
    signal_type: str = "save_claim_mismatch_attempt",
    category: str = "friction_signal",
    confidence_band: str = "high",
    workflow_phase: str = "agent_write_with_proof",
    issue_type: str = "save_claim_mismatch_attempt",
    invariant_id: str = "INV-002",
    captured_at: datetime | None = None,
) -> LearningSignalSample:
    return LearningSignalSample(
        event_id=event_id,
        captured_at=captured_at or datetime(2026, 2, 12, 10, 0, tzinfo=UTC),
        cluster_signature=cluster_signature,
        signal_type=signal_type,
        category=category,
        confidence_band=confidence_band,
        issue_type=issue_type,
        invariant_id=invariant_id,
        workflow_phase=workflow_phase,
        agent_version="api_agent_v1",
        modality="chat",
        pseudonymized_user_id=user_ref,
        attributes={"example": True},
    )


def _settings() -> IssueClusterSettings:
    return IssueClusterSettings(
        window_days=30,
        min_support=3,
        min_unique_users=2,
        max_events_per_user_per_bucket=3,
        include_low_confidence=False,
        frequency_reference_count=12,
        reproducibility_reference_users=4,
        representative_examples=2,
    )


def test_compute_priority_score_uses_factor_product():
    factors = compute_priority_score(
        event_count=12,
        unique_users=4,
        severity=0.8,
        impact=0.7,
        frequency_reference_count=12,
        reproducibility_reference_users=4,
    )
    assert factors["frequency"] == 1.0
    assert factors["reproducibility"] == 1.0
    assert abs(factors["score"] - 0.56) < 1e-9


def test_build_issue_clusters_is_deterministic_for_input_order():
    samples = [
        _sample(event_id="a", user_ref="u_a"),
        _sample(event_id="b", user_ref="u_b", confidence_band="medium"),
        _sample(
            event_id="c",
            user_ref="u_a",
            signal_type="workflow_violation",
            workflow_phase="quality_health_evaluation",
            issue_type="workflow_violation",
            invariant_id="INV-004",
        ),
    ]
    settings = _settings()

    rows_a, stats_a = build_issue_clusters(samples, settings=settings)
    rows_b, stats_b = build_issue_clusters(list(reversed(samples)), settings=settings)

    assert rows_a == rows_b
    assert stats_a == stats_b
    assert len(rows_a) == 2  # day + week
    assert {row["period_granularity"] for row in rows_a} == {"day", "week"}
    assert all(row["event_count"] == 3 for row in rows_a)
    assert all(row["unique_users"] == 2 for row in rows_a)


def test_build_issue_clusters_filters_low_support_and_unique_users():
    samples = [
        _sample(event_id="x1", user_ref="u_only"),
        _sample(event_id="x2", user_ref="u_only", signal_type="quality_issue_detected"),
    ]
    rows, stats = build_issue_clusters(samples, settings=_settings())

    assert rows == []
    assert stats["clusters_written"] == 0
    assert stats["filtered_min_support"] == 2  # day + week bucket


def test_cluster_output_contains_summary_examples_and_workflow_phases():
    samples = [
        _sample(event_id="k1", user_ref="u_a", workflow_phase="agent_write_with_proof"),
        _sample(event_id="k2", user_ref="u_b", workflow_phase="quality_health_evaluation"),
        _sample(event_id="k3", user_ref="u_c", workflow_phase="quality_health_evaluation"),
    ]
    rows, _ = build_issue_clusters(samples, settings=_settings())
    day_row = next(row for row in rows if row["period_granularity"] == "day")
    data = day_row["cluster_data"]

    assert "recurred" in data["summary"]
    assert len(data["representative_examples"]) == 2
    assert data["affected_workflow_phases"][0] == "quality_health_evaluation"
    assert data["score_factors"]["formula"] == (
        "frequency * severity * impact * reproducibility"
    )


def test_build_issue_clusters_caps_per_user_contributions() -> None:
    settings = _settings()
    settings = IssueClusterSettings(
        window_days=settings.window_days,
        min_support=settings.min_support,
        min_unique_users=settings.min_unique_users,
        max_events_per_user_per_bucket=2,
        include_low_confidence=settings.include_low_confidence,
        frequency_reference_count=settings.frequency_reference_count,
        reproducibility_reference_users=settings.reproducibility_reference_users,
        representative_examples=settings.representative_examples,
    )
    samples = [
        _sample(event_id="d1", user_ref="u_a"),
        _sample(event_id="d2", user_ref="u_a"),
        _sample(event_id="d3", user_ref="u_a"),
        _sample(event_id="d4", user_ref="u_a"),
        _sample(event_id="d5", user_ref="u_b"),
        _sample(event_id="d6", user_ref="u_b"),
    ]

    rows, stats = build_issue_clusters(samples, settings=settings)

    assert rows, "day/week clusters should still be emitted"
    assert stats["dominance_dropped_events"] == 4  # two dropped per bucket, day+week
    assert all(row["event_count"] == 4 for row in rows)
    for row in rows:
        controls = row["cluster_data"]["false_positive_controls"]
        assert controls["max_events_per_user_per_bucket"] == 2
        assert controls["dominance_dropped_events"] == 2
