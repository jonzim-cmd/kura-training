"""Unit tests for learning-to-backlog bridge (2zc.3)."""

from kura_workers.learning_backlog_bridge import (
    LearningBacklogBridgeSettings,
    build_backlog_candidates,
)


def _settings() -> LearningBacklogBridgeSettings:
    return LearningBacklogBridgeSettings(
        cluster_min_score=0.18,
        cluster_min_events=3,
        cluster_min_unique_users=2,
        calibration_min_sample_count=3,
        max_candidates_per_source=3,
        max_candidates_per_run=6,
    )


def _cluster_row(
    *,
    signature: str = "sig_a",
    score: float = 0.44,
    event_count: int = 5,
    unique_users: int = 3,
) -> dict:
    return {
        "period_key": "2026-W07",
        "cluster_signature": signature,
        "score": score,
        "event_count": event_count,
        "unique_users": unique_users,
        "cluster_data": {
            "signature": {"signal_type_top": "save_claim_mismatch_attempt"},
            "score_factors": {
                "frequency": 0.8,
                "severity": 0.7,
                "impact": 0.9,
                "reproducibility": 0.75,
            },
            "affected_workflow_phases": ["agent_write_with_proof"],
            "representative_examples": [
                {"invariant_id": "INV-002"},
                {"invariant_id": "INV-008"},
            ],
        },
    }


def _underperforming_row(
    *,
    claim_class: str = "set_context.rest_seconds",
    parser_version: str = "mention_parser.v1",
    status: str = "underperforming",
    brier_score: float = 0.34,
    precision_high_conf: float | None = 0.5,
    sample_count: int = 9,
) -> dict:
    return {
        "period_key": "2026-W07",
        "claim_class": claim_class,
        "parser_version": parser_version,
        "status": status,
        "brier_score": brier_score,
        "precision_high_conf": precision_high_conf,
        "sample_count": sample_count,
        "details": {"drift_status": "drift_alert"},
    }


def test_build_backlog_candidates_generates_machine_readable_payloads():
    candidates, stats = build_backlog_candidates(
        cluster_rows=[_cluster_row()],
        underperforming_rows=[_underperforming_row()],
        settings=_settings(),
    )

    assert len(candidates) == 2
    assert stats["cluster_candidates"] == 1
    assert stats["calibration_candidates"] == 1
    for candidate in candidates:
        assert candidate["approval_required"] is True
        payload = candidate["issue_payload"]
        assert payload["schema_version"] == 1
        assert payload["approval_required"] is True
        assert payload["title"]
        assert payload["description"]
        assert payload["root_cause_hypothesis"]
        assert payload["impacted_metrics"]
        assert payload["suggested_updates"]
        checklist = payload["promotion_checklist"]
        assert checklist["workflow"] == "cluster_to_issue_to_regression_v1"
        assert checklist["auto_total_steps"] >= checklist["auto_completed_steps"]


def test_build_backlog_candidates_applies_noise_filters_and_caps():
    cluster_rows = [
        _cluster_row(signature="sig_filtered", score=0.05),  # filtered by score
        _cluster_row(signature="sig_1", score=0.30),
        _cluster_row(signature="sig_2", score=0.31),
        _cluster_row(signature="sig_3", score=0.32),
        _cluster_row(signature="sig_4", score=0.33),  # capped by source limit
    ]
    underperforming_rows = [
        _underperforming_row(claim_class="x.low_samples", sample_count=1),  # filtered
        _underperforming_row(claim_class="x.a"),
    ]
    settings = LearningBacklogBridgeSettings(
        cluster_min_score=0.18,
        cluster_min_events=3,
        cluster_min_unique_users=2,
        calibration_min_sample_count=3,
        max_candidates_per_source=3,
        max_candidates_per_run=4,
    )
    candidates, stats = build_backlog_candidates(
        cluster_rows=cluster_rows,
        underperforming_rows=underperforming_rows,
        settings=settings,
    )

    assert len(candidates) == 4
    assert stats["filtered_noise"] == 2
    assert stats["limited_by_source"] >= 1
    source_counts = {}
    for candidate in candidates:
        source = candidate["source_type"]
        source_counts[source] = source_counts.get(source, 0) + 1
    assert source_counts["issue_cluster"] <= 3


def test_build_backlog_candidates_is_deterministic_for_input_order():
    cluster_rows = [_cluster_row(signature="sig_a"), _cluster_row(signature="sig_b")]
    underperforming_rows = [
        _underperforming_row(claim_class="set_context.rest_seconds"),
        _underperforming_row(claim_class="set_context.tempo"),
    ]
    settings = _settings()

    candidates_a, stats_a = build_backlog_candidates(
        cluster_rows=cluster_rows,
        underperforming_rows=underperforming_rows,
        settings=settings,
    )
    candidates_b, stats_b = build_backlog_candidates(
        cluster_rows=list(reversed(cluster_rows)),
        underperforming_rows=list(reversed(underperforming_rows)),
        settings=settings,
    )

    assert candidates_a == candidates_b
    assert stats_a == stats_b
