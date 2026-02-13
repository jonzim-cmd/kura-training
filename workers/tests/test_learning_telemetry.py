"""Tests for canonical learning telemetry schema and helpers."""

import pytest

from kura_workers.learning_telemetry import (
    LEARNING_TELEMETRY_SCHEMA_VERSION,
    build_learning_signal_event,
    core_signal_types,
    pseudonymize_user_id,
    signal_category,
)


def test_core_signal_catalog_is_versioned_and_sufficiently_rich():
    signal_types = core_signal_types()
    assert len(signal_types) >= 10
    assert "save_handshake_verified" in signal_types
    assert "save_claim_mismatch_attempt" in signal_types
    assert "repair_auto_applied" in signal_types
    assert "workflow_violation" in signal_types
    assert "viz_source_bound" in signal_types
    assert "post_task_reflection_confirmed" in signal_types
    assert "post_task_reflection_partial" in signal_types
    assert "post_task_reflection_unresolved" in signal_types


def test_pseudonymize_user_id_is_stable_and_non_reversible_in_payload():
    user_id = "77b0a0d5-f9e1-4a12-8616-2cfdc24e6f7b"
    first = pseudonymize_user_id(user_id)
    second = pseudonymize_user_id(user_id)
    assert first == second
    assert first.startswith("u_")
    assert user_id not in first


def test_build_learning_signal_event_emits_stable_contract():
    event = build_learning_signal_event(
        user_id="user-123",
        signal_type="repair_proposed",
        workflow_phase="quality_health_evaluation",
        source="quality_health",
        agent="repair_planner",
        issue_type="unresolved_exercise_identity",
        invariant_id="INV-001",
        confidence="high",
        modality="chat",
        attributes={"proposal_id": "repair:1"},
        idempotency_seed="seed-1",
    )

    assert event["event_type"] == "learning.signal.logged"
    assert event["data"]["schema_version"] == LEARNING_TELEMETRY_SCHEMA_VERSION
    assert event["data"]["signal_type"] == "repair_proposed"
    assert event["data"]["category"] == "quality_signal"
    assert event["data"]["signature"]["invariant_id"] == "INV-001"
    assert event["data"]["signature"]["issue_type"] == "unresolved_exercise_identity"
    assert event["data"]["signature"]["confidence_band"] == "high"
    assert "pseudonymized_user_id" in event["data"]["user_ref"]
    assert event["metadata"]["idempotency_key"].startswith("learning-signal-")


def test_unknown_signal_type_is_rejected():
    with pytest.raises(ValueError):
        signal_category("not_a_real_signal")


def test_post_task_reflection_signal_categories_are_stable():
    assert signal_category("post_task_reflection_confirmed") == "outcome_signal"
    assert signal_category("post_task_reflection_partial") == "friction_signal"
    assert signal_category("post_task_reflection_unresolved") == "friction_signal"
