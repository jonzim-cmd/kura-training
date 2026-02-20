"""Contract for cross-capability evaluation and rollout gating."""

from __future__ import annotations

from typing import Any


def capability_eval_gate_contract_v1() -> dict[str, Any]:
    return {
        "schema_version": "capability_eval_gate.v1",
        "required_capabilities": [
            "strength_1rm",
            "sprint_max_speed",
            "jump_height",
            "endurance_threshold",
        ],
        "required_capability_fields": [
            "schema_version",
            "capability",
            "status",
            "estimate.mean",
            "estimate.interval",
            "confidence",
            "data_sufficiency",
            "model_version",
        ],
        "statuses": ["pass", "fail", "insufficient_data"],
        "thresholds": {
            "mean_confidence_min": 0.55,
            "required_fields_ok_rate_min": 1.0,
        },
        "rollout_policy": {
            "allow_rollout_only_on": "pass",
            "block_on": ["fail", "insufficient_data"],
            "insufficient_data_is_explicit": True,
        },
    }
