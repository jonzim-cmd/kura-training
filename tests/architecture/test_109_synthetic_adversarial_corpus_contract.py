from __future__ import annotations

from kura_workers.eval_harness import build_shadow_evaluation_report
from kura_workers.system_config import _get_conventions


def _mode_rows(mode: str, *, total: int, triggered: int, regret_band: str, laaj_verdict: str):
    return [
        {
            "scenario_id": f"{mode}-{idx + 1}",
            "failure_mode": mode,
            "triggered_failure": idx < triggered,
            "retrieval_regret_band": regret_band,
            "laaj_verdict": laaj_verdict,
        }
        for idx in range(total)
    ]


def _shadow_eval(*, triggered: int, regret_band: str, laaj_verdict: str) -> dict[str, object]:
    return {
        "model_tier": "strict",
        "projection_types": ["strength_inference"],
        "source": "event_store",
        "strength_engine": "closed_form",
        "eval_status": "ok",
        "summary": {},
        "summary_by_source": {},
        "shadow_mode": {"status": "pass", "checks": []},
        "results": [
            {
                "projection_type": "strength_inference",
                "status": "ok",
                "metrics": {"coverage_ci95": 0.90, "mae": 6.0},
            }
        ],
        "adversarial_corpus": {
            "scenarios": _mode_rows(
                "retrieval_miss",
                total=10,
                triggered=triggered,
                regret_band=regret_band,
                laaj_verdict=laaj_verdict,
            )
        },
    }


def test_synthetic_adversarial_corpus_contract_declares_required_modes_and_sidecar_alignment() -> None:
    contract = _get_conventions()["synthetic_adversarial_corpus_v1"]
    assert contract["schema_version"] == "synthetic_adversarial_corpus.v1"
    assert contract["policy_role"] == "advisory_regression_gate"
    assert set(contract["required_failure_modes"]) == {
        "hallucination",
        "overconfidence",
        "retrieval_miss",
        "data_integrity_drift",
    }
    assert contract["entrypoint"] == "eval_harness.evaluate_synthetic_adversarial_corpus"
    assert contract["regression_policy"]["max_failure_rate_delta"]["retrieval_miss"] == 0.03
    assert contract["regression_policy"]["min_sidecar_alignment_rate"] == 0.70
    assert contract["sidecar_alignment"]["retrieval_regret_signal_type"] == "retrieval_regret_observed"
    assert contract["sidecar_alignment"]["laaj_signal_type"] == "laaj_sidecar_assessed"
    assert contract["sidecar_alignment"]["expected_laaj_verdict_when_triggered"] == "review"


def test_synthetic_adversarial_corpus_runtime_contract_blocks_on_regression() -> None:
    baseline = _shadow_eval(triggered=2, regret_band="high", laaj_verdict="review")
    candidate = _shadow_eval(triggered=6, regret_band="low", laaj_verdict="pass")

    report = build_shadow_evaluation_report(
        baseline_eval=baseline,
        candidate_eval=candidate,
    )

    assert report["adversarial_corpus"]["schema_version"] == "synthetic_adversarial_corpus.v1"
    assert report["adversarial_corpus"]["status"] == "fail"
    assert report["adversarial_corpus"]["failed_modes"] == ["retrieval_miss"]
    assert report["release_gate"]["status"] == "fail"
    assert "adversarial_corpus:retrieval_miss" in report["release_gate"]["failed_metrics"]
