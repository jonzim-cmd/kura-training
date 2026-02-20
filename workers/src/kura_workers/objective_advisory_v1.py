"""Objective advisory contract (warnings-only, override-aware)."""

from __future__ import annotations

from typing import Any


def objective_advisory_contract_v1() -> dict[str, Any]:
    """Return the advisory-only objective consistency contract."""
    return {
        "schema_version": "objective_advisory.v1",
        "policy_role": "advisory_only",
        "warning_schema": {
            "required_fields": [
                "code",
                "severity",
                "confidence",
                "message",
                "evidence",
                "overridable",
            ],
            "severity_values": ["warning", "info"],
        },
        "required_reason_codes": [
            "objective_trackability_gap",
            "objective_default_inferred",
            "objective_metric_staleness",
            "objective_override_review_due",
        ],
        "trackability_rules": {
            "requires_success_metrics_or_target": True,
            "staleness_days": 21,
        },
        "override_policy": {
            "warnings_overridable": True,
            "required_fields": [
                "reason",
                "scope",
                "expected_outcome",
                "review_point",
                "actor",
            ],
            "safety_invariants_non_overridable": [
                "consent_write_gate",
                "approval_required_high_impact_write",
            ],
        },
        "non_goals": [
            "No hard blocks for objective warning signals.",
            "No silent coercion into a single sports template.",
        ],
    }

