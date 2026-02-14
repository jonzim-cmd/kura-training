from __future__ import annotations

from kura_workers.eval_harness import build_shadow_evaluation_report


def _shadow_eval(model_tier: str, *, coverage: float, mae: float) -> dict[str, object]:
    return {
        "model_tier": model_tier,
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
                "metrics": {
                    "coverage_ci95": coverage,
                    "mae": mae,
                },
            }
        ],
    }


def test_shadow_eval_tier_matrix_contract_blocks_weakest_tier_regressions() -> None:
    baseline_strict = _shadow_eval("strict", coverage=0.90, mae=6.0)
    baseline_moderate = _shadow_eval("moderate", coverage=0.89, mae=6.1)
    candidate_strict = _shadow_eval("strict", coverage=0.81, mae=6.1)
    candidate_moderate = _shadow_eval("moderate", coverage=0.88, mae=6.2)

    report = build_shadow_evaluation_report(
        baseline_eval=baseline_moderate,
        candidate_eval=candidate_moderate,
        baseline_tier_reports={"strict": baseline_strict, "moderate": baseline_moderate},
        candidate_tier_reports={"strict": candidate_strict, "moderate": candidate_moderate},
    )

    assert report["tier_matrix"]["policy_version"] == "shadow_eval_tier_matrix_v1"
    assert report["tier_matrix"]["weakest_tier"] == "strict"
    assert report["release_gate"]["tier_matrix_policy_version"] == "shadow_eval_tier_matrix_v1"
    assert report["release_gate"]["weakest_tier"] == "strict"

    strict_entry = next(item for item in report["tier_matrix"]["tiers"] if item["model_tier"] == "strict")
    assert strict_entry["release_gate"]["status"] == "fail"
    assert report["release_gate"]["status"] == "fail"
