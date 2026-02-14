from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_proof_in_production_artifact.py"


def _write_shadow_report(path: Path, *, gate_status: str, allow_rollout: bool) -> None:
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-02-14T18:00:00+00:00",
                "release_gate": {
                    "policy_version": "shadow_eval_gate_v1",
                    "tier_matrix_policy_version": "shadow_eval_tier_matrix_v1",
                    "status": gate_status,
                    "allow_rollout": allow_rollout,
                    "weakest_tier": "strict",
                    "tier_matrix_status": gate_status,
                    "failed_metrics": [] if allow_rollout else ["strength_inference:coverage_ci95"],
                    "missing_metrics": [] if gate_status == "pass" else ["readiness_inference:mae_nowcast"],
                    "reasons": [] if gate_status == "pass" else ["weakest_tier_gate_status=strict:fail"],
                },
                "tier_matrix": {
                    "policy_version": "shadow_eval_tier_matrix_v1",
                    "status": gate_status,
                    "weakest_tier": "strict",
                    "tiers": [
                        {
                            "model_tier": "strict",
                            "release_gate": {
                                "status": gate_status,
                                "missing_metrics": [],
                            },
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )


def _run_generator(*, shadow_report: Path, json_output: Path, md_output: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "workers" / "src")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--shadow-report",
            str(shadow_report),
            "--json-output",
            str(json_output),
            "--md-output",
            str(md_output),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_proof_in_production_artifact_generator_pass_case(tmp_path: Path) -> None:
    shadow_report = tmp_path / "shadow.json"
    json_output = tmp_path / "artifact.json"
    md_output = tmp_path / "artifact.md"
    _write_shadow_report(shadow_report, gate_status="pass", allow_rollout=True)

    result = _run_generator(
        shadow_report=shadow_report,
        json_output=json_output,
        md_output=md_output,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    payload = json.loads(json_output.read_text(encoding="utf-8"))
    markdown = md_output.read_text(encoding="utf-8")
    assert payload["schema_version"] == "proof_in_production_decision_artifact.v1"
    assert payload["decision"]["status"] == "approve_rollout"
    assert payload["decision"]["gate_status"] == "pass"
    assert payload["stakeholder_summary"]["headline"]
    assert "## Recommended Next Steps" in markdown


def test_proof_in_production_artifact_generator_insufficient_data_case(tmp_path: Path) -> None:
    shadow_report = tmp_path / "shadow.json"
    json_output = tmp_path / "artifact.json"
    md_output = tmp_path / "artifact.md"
    _write_shadow_report(shadow_report, gate_status="insufficient_data", allow_rollout=False)

    result = _run_generator(
        shadow_report=shadow_report,
        json_output=json_output,
        md_output=md_output,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "proof_in_production_decision_artifact.v1"
    assert payload["decision"]["status"] == "needs_data"
    assert payload["missing_data"]
    assert payload["recommended_next_steps"]
