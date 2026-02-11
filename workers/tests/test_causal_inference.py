"""Tests for observational causal inference utilities."""

from kura_workers.causal_inference import estimate_intervention_effect


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
