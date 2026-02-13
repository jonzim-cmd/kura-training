from __future__ import annotations

from kura_workers.performance_gate import RegressionPolicy, evaluate_regression_gate


def _report(api_p95: float, worker_p95: float) -> dict[str, object]:
    return {
        "schema_version": "performance_baseline.v1",
        "api": {
            "endpoints": [
                {
                    "label": "GET /v1/projections",
                    "p95_ms": api_p95,
                }
            ]
        },
        "worker": {
            "handlers": [
                {
                    "label": "worker.update_training_timeline",
                    "p95_ms": worker_p95,
                }
            ]
        },
    }


def test_regression_gate_passes_when_within_budget() -> None:
    policy = RegressionPolicy(
        max_regression_pct=0.20,
        min_regression_budget_ms=5.0,
        api_absolute_p95_ms=250.0,
        worker_absolute_p95_ms=750.0,
    )
    result = evaluate_regression_gate(
        baseline_report=_report(api_p95=100.0, worker_p95=300.0),
        candidate_report=_report(api_p95=115.0, worker_p95=330.0),
        policy=policy,
        baseline_path="baseline.json",
        candidate_path="candidate.json",
    )
    assert result["status"] == "pass"
    assert result["summary"]["failed_metric_count"] == 0
    assert result["failure_reasons"] == []


def test_regression_gate_fails_when_regression_exceeds_budget() -> None:
    policy = RegressionPolicy(
        max_regression_pct=0.05,
        min_regression_budget_ms=2.0,
        api_absolute_p95_ms=250.0,
        worker_absolute_p95_ms=750.0,
    )
    result = evaluate_regression_gate(
        baseline_report=_report(api_p95=100.0, worker_p95=300.0),
        candidate_report=_report(api_p95=130.0, worker_p95=450.0),
        policy=policy,
        baseline_path="baseline.json",
        candidate_path="candidate.json",
    )
    assert result["status"] == "fail"
    assert result["summary"]["failed_metric_count"] == 2
    assert "api::GET /v1/projections:regression_over_budget" in result["failure_reasons"]
    assert (
        "worker::worker.update_training_timeline:regression_over_budget"
        in result["failure_reasons"]
    )


def test_regression_gate_fails_when_candidate_metric_missing() -> None:
    policy = RegressionPolicy()
    baseline = _report(api_p95=100.0, worker_p95=300.0)
    candidate = {
        "schema_version": "performance_baseline.v1",
        "api": {"endpoints": []},
        "worker": {"handlers": []},
    }
    result = evaluate_regression_gate(
        baseline_report=baseline,
        candidate_report=candidate,
        policy=policy,
        baseline_path="baseline.json",
        candidate_path="candidate.json",
    )
    assert result["status"] == "fail"
    assert result["summary"]["failed_metric_count"] == 2
    assert "api::GET /v1/projections:missing_metric" in result["failure_reasons"]
    assert "worker::worker.update_training_timeline:missing_metric" in result["failure_reasons"]
