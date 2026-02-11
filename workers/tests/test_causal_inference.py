"""Tests for observational causal inference utilities."""

from kura_workers.causal_inference import estimate_intervention_effect
from kura_workers.handlers.causal_inference import (
    OUTCOME_STRENGTH_PER_EXERCISE,
    _append_result_caveats,
    _estimate_segment_slices,
    _manifest_contribution,
)


def test_causal_effect_insufficient_data():
    samples = [
        {
            "treated": idx % 2,
            "outcome": 0.4 + (0.01 * idx),
            "confounders": {
                "baseline_readiness": 0.5 + (0.02 * idx),
                "baseline_sleep_hours": 6.0 + (0.1 * idx),
            },
        }
        for idx in range(8)
    ]

    result = estimate_intervention_effect(samples, min_samples=24, bootstrap_samples=80)
    assert result["status"] == "insufficient_data"
    assert result["effect"] is None
    assert any(c["code"] == "insufficient_samples" for c in result["caveats"])


def test_causal_effect_returns_estimate_and_ci():
    samples: list[dict] = []
    for idx in range(120):
        baseline = 0.1 + (0.8 * ((idx % 20) / 20.0))
        sleep_hours = 6.0 + (0.4 * (idx % 5))
        load_volume = 800.0 + (120.0 * (idx % 7))
        propensity = 0.18 + (0.55 * baseline) + (0.04 * ((idx % 3) / 2.0))
        draw = ((idx * 37) % 100) / 100.0
        treated = 1 if draw < max(0.05, min(0.95, propensity)) else 0
        noise = (((idx * 17) % 13) - 6) / 300.0
        outcome = (
            (0.33 * baseline)
            + (0.015 * (sleep_hours - 6.0))
            - (0.00005 * load_volume)
            + (0.09 * treated)
            + noise
        )
        samples.append(
            {
                "treated": treated,
                "outcome": outcome,
                "confounders": {
                    "baseline_readiness": baseline,
                    "baseline_sleep_hours": sleep_hours,
                    "baseline_load_volume": load_volume,
                },
            }
        )

    result = estimate_intervention_effect(samples, min_samples=40, bootstrap_samples=140)
    assert result["status"] == "ok"
    assert result["effect"] is not None
    assert result["effect"]["mean_ate"] > 0.03
    assert result["effect"]["ci95"][0] < result["effect"]["ci95"][1]
    assert result["propensity"]["method"] == "logistic_ipw"
    assert any(a["code"] == "no_unmeasured_confounding" for a in result["assumptions"])


def test_causal_effect_surfaces_overlap_related_caveats():
    samples: list[dict] = []
    for idx in range(90):
        baseline_signal = -1.0 + (2.0 * idx / 89.0)
        treated = 1 if baseline_signal > 0.55 else 0
        outcome = (0.2 * baseline_signal) + (0.06 * treated) + (((idx % 9) - 4) / 220.0)
        samples.append(
            {
                "treated": treated,
                "outcome": outcome,
                "confounders": {
                    "baseline_signal": baseline_signal,
                },
            }
        )

    result = estimate_intervention_effect(samples, min_samples=30, bootstrap_samples=120)
    caveat_codes = {c["code"] for c in result["caveats"]}
    assert caveat_codes.intersection(
        {
            "weak_overlap",
            "extreme_weights",
            "low_effective_sample_size",
            "positivity_violation",
        }
    )


def test_segment_slices_apply_minimum_sample_guardrail():
    samples: list[dict] = []
    for idx in range(18):
        samples.append(
            {
                "treated": idx % 2,
                "outcome": 0.42 + (0.05 * (idx % 2)) + (((idx % 4) - 1.5) / 60.0),
                "confounders": {
                    "baseline_readiness": 0.5 + (idx / 100.0),
                    "baseline_sleep_hours": 6.5 + (idx / 50.0),
                },
                "subgroup": "low_readiness" if idx < 9 else "high_readiness",
                "phase": "week_start" if idx % 3 == 0 else "recovery",
            }
        )

    subgroup_results = _estimate_segment_slices(
        samples,
        segment_key="subgroup",
        min_samples=10,
        bootstrap_samples=80,
    )
    assert set(subgroup_results) == {"high_readiness", "low_readiness"}
    for result in subgroup_results.values():
        caveat_codes = {c["code"] for c in result["caveats"]}
        assert "segment_insufficient_samples" in caveat_codes


def test_caveat_propagation_keeps_outcome_and_segment_context():
    machine_caveats: list[dict] = []
    result = {
        "caveats": [
            {
                "code": "weak_overlap",
                "severity": "medium",
                "details": {"overlap_width": 0.08},
            }
        ]
    }

    _append_result_caveats(
        machine_caveats,
        intervention="nutrition_shift",
        outcome="strength_aggregate_delta_t_plus_1",
        result=result,
        exercise_id="bench_press",
        segment_type="phase",
        segment_label="recovery",
    )

    assert len(machine_caveats) == 1
    caveat = machine_caveats[0]
    assert caveat["intervention"] == "nutrition_shift"
    assert caveat["outcome"] == "strength_aggregate_delta_t_plus_1"
    assert caveat["exercise_id"] == "bench_press"
    assert caveat["segment_type"] == "phase"
    assert caveat["segment_label"] == "recovery"


def test_manifest_contribution_prefers_strongest_strength_exercise_signal():
    contribution = _manifest_contribution(
        [
            {
                "data": {
                    "interventions": {
                        "program_change": {
                            "status": "ok",
                            "effect": {"mean_ate": 0.02},
                            "outcomes": {
                                "strength_aggregate_delta_t_plus_1": {
                                    "effect": {"mean_ate": 0.05},
                                },
                                OUTCOME_STRENGTH_PER_EXERCISE: {
                                    "bench_press": {"effect": {"mean_ate": 0.12}},
                                    "squat": {"effect": {"mean_ate": 0.09}},
                                },
                            },
                        }
                    }
                }
            }
        ]
    )

    strongest = contribution["strongest_signal"]
    assert strongest["intervention"] == "program_change"
    assert strongest["outcome"] == OUTCOME_STRENGTH_PER_EXERCISE
    assert strongest["exercise_id"] == "bench_press"
    assert strongest["mean_ate"] == 0.12
