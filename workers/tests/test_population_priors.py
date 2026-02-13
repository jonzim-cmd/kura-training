"""Unit tests for population prior aggregation logic."""

from kura_workers.population_priors import (
    CAUSAL_OUTCOME_STRENGTH_PER_EXERCISE,
    STRENGTH_FALLBACK_TARGET_KEY,
    _bool_from_any,
    _build_causal_lookup_targets,
    _build_causal_prior_rows,
    _build_readiness_prior_rows,
    _build_strength_prior_rows,
    _cohort_key_from_user_profile,
    _quality_health_status_from_projection,
    _weighted_stats,
    build_causal_estimand_target_key,
    population_prior_blend_weight,
)


def test_bool_from_any_parses_common_values():
    assert _bool_from_any(True) is True
    assert _bool_from_any(False) is False
    assert _bool_from_any(1) is True
    assert _bool_from_any(0) is False
    assert _bool_from_any("true") is True
    assert _bool_from_any("No") is False
    assert _bool_from_any("maybe") is None


def test_cohort_key_from_user_profile_defaults_unknown():
    assert _cohort_key_from_user_profile(None) == "tm:unknown|el:unknown"
    assert _cohort_key_from_user_profile({}) == "tm:unknown|el:unknown"
    assert _cohort_key_from_user_profile(
        {"user": {"profile": {"training_modality": "Strength", "experience_level": "Advanced"}}}
    ) == "tm:strength|el:advanced"


def test_quality_health_status_from_projection_prefers_explicit_status():
    assert _quality_health_status_from_projection(
        {"status": "degraded", "integrity_slo_status": "healthy"}
    ) == "degraded"
    assert _quality_health_status_from_projection(
        {"quality_status": "monitor"}
    ) == "monitor"


def test_quality_health_status_from_projection_falls_back_to_autonomy_policy():
    assert _quality_health_status_from_projection(
        {"autonomy_policy": {"calibration_status": "degraded"}}
    ) == "degraded"
    assert _quality_health_status_from_projection({}) == "unknown"


def test_weighted_stats_has_variance_floor():
    mean, var = _weighted_stats([1.0, 1.0, 1.0], [1.0, 1.0, 1.0])
    assert mean == 1.0
    assert var > 0.0


def test_build_strength_prior_rows_respects_privacy_gate():
    rows = [
        {
            "user_id": "u1",
            "key": "bench_press",
            "data": {
                "trend": {"slope_kg_per_day": 0.05},
                "dynamics": {"estimated_1rm": {"confidence": 0.8}},
                "data_quality": {"insufficient_data": False},
            },
        },
        {
            "user_id": "u2",
            "key": "bench_press",
            "data": {
                "trend": {"slope_kg_per_day": 0.08},
                "dynamics": {"estimated_1rm": {"confidence": 0.7}},
                "data_quality": {"insufficient_data": False},
            },
        },
    ]
    cohort_by_user = {
        "u1": "tm:strength|el:intermediate",
        "u2": "tm:strength|el:intermediate",
    }

    blocked = _build_strength_prior_rows(
        rows,
        cohort_by_user,
        min_cohort_size=3,
        window_days=180,
    )
    assert blocked == []

    allowed = _build_strength_prior_rows(
        rows,
        cohort_by_user,
        min_cohort_size=2,
        window_days=180,
    )
    targets = {(row["cohort_key"], row["target_key"]) for row in allowed}
    assert ("tm:strength|el:intermediate", "bench_press") in targets
    assert ("tm:strength|el:intermediate", STRENGTH_FALLBACK_TARGET_KEY) in targets


