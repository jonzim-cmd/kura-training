"""Layer 1: Core Invariant Property Tests.

Property-based tests for the critical invariant validation in the Kura API.
These test AGAINST the real API — no Python reimplementation.

Covers:
- set.logged intensity validation (RPE/RIR)
- event.retracted structural invariants
- set.corrected structural invariants
- projection_rule.created/archived structural invariants
- training_plan intensity validation
- Plausibility warning correctness

Run:
    KURA_API_KEY=... uv run pytest tests/fuzzing/test_core_invariants.py -v
    KURA_API_KEY=... uv run pytest tests/fuzzing/test_core_invariants.py -v --hypothesis-seed=42
"""

from __future__ import annotations

import uuid

from hypothesis import given, settings, HealthCheck

from .conftest import KuraTestClient, pytestmark  # noqa: F401
from .contracts import CreateEventRequest, EventMetadata, PLAUSIBILITY_RANGES
from .strategies import (
    set_logged_data,
    retraction_data,
    set_correction_data,
    projection_rule_created_data,
    projection_rule_archived_data,
    training_plan_data,
    plausibility_data,
    numeric_or_junk,
    locale_decimal,
    non_numeric_junk,
)
from .assertions import (
    assert_success,
    assert_policy_violation,
    assert_rejection,
    assert_plausibility_warnings,
    assert_no_plausibility_warnings,
    assert_event_always_accepted,
)

FUZZ_SETTINGS = settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=10_000,
)


# --- set.logged intensity ---


