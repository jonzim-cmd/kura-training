"""Performance regression gate for API/worker baseline artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import json


REPORT_SCHEMA_VERSION = "performance_regression_gate.v1"


@dataclass(frozen=True)
class RegressionPolicy:
    max_regression_pct: float = 0.20
    min_regression_budget_ms: float = 5.0
    api_absolute_p95_ms: float = 250.0
    worker_absolute_p95_ms: float = 750.0

    def validate(self) -> None:
        if self.max_regression_pct < 0:
            raise ValueError("max_regression_pct must be >= 0")
        if self.min_regression_budget_ms < 0:
            raise ValueError("min_regression_budget_ms must be >= 0")
        if self.api_absolute_p95_ms <= 0:
            raise ValueError("api_absolute_p95_ms must be > 0")
        if self.worker_absolute_p95_ms <= 0:
            raise ValueError("worker_absolute_p95_ms must be > 0")


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _safe_p95(entry: dict[str, Any], metric_id: str) -> float:
    value = entry.get("p95_ms")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Metric {metric_id} is missing numeric p95_ms") from exc
    if parsed < 0:
        raise ValueError(f"Metric {metric_id} has negative p95_ms")
    return parsed


def extract_p95_metrics(
    report: dict[str, Any], *, allow_empty: bool = False
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}

    api_endpoints = (report.get("api") or {}).get("endpoints") or []
    for entry in api_endpoints:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()
        if not label:
            continue
        metric_id = f"api::{label}"
        metrics[metric_id] = {
            "metric_id": metric_id,
            "domain": "api",
            "label": label,
            "p95_ms": _safe_p95(entry, metric_id),
        }

    worker_handlers = (report.get("worker") or {}).get("handlers") or []
    for entry in worker_handlers:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()
        if not label:
            continue
        metric_id = f"worker::{label}"
        metrics[metric_id] = {
            "metric_id": metric_id,
            "domain": "worker",
            "label": label,
            "p95_ms": _safe_p95(entry, metric_id),
        }

    if not metrics and not allow_empty:
        raise ValueError("No benchmark metrics found in report")
    return metrics


def _absolute_threshold(domain: str, policy: RegressionPolicy) -> float:
    if domain == "api":
        return policy.api_absolute_p95_ms
    return policy.worker_absolute_p95_ms


def evaluate_regression_gate(
    *,
    baseline_report: dict[str, Any],
    candidate_report: dict[str, Any],
    policy: RegressionPolicy,
    baseline_path: str,
    candidate_path: str,
) -> dict[str, Any]:
    policy.validate()

    baseline_metrics = extract_p95_metrics(baseline_report)
    candidate_metrics = extract_p95_metrics(candidate_report, allow_empty=True)

    metric_results: list[dict[str, Any]] = []
    gate_failures: list[str] = []

    for metric_id, baseline_metric in sorted(baseline_metrics.items()):
        candidate_metric = candidate_metrics.get(metric_id)
        if candidate_metric is None:
            metric_results.append(
                {
                    "metric_id": metric_id,
                    "domain": baseline_metric["domain"],
                    "label": baseline_metric["label"],
                    "baseline_p95_ms": baseline_metric["p95_ms"],
                    "candidate_p95_ms": None,
                    "delta_ms": None,
                    "delta_pct": None,
                    "allowed_regression_ms": None,
                    "absolute_threshold_ms": _absolute_threshold(
                        baseline_metric["domain"], policy
                    ),
                    "status": "fail",
                    "failure_reasons": ["missing_metric"],
                }
            )
            gate_failures.append(f"{metric_id}:missing_metric")
            continue

        baseline_p95 = float(baseline_metric["p95_ms"])
        candidate_p95 = float(candidate_metric["p95_ms"])
        delta_ms = round(candidate_p95 - baseline_p95, 3)
        delta_pct = (
            round((candidate_p95 - baseline_p95) / baseline_p95, 6)
            if baseline_p95 > 0
            else None
        )

        allowed_regression_ms = round(
            baseline_p95 * policy.max_regression_pct + policy.min_regression_budget_ms,
            3,
        )
        absolute_threshold_ms = _absolute_threshold(baseline_metric["domain"], policy)

        reasons: list[str] = []
        if delta_ms > allowed_regression_ms:
            reasons.append("regression_over_budget")
        if candidate_p95 > absolute_threshold_ms:
            reasons.append("absolute_threshold_exceeded")

        status = "pass" if not reasons else "fail"
        if reasons:
            gate_failures.extend([f"{metric_id}:{reason}" for reason in reasons])

        metric_results.append(
            {
                "metric_id": metric_id,
                "domain": baseline_metric["domain"],
                "label": baseline_metric["label"],
                "baseline_p95_ms": baseline_p95,
                "candidate_p95_ms": candidate_p95,
                "delta_ms": delta_ms,
                "delta_pct": delta_pct,
                "allowed_regression_ms": allowed_regression_ms,
                "absolute_threshold_ms": absolute_threshold_ms,
                "status": status,
                "failure_reasons": reasons,
            }
        )

    status = "pass" if not gate_failures else "fail"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "policy": {
            "max_regression_pct": policy.max_regression_pct,
            "min_regression_budget_ms": policy.min_regression_budget_ms,
            "api_absolute_p95_ms": policy.api_absolute_p95_ms,
            "worker_absolute_p95_ms": policy.worker_absolute_p95_ms,
        },
        "inputs": {
            "baseline_path": baseline_path,
            "candidate_path": candidate_path,
        },
        "summary": {
            "metric_count": len(metric_results),
            "failed_metric_count": len([item for item in metric_results if item["status"] == "fail"]),
        },
        "metrics": metric_results,
        "failure_reasons": gate_failures,
    }
