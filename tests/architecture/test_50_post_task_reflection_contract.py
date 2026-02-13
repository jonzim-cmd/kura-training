from __future__ import annotations

from kura_workers.learning_telemetry import core_signal_types, signal_category
from tests.architecture.conftest import assert_kura_api_test_passes


def test_post_task_reflection_contract_confirms_when_verification_and_audit_are_clean() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::post_task_reflection_contract_confirms_when_verification_and_audit_are_clean"
    )


def test_post_task_reflection_contract_marks_unresolved_when_verification_fails() -> None:
    assert_kura_api_test_passes(
        "routes::agent::tests::post_task_reflection_contract_marks_unresolved_when_verification_fails"
    )


def test_reflection_signal_taxonomy_is_registered() -> None:
    assert "post_task_reflection_confirmed" in core_signal_types()
    assert "post_task_reflection_partial" in core_signal_types()
    assert "post_task_reflection_unresolved" in core_signal_types()
    assert signal_category("post_task_reflection_confirmed") == "outcome_signal"
    assert signal_category("post_task_reflection_partial") == "friction_signal"
    assert signal_category("post_task_reflection_unresolved") == "friction_signal"