class TestSetLoggedIntensity:
    """Property tests for set.logged RPE/RIR validation."""

    @given(data=set_logged_data(valid=True))
    @FUZZ_SETTINGS
    def test_valid_set_logged_accepted(self, api_client: KuraTestClient, data):
        """Valid set.logged events are always accepted."""
        event = CreateEventRequest(
            event_type="set.logged",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        # Valid data should be accepted (2xx) or get plausibility warnings (still 2xx)
        assert_event_always_accepted(status)

    @given(data=set_logged_data(valid=False))
    @FUZZ_SETTINGS
    def test_invalid_set_logged_handled(self, api_client: KuraTestClient, data):
        """Invalid set.logged data gets proper error handling.

        The API should either:
        - Accept it (if the 'invalid' parts don't trigger invariants, e.g. non-numeric reps)
        - Reject with a known invariant code
        """
        event = CreateEventRequest(
            event_type="set.logged",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status >= 400:
            assert_rejection(body, status)

    @given(rpe=numeric_or_junk)
    @FUZZ_SETTINGS
    def test_rpe_fuzz(self, api_client: KuraTestClient, rpe):
        """Fuzz the RPE field with diverse values."""
        event = CreateEventRequest(
            event_type="set.logged",
            data={"exercise": "Squat", "exercise_id": "squat", "reps": 5, "rpe": rpe},
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status == 422:
            code = assert_policy_violation(body, status)
            assert code in {
                "inv_set_rpe_invalid_type",
                "inv_set_rpe_out_of_range",
            }, f"Unexpected RPE violation code: {code}"

    @given(rir=numeric_or_junk)
    @FUZZ_SETTINGS
    def test_rir_fuzz(self, api_client: KuraTestClient, rir):
        """Fuzz the RIR field with diverse values."""
        event = CreateEventRequest(
            event_type="set.logged",
            data={"exercise": "Squat", "exercise_id": "squat", "reps": 5, "rir": rir},
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status == 422:
            code = assert_policy_violation(body, status)
            assert code in {
                "inv_set_rir_invalid_type",
                "inv_set_rir_out_of_range",
            }, f"Unexpected RIR violation code: {code}"

    @given(locale_val=locale_decimal)
    @FUZZ_SETTINGS
    def test_locale_decimal_rpe_parsing(self, api_client: KuraTestClient, locale_val):
        """Locale decimals like '8,5' should be parsed correctly."""
        event = CreateEventRequest(
            event_type="set.logged",
            data={"exercise": "Squat", "exercise_id": "squat", "reps": 5, "rpe": locale_val},
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        # Should either be accepted or rejected with range error, NOT invalid type
        if status == 422:
            code = assert_policy_violation(body, status)
            assert code != "inv_set_rpe_invalid_type", (
                f"Locale decimal '{locale_val}' should be parseable"
            )


# --- event.retracted ---


class TestRetractionInvariants:
    """Property tests for event.retracted validation."""

    @given(data=retraction_data(valid=True))
    @FUZZ_SETTINGS
    def test_valid_retraction_accepted(self, api_client: KuraTestClient, data):
        """Valid retractions are accepted."""
        event = CreateEventRequest(
            event_type="event.retracted",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        assert_event_always_accepted(status)

    @given(data=retraction_data(valid=False))
    @FUZZ_SETTINGS
    def test_invalid_retraction_rejected(self, api_client: KuraTestClient, data):
        """Invalid retractions get proper rejection with known codes."""
        event = CreateEventRequest(
            event_type="event.retracted",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        code = assert_policy_violation(body, status)
        assert code in {
            "inv_retraction_target_required",
            "inv_retraction_target_invalid_uuid",
            "inv_retraction_type_invalid",
        }, f"Unexpected retraction violation code: {code}"


# --- set.corrected ---


class TestSetCorrectionInvariants:
    """Property tests for set.corrected validation."""

    @given(data=set_correction_data(valid=True))
    @FUZZ_SETTINGS
    def test_valid_correction_accepted(self, api_client: KuraTestClient, data):
        """Valid corrections are accepted."""
        event = CreateEventRequest(
            event_type="set.corrected",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        assert_event_always_accepted(status)

    @given(data=set_correction_data(valid=False))
    @FUZZ_SETTINGS
    def test_invalid_correction_rejected(self, api_client: KuraTestClient, data):
        """Invalid corrections get proper rejection with known codes."""
        event = CreateEventRequest(
            event_type="set.corrected",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        code = assert_policy_violation(body, status)
        assert code in {
            "inv_set_correction_target_required",
            "inv_set_correction_target_invalid_uuid",
            "inv_set_correction_changed_fields_required",
            "inv_set_correction_changed_fields_invalid",
            "inv_set_correction_changed_fields_empty",
            "inv_set_correction_changed_fields_key_invalid",
        }, f"Unexpected correction violation code: {code}"


# --- projection_rule.created ---


class TestProjectionRuleInvariants:
    """Property tests for projection rule validation."""

    @given(data=projection_rule_created_data(valid=True))
    @FUZZ_SETTINGS
    def test_valid_rule_accepted(self, api_client: KuraTestClient, data):
        """Valid projection rules pass structural validation.

        NOTE: May still be rejected by legacy domain invariants (workflow gate).
        We only check that it's NOT rejected by structural invariants.
        """
        event = CreateEventRequest(
            event_type="projection_rule.created",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status == 422:
            code = body.get("error") or body.get("code", "")
            # Workflow/domain invariants are fine — structural ones should NOT fire
            assert code not in {
                "inv_projection_rule_name_required",
                "inv_projection_rule_type_required",
                "inv_projection_rule_type_invalid",
                "inv_projection_rule_source_events_invalid",
                "inv_projection_rule_fields_invalid",
                "inv_projection_rule_source_events_too_large",
                "inv_projection_rule_fields_too_large",
                "inv_projection_rule_group_by_required",
                "inv_projection_rule_group_by_not_in_fields",
            }, f"Valid rule data triggered structural invariant: {code}"

    @given(data=projection_rule_created_data(valid=False))
    @FUZZ_SETTINGS
    def test_invalid_rule_rejected(self, api_client: KuraTestClient, data):
        """Invalid projection rules get proper rejection."""
        event = CreateEventRequest(
            event_type="projection_rule.created",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        code = assert_policy_violation(body, status)
        assert code.startswith("inv_projection_rule_"), (
            f"Expected projection rule invariant code, got: {code}"
        )

    @given(data=projection_rule_archived_data(valid=False))
    @FUZZ_SETTINGS
    def test_invalid_archive_rejected(self, api_client: KuraTestClient, data):
        """Invalid rule archives get proper rejection."""
        event = CreateEventRequest(
            event_type="projection_rule.archived",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        code = assert_policy_violation(body, status, expected_code="inv_projection_rule_archive_name_required")


# --- training_plan intensity ---


class TestTrainingPlanIntensity:
    """Property tests for training plan intensity validation."""

    @given(data=training_plan_data(valid=True))
    @FUZZ_SETTINGS
    def test_valid_plan_passes_structural_validation(self, api_client: KuraTestClient, data):
        """Valid training plan data passes structural checks.

        NOTE: Will be rejected by inv_plan_write_requires_write_with_proof
        (which is expected), but structural intensity checks should NOT fire.
        """
        event = CreateEventRequest(
            event_type="training_plan.created",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status == 422:
            code = body.get("error") or body.get("code", "")
            # Domain invariants (workflow, write-with-proof) are expected
            structural_codes = {
                "inv_training_plan_sessions_invalid",
                "inv_training_plan_exercises_invalid",
                "inv_training_plan_target_rpe_invalid_type",
                "inv_training_plan_target_rpe_out_of_range",
                "inv_training_plan_rpe_invalid_type",
                "inv_training_plan_rpe_out_of_range",
                "inv_training_plan_target_rir_invalid_type",
                "inv_training_plan_target_rir_out_of_range",
                "inv_training_plan_rir_invalid_type",
                "inv_training_plan_rir_out_of_range",
            }
            assert code not in structural_codes, (
                f"Valid plan data triggered structural invariant: {code}"
            )

    @given(data=training_plan_data(valid=False))
    @FUZZ_SETTINGS
    def test_invalid_plan_rejected(self, api_client: KuraTestClient, data):
        """Invalid training plan data is rejected with known codes."""
        event = CreateEventRequest(
            event_type="training_plan.created",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        code = assert_policy_violation(body, status)
        assert code.startswith("inv_training_plan_"), (
            f"Expected training plan invariant code, got: {code}"
        )


# --- Plausibility warnings ---


class TestPlausibilityWarnings:
    """Property tests for soft plausibility warnings."""

    @given(data=plausibility_data("set.logged", within_range=True))
    @FUZZ_SETTINGS
    def test_set_logged_in_range_no_warnings(self, api_client: KuraTestClient, data):
        """In-range set.logged values should NOT produce warnings."""
        event = CreateEventRequest(
            event_type="set.logged",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        assert_event_always_accepted(status)
        assert_no_plausibility_warnings(body, "set.logged", data)

    @given(data=plausibility_data("set.logged", within_range=False))
    @FUZZ_SETTINGS
    def test_set_logged_out_of_range_gets_warnings(self, api_client: KuraTestClient, data):
        """Out-of-range set.logged values should produce warnings but still be accepted."""
        event = CreateEventRequest(
            event_type="set.logged",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        # RPE/RIR out of range triggers hard rejection, not soft warning
        # Only check plausibility for weight_kg and reps
        if status < 400:
            assert_plausibility_warnings(body, "set.logged", data)

    @given(data=plausibility_data("bodyweight.logged", within_range=False))
    @FUZZ_SETTINGS
    def test_bodyweight_out_of_range_gets_warnings(self, api_client: KuraTestClient, data):
        """Out-of-range bodyweight should produce warnings but still be accepted."""
        event = CreateEventRequest(
            event_type="bodyweight.logged",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status < 400:
            assert_plausibility_warnings(body, "bodyweight.logged", data)

    @given(data=plausibility_data("meal.logged", within_range=False))
    @FUZZ_SETTINGS
    def test_meal_out_of_range_gets_warnings(self, api_client: KuraTestClient, data):
        """Out-of-range nutrition values should produce warnings."""
        event = CreateEventRequest(
            event_type="meal.logged",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status < 400:
            assert_plausibility_warnings(body, "meal.logged", data)

    @given(data=plausibility_data("sleep.logged", within_range=False))
    @FUZZ_SETTINGS
    def test_sleep_out_of_range_gets_warnings(self, api_client: KuraTestClient, data):
        """Out-of-range sleep values should produce warnings."""
        event = CreateEventRequest(
            event_type="sleep.logged",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status < 400:
            assert_plausibility_warnings(body, "sleep.logged", data)

    @given(data=plausibility_data("soreness.logged", within_range=False))
    @FUZZ_SETTINGS
    def test_soreness_out_of_range_gets_warnings(self, api_client: KuraTestClient, data):
        """Out-of-range soreness severity should produce warnings."""
        event = CreateEventRequest(
            event_type="soreness.logged",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status < 400:
            assert_plausibility_warnings(body, "soreness.logged", data)

    @given(data=plausibility_data("energy.logged", within_range=False))
    @FUZZ_SETTINGS
    def test_energy_out_of_range_gets_warnings(self, api_client: KuraTestClient, data):
        """Out-of-range energy level should produce warnings."""
        event = CreateEventRequest(
            event_type="energy.logged",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status < 400:
            assert_plausibility_warnings(body, "energy.logged", data)

    @given(data=plausibility_data("measurement.logged", within_range=False))
    @FUZZ_SETTINGS
    def test_measurement_out_of_range_gets_warnings(self, api_client: KuraTestClient, data):
        """Out-of-range measurement values should produce warnings."""
        event = CreateEventRequest(
            event_type="measurement.logged",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status < 400:
            assert_plausibility_warnings(body, "measurement.logged", data)
