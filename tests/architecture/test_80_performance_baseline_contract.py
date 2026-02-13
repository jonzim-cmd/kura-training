from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "performance_baseline.py"
ARTIFACT_PATH = REPO_ROOT / "docs" / "reports" / "performance-baseline-latest.json"


def _load_artifact() -> dict[str, object]:
    assert ARTIFACT_PATH.exists(), f"Missing baseline artifact: {ARTIFACT_PATH}"
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def test_performance_baseline_entrypoint_exposes_expected_cli_flags() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for required_flag in (
        "--output",
        "--samples",
        "--warmup",
        "--worker-event-count",
        "--worker-window-days",
        "--startup-timeout-seconds",
        "--api-pace-ms",
        "--port",
    ):
        assert required_flag in result.stdout


def test_performance_baseline_artifact_has_machine_readable_contract() -> None:
    report = _load_artifact()
    assert report["schema_version"] == "performance_baseline.v1"
    assert isinstance(report["generated_at"], str) and report["generated_at"]
    assert isinstance(report["run_command"], str) and "performance_baseline.py" in report["run_command"]

    machine_context = report["machine_context"]
    assert isinstance(machine_context, dict)
    assert machine_context["python_version"]
    assert machine_context["platform"]

    dataset = report["dataset"]
    assert dataset["profile"] == "synthetic_set_logged_v1"
    assert dataset["user_id"]
    assert dataset["seed_event_id"]

    api = report["api"]
    endpoints = api["endpoints"]
    assert isinstance(endpoints, list) and endpoints
    endpoint_labels = {entry["label"] for entry in endpoints}
    assert endpoint_labels == {
        "POST /v1/events",
        "GET /v1/projections",
        "GET /v1/projections/user_profile/me",
    }
    for entry in endpoints:
        assert entry["sample_count"] > 0
        assert entry["p50_ms"] >= 0.0
        assert entry["p95_ms"] >= entry["p50_ms"]

    worker = report["worker"]
    handlers = worker["handlers"]
    assert isinstance(handlers, list) and handlers
    handler_labels = {entry["label"] for entry in handlers}
    assert handler_labels == {
        "worker.update_exercise_progression",
        "worker.update_training_timeline",
    }
    for entry in handlers:
        assert entry["sample_count"] > 0
        assert entry["p50_ms"] >= 0.0
        assert entry["p95_ms"] >= entry["p50_ms"]
