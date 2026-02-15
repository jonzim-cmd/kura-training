from __future__ import annotations

import re
from pathlib import Path

from kura_workers.learning_telemetry import core_signal_types, signal_category
from kura_workers.system_config import _get_conventions
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::advisory_scoring_layer_contract_schema_version_is_pinned",
    "routes::agent::tests::advisory_scoring_layer_contract_is_advisory_only_non_blocking",
    "routes::agent::tests::advisory_scoring_layer_contract_maps_risky_case_to_cautionary_actions",
    "routes::agent::tests::advisory_scoring_layer_contract_maps_stable_case_to_low_friction_actions",
    "routes::agent::tests::advisory_scoring_layer_contract_keeps_general_guidance_for_high_risk_scores",
)

RUNTIME_THRESHOLD_CONSTANTS: tuple[str, ...] = (
    "ADVISORY_RESPONSE_HINT_GROUNDED_MIN_SPECIFICITY",
    "ADVISORY_RESPONSE_HINT_GROUNDED_MAX_HALLUCINATION_RISK",
    "ADVISORY_RESPONSE_HINT_GROUNDED_MAX_DATA_QUALITY_RISK",
    "ADVISORY_RESPONSE_HINT_GENERAL_MIN_HALLUCINATION_RISK",
    "ADVISORY_RESPONSE_HINT_GENERAL_MAX_CONFIDENCE",
    "ADVISORY_RESPONSE_HINT_GENERAL_MIN_DATA_QUALITY_RISK",
    "ADVISORY_PERSIST_ACTION_ASK_FIRST_MIN_RISK",
    "ADVISORY_PERSIST_ACTION_DRAFT_MIN_RISK",
    "ADVISORY_CLARIFICATION_BUDGET_MIN_RISK",
    "ADVISORY_UNCERTAINTY_NOTE_MIN_HALLUCINATION_RISK",
    "ADVISORY_UNCERTAINTY_NOTE_MAX_CONFIDENCE",
)
_POLICY_RS = (
    Path(__file__).resolve().parents[2] / "api" / "src" / "routes" / "agent" / "policy.rs"
)
_RUNTIME_CONSTANT_RE = re.compile(
    r"pub\(super\) const (?P<name>ADVISORY_[A-Z0-9_]+): f64 = (?P<value>\d+(?:\.\d+)?);"
)


def _runtime_advisory_threshold_constants() -> dict[str, float]:
    source = _POLICY_RS.read_text(encoding="utf-8")
    parsed: dict[str, float] = {}
    for match in _RUNTIME_CONSTANT_RE.finditer(source):
        name = match.group("name")
        if name in RUNTIME_THRESHOLD_CONSTANTS:
            parsed[name] = float(match.group("value"))
    missing = sorted(set(RUNTIME_THRESHOLD_CONSTANTS) - set(parsed))
    assert not missing, f"Missing advisory runtime constants in policy.rs: {missing}"
    return {name: parsed[name] for name in RUNTIME_THRESHOLD_CONSTANTS}


def test_advisory_scoring_layer_contract_declares_four_score_surface_and_nudge_policy() -> None:
    contract = _get_conventions()["advisory_scoring_layer_v1"]

    assert contract["schema_version"] == "advisory_scoring_layer.v1"
    assert contract["action_schema_version"] == "advisory_action_plan.v1"
    assert contract["policy_role"] == "advisory_only"

    scores = contract["scores"]
    assert set(scores) == {
        "specificity_score",
        "hallucination_risk",
        "data_quality_risk",
        "confidence_score",
    }
    assert scores["specificity_score"]["direction"] == "higher_is_better"
    assert scores["hallucination_risk"]["direction"] == "higher_is_riskier"
    assert scores["data_quality_risk"]["direction"] == "higher_is_riskier"
    assert scores["confidence_score"]["direction"] == "higher_is_better"
    for score in scores.values():
        assert score["range"] == [0.0, 1.0]

    action_map = contract["action_map"]
    assert set(action_map["response_mode_hint_values"]) == {
        "grounded_personalized",
        "hypothesis_personalized",
        "general_guidance",
    }
    assert set(action_map["persist_action_values"]) == {
        "persist_now",
        "draft_preferred",
        "ask_first",
    }
    assert action_map["clarification_question_budget_max"] == 1
    assert (
        action_map["runtime_threshold_constants"]
        == _runtime_advisory_threshold_constants()
    )

    safety = contract["safety"]
    assert safety["advisory_only"] is True
    assert safety["must_not_block_autonomy"] is True
    assert safety["must_reconcile_with_persist_intent"] is True
    assert safety["must_keep_saved_wording_proof_bound"] is True

    event_contract = contract["event_contract"]
    assert event_contract["event_type"] == "learning.signal.logged"
    assert event_contract["signal_type"] == "advisory_scoring_assessed"

    calibration_metrics = set(contract["calibration"]["metrics"])
    assert {
        "advisory_high_hallucination_risk_rate_pct",
        "advisory_high_data_quality_risk_rate_pct",
        "advisory_high_risk_cautious_rate_pct",
        "advisory_high_risk_persist_now_rate_pct",
    }.issubset(calibration_metrics)


def test_advisory_scoring_signal_taxonomy_is_registered() -> None:
    signals = set(core_signal_types())
    assert "advisory_scoring_assessed" in signals
    assert signal_category("advisory_scoring_assessed") == "quality_signal"


def test_advisory_scoring_layer_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
