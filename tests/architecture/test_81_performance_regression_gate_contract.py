from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "performance_regression_gate.py"


def _write_report(path: Path, api_p95: float, worker_p95: float) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "performance_baseline.v1",
                "api": {
                    "endpoints": [
                        {"label": "GET /v1/projections", "p95_ms": api_p95},
                    ]
                },
                "worker": {
                    "handlers": [
                        {"label": "worker.update_training_timeline", "p95_ms": worker_p95},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def _run_gate(*, baseline: Path, candidate: Path, output: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "workers" / "src")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_performance_regression_gate_pass_case_is_reproducible(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    output = tmp_path / "gate.json"
    _write_report(baseline, api_p95=100.0, worker_p95=300.0)
    _write_report(candidate, api_p95=115.0, worker_p95=335.0)

    result = _run_gate(baseline=baseline, candidate=candidate, output=output)
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "performance_regression_gate.v1"
    assert payload["status"] == "pass"
    assert payload["summary"]["failed_metric_count"] == 0


def test_performance_regression_gate_fail_case_is_reproducible(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    output = tmp_path / "gate.json"
    _write_report(baseline, api_p95=100.0, worker_p95=300.0)
    _write_report(candidate, api_p95=500.0, worker_p95=900.0)

    result = _run_gate(baseline=baseline, candidate=candidate, output=output)
    assert result.returncode == 1, result.stdout + result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "performance_regression_gate.v1"
    assert payload["status"] == "fail"
    assert payload["summary"]["failed_metric_count"] == 2
    assert payload["failure_reasons"]
