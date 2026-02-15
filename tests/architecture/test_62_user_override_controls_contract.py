from __future__ import annotations

from kura_workers.event_conventions import get_event_conventions
from kura_workers.interview_guide import get_interview_guide
from tests.architecture.conftest import assert_kura_api_test_passes


OVERRIDE_RUNTIME_TESTS: tuple[str, ...] = (
    "routes::agent::tests::user_preference_overrides_apply_scope_and_verbosity_when_healthy",
    "routes::agent::tests::user_preference_scope_override_is_clamped_when_quality_not_healthy",
    "routes::agent::tests::user_preference_confirmation_always_forces_confirm_first_gate",
    "routes::agent::tests::user_preference_confirmation_never_cannot_bypass_strict_tier",
    "routes::agent::tests::user_preference_save_confirmation_mode_always_prompts_when_unsaved",
    "routes::agent::tests::user_preference_save_confirmation_mode_never_keeps_routine_verified_auto_save",
    "routes::agent::tests::user_preference_save_confirmation_mode_never_respects_high_impact_safety_floor",
    "routes::agent::tests::user_preference_overrides_fallback_to_defaults_when_invalid",
    "routes::agent::tests::claim_guard_respects_concise_verbosity_phrase",
)


def test_override_keys_are_declared_in_preference_catalog() -> None:
    keys = set(get_event_conventions()["preference.set"]["common_keys"])
    assert {
        "autonomy_scope",
        "verbosity",
        "confirmation_strictness",
        "save_confirmation_mode",
    }.issubset(keys)


def test_interview_guide_can_emit_override_preferences() -> None:
    guide = get_interview_guide()
    comm_pref = next(
        area for area in guide["coverage_areas"] if area["area"] == "communication_preferences"
    )
    assert "preference.set" in comm_pref["produces"]


def test_consistency_inbox_approval_is_not_overridable() -> None:
    """User preference overrides must not be able to disable the
    'no fix without approval' safety invariant."""
    from kura_workers.system_config import _get_agent_behavior

    behavior = _get_agent_behavior()
    protocol = behavior["operational"]["consistency_inbox_protocol_v1"]
    # This is a safety invariant, not a user preference.
    assert protocol["approval_required_before_fix"] is True
    # Verify it's declared as a safety invariant.
    invariants = protocol.get("safety_invariants", [])
    assert any("approval" in inv.lower() or "fix" in inv.lower() for inv in invariants), (
        "consistency_inbox_protocol must declare a safety invariant about approval-before-fix"
    )


def test_override_precedence_runtime_cases_pass() -> None:
    for test_name in OVERRIDE_RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)
