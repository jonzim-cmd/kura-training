"""Hardening gate for structured errors, calibration, and import mapping rollout."""

from __future__ import annotations

from typing import Any

HARDENING_GATE_SCHEMA_VERSION = "training_hardening_gate.v1"


def hardening_gate_contract_v1() -> dict[str, Any]:
    return {
        "schema_version": HARDENING_GATE_SCHEMA_VERSION,
        "tracks": [
            "error_taxonomy",
            "load_calibration",
            "import_mapping",
            "rollback_readiness",
        ],
        "required_issue_chain": [
            "kura-training-316.10",
            "kura-training-316.11",
            "kura-training-316.12",
            "kura-training-316.13",
            "kura-training-316.14",
            "kura-training-316.15",
        ],
        "thresholds": {
            "error_taxonomy": {
                "missing_anchor_code_coverage_min_pct": 100.0,
                "code_based_detection_required": True,
            },
            "load_calibration": {
                "shadow_allow_rollout_required": True,
                "max_brier_score_degradation": 0.005,
            },
            "import_mapping": {
                "golden_fixture_pass_rate_min_pct": 100.0,
                "provider_modality_coverage_min_pct": 75.0,
            },
            "rollback_readiness": {
                "training_load_v2_flag_disable_verified": True,
                "calibrated_profile_flag_disable_verified": True,
                "parameter_version_pin_verified": True,
            },
        },
    }


def _check(name: str, passed: bool, *, value: Any, threshold: Any, detail: str) -> dict[str, Any]:
    return {
        "check": name,
        "pass": bool(passed),
        "value": value,
        "threshold": threshold,
        "detail": detail,
    }


def evaluate_hardening_gate_v1(snapshot: dict[str, Any]) -> dict[str, Any]:
    contract = hardening_gate_contract_v1()
    thresholds = contract["thresholds"]
    checks: list[dict[str, Any]] = []

    error_taxonomy = snapshot.get("error_taxonomy") or {}
    error_coverage = float(error_taxonomy.get("missing_anchor_code_coverage_pct", 0.0) or 0.0)
    code_detection_enabled = bool(error_taxonomy.get("code_based_detection_enabled"))
    checks.append(
        _check(
            "error_taxonomy.code_based_detection",
            code_detection_enabled
            == bool(thresholds["error_taxonomy"]["code_based_detection_required"]),
            value=code_detection_enabled,
            threshold=bool(thresholds["error_taxonomy"]["code_based_detection_required"]),
            detail="Missing-anchor detection must run on structured error codes only.",
        )
    )
    checks.append(
        _check(
            "error_taxonomy.coverage",
            error_coverage
            >= float(thresholds["error_taxonomy"]["missing_anchor_code_coverage_min_pct"]),
            value=error_coverage,
            threshold=float(thresholds["error_taxonomy"]["missing_anchor_code_coverage_min_pct"]),
            detail="All missing-anchor invalid cases must emit stable error codes.",
        )
    )

    calibration = snapshot.get("calibration") or {}
    shadow = calibration.get("shadow") or {}
    shadow_allow_rollout = bool(shadow.get("allow_rollout"))
    brier_delta = float(
        ((shadow.get("metrics") or {}).get("delta") or {}).get("brier_score", 0.0) or 0.0
    )
    checks.append(
        _check(
            "load_calibration.shadow_allow_rollout",
            shadow_allow_rollout
            == bool(thresholds["load_calibration"]["shadow_allow_rollout_required"]),
            value=shadow_allow_rollout,
            threshold=bool(thresholds["load_calibration"]["shadow_allow_rollout_required"]),
            detail="Calibration candidate must pass shadow guardrails.",
        )
    )
    checks.append(
        _check(
            "load_calibration.brier_degradation",
            brier_delta <= float(thresholds["load_calibration"]["max_brier_score_degradation"]),
            value=brier_delta,
            threshold=float(thresholds["load_calibration"]["max_brier_score_degradation"]),
            detail="Candidate may not degrade Brier score beyond allowed threshold.",
        )
    )

    import_mapping = snapshot.get("import_mapping") or {}
    fixture_pass_rate = float(import_mapping.get("golden_fixture_pass_rate_pct", 0.0) or 0.0)
    coverage_pct = float(import_mapping.get("provider_modality_coverage_pct", 0.0) or 0.0)
    checks.append(
        _check(
            "import_mapping.golden_fixture_pass_rate",
            fixture_pass_rate >= float(thresholds["import_mapping"]["golden_fixture_pass_rate_min_pct"]),
            value=fixture_pass_rate,
            threshold=float(thresholds["import_mapping"]["golden_fixture_pass_rate_min_pct"]),
            detail="Golden fixture suite must be fully green before rollout.",
        )
    )
    checks.append(
        _check(
            "import_mapping.provider_modality_coverage",
            coverage_pct >= float(thresholds["import_mapping"]["provider_modality_coverage_min_pct"]),
            value=coverage_pct,
            threshold=float(thresholds["import_mapping"]["provider_modality_coverage_min_pct"]),
            detail="Provider/modality support coverage must meet minimum threshold.",
        )
    )

    rollback = snapshot.get("rollback_readiness") or {}
    checks.append(
        _check(
            "rollback_readiness.training_load_v2_flag_disable",
            bool(rollback.get("training_load_v2_flag_disable_verified"))
            == bool(thresholds["rollback_readiness"]["training_load_v2_flag_disable_verified"]),
            value=bool(rollback.get("training_load_v2_flag_disable_verified")),
            threshold=bool(thresholds["rollback_readiness"]["training_load_v2_flag_disable_verified"]),
            detail="training_load_v2 feature-flag rollback must be validated.",
        )
    )
    checks.append(
        _check(
            "rollback_readiness.calibrated_profile_flag_disable",
            bool(rollback.get("calibrated_profile_flag_disable_verified"))
            == bool(thresholds["rollback_readiness"]["calibrated_profile_flag_disable_verified"]),
            value=bool(rollback.get("calibrated_profile_flag_disable_verified")),
            threshold=bool(thresholds["rollback_readiness"]["calibrated_profile_flag_disable_verified"]),
            detail="calibrated-profile feature-flag rollback must be validated.",
        )
    )
    checks.append(
        _check(
            "rollback_readiness.parameter_version_pin",
            bool(rollback.get("parameter_version_pin_verified"))
            == bool(thresholds["rollback_readiness"]["parameter_version_pin_verified"]),
            value=bool(rollback.get("parameter_version_pin_verified")),
            threshold=bool(thresholds["rollback_readiness"]["parameter_version_pin_verified"]),
            detail="Parameter version pinning fallback must be validated.",
        )
    )

    passed = all(bool(check["pass"]) for check in checks)
    failed = [check for check in checks if not check["pass"]]
    recommended_next_steps = [
        f"Fix {check['check']} ({check['detail']})"
        for check in failed
    ]
    residual_risks = [
        "Structured error telemetry drift",
        "Calibration overfitting to replay cohorts",
        "Provider-specific import edge cases",
    ]

    return {
        "schema_version": HARDENING_GATE_SCHEMA_VERSION,
        "status": "pass" if passed else "fail",
        "allow_rollout": passed,
        "checks": checks,
        "failed_checks": failed,
        "recommended_next_steps": recommended_next_steps,
        "residual_risks": residual_risks,
    }
