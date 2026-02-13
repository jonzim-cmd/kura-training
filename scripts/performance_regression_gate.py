#!/usr/bin/env python3
"""Compare candidate benchmark metrics against a baseline and enforce thresholds."""

from __future__ import annotations

import argparse
from pathlib import Path

from kura_workers.performance_gate import (
    RegressionPolicy,
    evaluate_regression_gate,
    load_report,
    write_report,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / "docs" / "reports" / "performance-baseline-latest.json"
DEFAULT_CANDIDATE = REPO_ROOT / "docs" / "reports" / "performance-baseline-latest.json"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "reports" / "performance-regression-gate-latest.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="performance_regression_gate",
        description="Fail when candidate benchmark p95 metrics regress beyond policy thresholds.",
    )
    parser.add_argument(
        "--baseline",
        default=str(DEFAULT_BASELINE),
        help="Path to baseline benchmark JSON artifact.",
    )
    parser.add_argument(
        "--candidate",
        default=str(DEFAULT_CANDIDATE),
        help="Path to candidate benchmark JSON artifact.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path where gate report JSON should be written.",
    )
    parser.add_argument(
        "--max-regression-pct",
        type=float,
        default=0.20,
        help="Allowed p95 regression percentage over baseline before failing.",
    )
    parser.add_argument(
        "--min-regression-budget-ms",
        type=float,
        default=5.0,
        help="Absolute p95 slack budget in milliseconds, added to percentage budget.",
    )
    parser.add_argument(
        "--api-absolute-p95-ms",
        type=float,
        default=250.0,
        help="Absolute API p95 limit in milliseconds.",
    )
    parser.add_argument(
        "--worker-absolute-p95-ms",
        type=float,
        default=750.0,
        help="Absolute worker-handler p95 limit in milliseconds.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    baseline_path = Path(args.baseline).resolve()
    candidate_path = Path(args.candidate).resolve()
    output_path = Path(args.output).resolve()

    baseline_report = load_report(baseline_path)
    candidate_report = load_report(candidate_path)
    policy = RegressionPolicy(
        max_regression_pct=float(args.max_regression_pct),
        min_regression_budget_ms=float(args.min_regression_budget_ms),
        api_absolute_p95_ms=float(args.api_absolute_p95_ms),
        worker_absolute_p95_ms=float(args.worker_absolute_p95_ms),
    )

    gate_report = evaluate_regression_gate(
        baseline_report=baseline_report,
        candidate_report=candidate_report,
        policy=policy,
        baseline_path=str(baseline_path),
        candidate_path=str(candidate_path),
    )
    write_report(output_path, gate_report)

    if gate_report["status"] == "pass":
        print(f"PASS: {output_path}")
        raise SystemExit(0)
    print(f"FAIL: {output_path}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
