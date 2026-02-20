from kura_workers.capability_estimation_runtime import (
    STATUS_INSUFFICIENT_DATA,
    build_capability_envelope,
    build_insufficient_envelope,
    confidence_from_evidence,
    data_sufficiency_block,
    effort_adjusted_e1rm,
    effective_reps_to_failure,
    interval_around,
    summarize_observations,
)


def test_effective_reps_prefers_explicit_rir() -> None:
    effective_reps, source = effective_reps_to_failure(3, rir=3, rpe=9.5)
    assert effective_reps == 6.0
    assert source == "explicit"


def test_effective_reps_falls_back_to_rpe_when_rir_missing() -> None:
    effective_reps, source = effective_reps_to_failure(3, rpe=8)
    assert effective_reps == 5.0
    assert source == "inferred_from_rpe"


def test_effort_adjusted_e1rm_distinguishes_same_weight_reps_by_rir() -> None:
    e1rm_rir0, source_rir0 = effort_adjusted_e1rm(70, 3, rir=0)
    e1rm_rir3, source_rir3 = effort_adjusted_e1rm(70, 3, rir=3)
    assert source_rir0 == "explicit"
    assert source_rir3 == "explicit"
    assert e1rm_rir3 > e1rm_rir0


def test_confidence_from_evidence_degrades_for_comparability() -> None:
    baseline = confidence_from_evidence(observed_points=6, required_points=6)
    degraded = confidence_from_evidence(
        observed_points=6,
        required_points=6,
        comparability_degraded=True,
    )
    assert baseline > degraded


def test_build_insufficient_envelope_includes_required_fields() -> None:
    envelope = build_insufficient_envelope(
        capability="strength_1rm",
        required_observations=6,
        observed_observations=2,
        model_version="capability_estimation.v1",
        recommended_next_observations=["log 4 more hard sets with RIR or RPE"],
    )
    assert envelope["status"] == STATUS_INSUFFICIENT_DATA
    assert envelope["data_sufficiency"]["required_observations"] == 6
    assert envelope["data_sufficiency"]["observed_observations"] == 2
    assert "insufficient_data" in {c["code"] for c in envelope["caveats"]}
    assert envelope["recommended_next_observations"]


def test_build_capability_envelope_preserves_contract_shape() -> None:
    sufficiency = data_sufficiency_block(
        required_observations=6,
        observed_observations=6,
        uncertainty_reason_codes=[],
        recommended_next_observations=[],
    )
    envelope = build_capability_envelope(
        capability="sprint_max_speed",
        estimate_mean=9.14,
        estimate_interval=interval_around(9.14, 0.11),
        status="ok",
        confidence=0.89,
        data_sufficiency=sufficiency,
        model_version="capability_estimation.v1",
        protocol_signature={"timing_method": "timing_gates"},
        comparability={"group": "track:tartan|timing_gates"},
    )
    assert envelope["schema_version"] == "capability_output.v1"
    assert envelope["estimate"]["mean"] == 9.14
    assert len(envelope["estimate"]["interval"]) == 2
    assert "model_version" in envelope
    assert "data_sufficiency" in envelope


def test_summarize_observations_supports_inverse_variance_weighting() -> None:
    mean, sd = summarize_observations([100.0, 101.0], variances=[0.04, 0.09])
    assert mean is not None
    assert 100.0 <= mean <= 101.0
    assert sd > 0.0