def test_build_readiness_prior_rows():
    rows = [
        {
            "user_id": "u1",
            "key": "overview",
            "data": {
                "baseline": {"posterior_mean": 0.62},
                "dynamics": {"readiness": {"confidence": 0.8}},
                "data_quality": {"insufficient_data": False},
            },
        },
        {
            "user_id": "u2",
            "key": "overview",
            "data": {
                "baseline": {"posterior_mean": 0.66},
                "dynamics": {"readiness": {"confidence": 0.9}},
                "data_quality": {"insufficient_data": False},
            },
        },
    ]
    cohort_by_user = {
        "u1": "tm:strength|el:intermediate",
        "u2": "tm:strength|el:intermediate",
    }
    priors = _build_readiness_prior_rows(
        rows,
        cohort_by_user,
        min_cohort_size=2,
        window_days=180,
    )
    assert len(priors) == 1
    assert priors[0]["projection_type"] == "readiness_inference"
    assert priors[0]["prior_payload"]["privacy_gate_passed"] is True


def test_build_causal_estimand_target_key_normalizes_components():
    assert build_causal_estimand_target_key(
        intervention=" Nutrition_Shift ",
        outcome="Readiness_Score_T_Plus_1",
    ) == "estimand|nutrition_shift|readiness_score_t_plus_1"
    assert build_causal_estimand_target_key(
        intervention="program_change",
        outcome=CAUSAL_OUTCOME_STRENGTH_PER_EXERCISE,
        exercise_id=" Bench_Press ",
    ) == "estimand|program_change|strength_delta_by_exercise_t_plus_1|bench_press"


def test_build_causal_lookup_targets_uses_strength_aggregate_fallback():
    target = build_causal_estimand_target_key(
        intervention="sleep_intervention",
        outcome=CAUSAL_OUTCOME_STRENGTH_PER_EXERCISE,
        exercise_id="deadlift",
    )
    lookup_targets = _build_causal_lookup_targets(target)
    assert lookup_targets[0] == target
    assert (
        "estimand|sleep_intervention|strength_aggregate_delta_t_plus_1"
        in lookup_targets
    )


def test_build_causal_prior_rows():
    rows = [
        {
            "user_id": "u1",
            "key": "overview",
            "data": {
                "status": "ok",
                "interventions": {
                    "program_change": {
                        "status": "ok",
                        "outcomes": {
                            "readiness_score_t_plus_1": {
                                "status": "ok",
                                "effect": {"mean_ate": 0.042, "ci95": [0.01, 0.08]},
                                "diagnostics": {"effect_sd": 0.02},
                            }
                        },
                    }
                },
            },
        },
        {
            "user_id": "u2",
            "key": "overview",
            "data": {
                "status": "ok",
                "interventions": {
                    "program_change": {
                        "status": "ok",
                        "outcomes": {
                            "readiness_score_t_plus_1": {
                                "status": "ok",
                                "effect": {"mean_ate": 0.058, "ci95": [0.02, 0.1]},
                                "diagnostics": {"effect_sd": 0.03},
                            }
                        },
                    }
                },
            },
        },
    ]
    cohort_by_user = {
        "u1": "tm:strength|el:intermediate",
        "u2": "tm:strength|el:intermediate",
    }

    priors = _build_causal_prior_rows(
        rows,
        cohort_by_user,
        min_cohort_size=2,
        window_days=180,
    )

    assert len(priors) == 1
    prior = priors[0]
    assert prior["projection_type"] == "causal_inference"
    assert prior["target_key"] == "estimand|program_change|readiness_score_t_plus_1"
    assert prior["prior_payload"]["privacy_gate_passed"] is True
    assert prior["prior_payload"]["mean"] > 0.0
    assert prior["prior_payload"]["var"] > 0.0
    assert prior["prior_payload"]["estimand"]["intervention"] == "program_change"
    assert prior["prior_payload"]["estimand"]["outcome"] == "readiness_score_t_plus_1"


def test_population_prior_blend_weight_clamped(monkeypatch):
    monkeypatch.setenv("KURA_POPULATION_PRIOR_BLEND_WEIGHT", "1.5")
    assert population_prior_blend_weight() == 0.95
