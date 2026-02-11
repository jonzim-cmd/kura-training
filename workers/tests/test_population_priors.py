"""Unit tests for population prior aggregation logic."""

from kura_workers.population_priors import (
    STRENGTH_FALLBACK_TARGET_KEY,
    _bool_from_any,
    _build_readiness_prior_rows,
    _build_strength_prior_rows,
    _cohort_key_from_user_profile,
    _weighted_stats,
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


def test_population_prior_blend_weight_clamped(monkeypatch):
    monkeypatch.setenv("KURA_POPULATION_PRIOR_BLEND_WEIGHT", "1.5")
    assert population_prior_blend_weight() == 0.95
