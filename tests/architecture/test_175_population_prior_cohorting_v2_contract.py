from __future__ import annotations

from kura_workers.population_priors import (
    _build_causal_lookup_targets,
    _cohort_key_from_user_profile,
    _cohort_key_variants,
    build_causal_estimand_target_key,
)


def test_population_prior_cohort_key_v2_includes_objective_mode_dimension() -> None:
    cohort_key = _cohort_key_from_user_profile(
        {
            "user": {
                "profile": {
                    "training_modality": "running",
                    "experience_level": "advanced",
                },
                "workflow_state": {"mode": "coach"},
            }
        }
    )
    assert cohort_key == "tm:running|el:advanced|om:coach"


def test_population_prior_cohort_variants_keep_legacy_and_global_fallbacks() -> None:
    variants = _cohort_key_variants("tm:running|el:advanced|om:coach")
    assert variants[0] == "tm:running|el:advanced|om:coach"
    assert "tm:running|el:advanced" in variants
    assert "tm:unknown|el:unknown|om:unknown" in variants
    assert "tm:unknown|el:unknown" in variants


def test_causal_lookup_targets_include_objective_modality_and_aggregate_fallback() -> None:
    target_key = build_causal_estimand_target_key(
        intervention="program_change",
        outcome="strength_delta_by_exercise_t_plus_1",
        objective_mode="coach",
        modality="running",
        exercise_id="bench_press",
    )
    lookup_targets = _build_causal_lookup_targets(target_key)
    assert lookup_targets[0] == target_key
    assert (
        "estimand|program_change|strength_aggregate_delta_t_plus_1|om:coach|mod:running"
        in lookup_targets
    )
    assert (
        "estimand|program_change|strength_aggregate_delta_t_plus_1|om:unknown|mod:unknown"
        in lookup_targets
    )
