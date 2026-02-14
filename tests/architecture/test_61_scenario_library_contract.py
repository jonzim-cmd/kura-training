from __future__ import annotations

from kura_workers.system_config import _get_agent_behavior
from tests.architecture.conftest import assert_kura_api_test_passes


SCENARIO_RUNTIME_TESTS: dict[str, str] = {
    "onboarding_logging_saved": "routes::agent::tests::scenario_library_onboarding_logging_saved",
    "planning_override_confirm_first": "routes::agent::tests::scenario_library_planning_override_confirm_first",
    "correction_inferred_with_provenance": "routes::agent::tests::scenario_library_correction_inferred_with_provenance",
    "session_feedback_contradiction_unresolved": "routes::agent::tests::scenario_library_contradiction_unresolved",
    "pending_read_after_write_unresolved": "routes::agent::tests::scenario_library_pending_read_after_write_unresolved",
    "multi_conflict_overload_single_question": "routes::agent::tests::scenario_library_overload_single_conflict_question",
}


def test_scenario_library_contract_has_required_categories_and_transitions() -> None:
    behavior = _get_agent_behavior()
    library = behavior["operational"]["scenario_library_v1"]
    scenarios = library["scenarios"]
    categories = {scenario["category"] for scenario in scenarios}
    transitions = {
        transition
        for scenario in scenarios
        for transition in scenario.get("covers_transitions", [])
    }

    assert categories == set(library["required_categories"])
    assert {"onboarding", "logging", "correction", "planning_transition", "consistency_review"}.issubset(transitions)


def test_scenario_library_runtime_conformance_cases_pass() -> None:
    for test_name in SCENARIO_RUNTIME_TESTS.values():
        assert_kura_api_test_passes(test_name)
