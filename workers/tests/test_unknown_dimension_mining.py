"""Unit tests for unknown-dimension mining pipeline (2zc.6)."""

from datetime import UTC, datetime

from kura_workers.unknown_dimension_mining import (
    UnknownDimensionMiningSettings,
    UnknownObservationSample,
    build_unknown_dimension_proposals,
)


def _settings() -> UnknownDimensionMiningSettings:
    return UnknownDimensionMiningSettings(
        window_days=30,
        min_support=3,
        min_unique_users=2,
        max_events_per_user_per_cluster=3,
        frequency_reference_count=10,
        reproducibility_reference_users=4,
        representative_examples=3,
        max_proposals_per_run=10,
    )


def _sample(
    *,
    event_id: str,
    pseudo_user: str,
    captured_at: datetime | None = None,
    dimension: str = "coach.note.experimental",
    dimension_seed: str = "coach_note_experimental",
    tier: str = "unknown",
    scope_level: str = "session",
    semantic_fingerprint: str = "ankle-stiffness-warmup",
    value_type: str = "number",
    value: object = 3.0,
    unit: str | None = "score",
    context_text: str = "Ankle stiffness in warmup was 3/10.",
) -> UnknownObservationSample:
    return UnknownObservationSample(
        event_id=event_id,
        captured_at=captured_at or datetime(2026, 2, 13, 10, 0, tzinfo=UTC),
        dimension=dimension,
        dimension_seed=dimension_seed,
        tier=tier,
        scope_level=scope_level,
        semantic_fingerprint=semantic_fingerprint,
        value_type=value_type,
        value=value,
        unit=unit,
        context_text=context_text,
        tags=("warmup", "ankle"),
        pseudonymized_user_id=pseudo_user,
    )


def test_build_unknown_dimension_proposals_outputs_ranked_schema_and_evidence():
    samples = [
        _sample(event_id="e1", pseudo_user="u_a", value=3.0),
        _sample(event_id="e2", pseudo_user="u_b", value=4.0),
        _sample(event_id="e3", pseudo_user="u_a", value=2.5),
        _sample(event_id="e4", pseudo_user="u_b", value=3.5),
    ]
    proposals, stats = build_unknown_dimension_proposals(samples, settings=_settings())

    assert len(proposals) == 1
    assert stats["proposals_generated"] == 1
    proposal = proposals[0]
    assert proposal["proposal_score"] > 0.0
    assert proposal["confidence"] > 0.0
    schema = proposal["suggested_dimension"]
    assert schema["name"]
    assert schema["value_type"] in {"number", "mixed"}
    assert schema["expected_scale"]["min"] <= schema["expected_scale"]["max"]
    evidence = proposal["evidence_bundle"]
    assert evidence["event_count"] == 4
    assert evidence["unique_users"] == 2
    assert len(evidence["sample_utterances"]) >= 1
    payload = proposal["proposal_payload"]
    assert payload["approval_required"] is True
    assert payload["approval_workflow"]["route_on_accept"]["source_type"] == "unknown_dimension"


def test_build_unknown_dimension_proposals_filters_noise_thresholds():
    samples = [
        _sample(event_id="e1", pseudo_user="u_a"),
        _sample(event_id="e2", pseudo_user="u_a"),
    ]
    proposals, stats = build_unknown_dimension_proposals(samples, settings=_settings())

    assert proposals == []
    assert stats["filtered_min_support"] >= 1


def test_build_unknown_dimension_proposals_is_deterministic_for_input_order():
    samples = [
        _sample(event_id="e1", pseudo_user="u_a"),
        _sample(event_id="e2", pseudo_user="u_b"),
        _sample(event_id="e3", pseudo_user="u_a"),
        _sample(event_id="e4", pseudo_user="u_b"),
    ]
    settings = _settings()
    proposals_a, stats_a = build_unknown_dimension_proposals(samples, settings=settings)
    proposals_b, stats_b = build_unknown_dimension_proposals(
        list(reversed(samples)),
        settings=settings,
    )
    assert proposals_a == proposals_b
    assert stats_a == stats_b


def test_build_unknown_dimension_proposals_caps_per_user_contributions() -> None:
    settings = UnknownDimensionMiningSettings(
        window_days=30,
        min_support=3,
        min_unique_users=2,
        max_events_per_user_per_cluster=2,
        frequency_reference_count=10,
        reproducibility_reference_users=4,
        representative_examples=3,
        max_proposals_per_run=10,
    )
    samples = [
        _sample(event_id="c1", pseudo_user="u_a", value=3.0),
        _sample(event_id="c2", pseudo_user="u_a", value=3.1),
        _sample(event_id="c3", pseudo_user="u_a", value=3.2),
        _sample(event_id="c4", pseudo_user="u_a", value=3.3),
        _sample(event_id="c5", pseudo_user="u_b", value=4.0),
        _sample(event_id="c6", pseudo_user="u_b", value=4.1),
    ]

    proposals, stats = build_unknown_dimension_proposals(samples, settings=settings)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["event_count"] == 4
    controls = proposal["proposal_payload"]["false_positive_controls"]
    assert controls["max_events_per_user_per_cluster"] == 2
    assert controls["dominance_dropped_events"] == 2
    assert stats["dominance_dropped_events"] == 2
