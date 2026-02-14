from __future__ import annotations

import sys
from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS_SRC = REPO_ROOT / "workers" / "src"
if str(WORKERS_SRC) not in sys.path:
    sys.path.insert(0, str(WORKERS_SRC))

from kura_workers.system_config import _get_agent_behavior, _get_conventions


def _load_save_echo_policy() -> dict:
    behavior = _get_agent_behavior()
    operational = behavior["operational"]
    assert "save_echo_policy_v1" in operational, (
        "save_echo_policy_v1 must exist in agent_behavior.operational"
    )
    return operational["save_echo_policy_v1"]


# ── Python-side contract assertions ──────────────────────────────────


def test_save_echo_policy_always_on() -> None:
    policy = _load_save_echo_policy()
    assert policy["always_on"] is True, "save_echo must be always_on"


def test_save_echo_policy_tier_independent() -> None:
    policy = _load_save_echo_policy()
    assert policy["tier_independent"] is True, "save_echo must be tier_independent"


def test_save_echo_policy_schema_version_pinned() -> None:
    policy = _load_save_echo_policy()
    assert policy["schema_version"] == "save_echo_policy.v1"


def test_save_echo_policy_has_telemetry_fields() -> None:
    policy = _load_save_echo_policy()
    expected_fields = {"save_echo_required", "save_echo_present", "save_echo_completeness"}
    actual_fields = set(policy["telemetry_fields"].keys())
    assert expected_fields == actual_fields, (
        f"telemetry_fields mismatch: expected {expected_fields}, got {actual_fields}"
    )


def test_save_echo_policy_contract_required_after_saved_states() -> None:
    policy = _load_save_echo_policy()
    required_after = policy["contract"]["required_after"]
    assert "saved_verified" in required_after
    assert "inferred" in required_after


def test_model_tier_registry_mentions_save_echo_separation() -> None:
    conventions = _get_conventions()
    rules = conventions["model_tier_registry_v1"]["rules"]
    save_echo_rules = [r for r in rules if "save_echo" in r.lower() or "Save-Echo" in r]
    assert len(save_echo_rules) >= 1, (
        "model_tier_registry_v1 rules must reference save_echo_policy_v1"
    )


# ── Rust runtime contract tests (delegated) ──────────────────────────


def test_save_echo_contract_enforced_in_moderate_tier() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::save_echo_contract_enforced_in_moderate_tier"
    )


def test_save_echo_contract_enforced_in_advanced_tier() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::save_echo_contract_enforced_in_advanced_tier"
    )


def test_save_echo_contract_not_required_when_claim_failed() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::save_echo_contract_not_required_when_claim_failed"
    )
