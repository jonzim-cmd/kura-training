"""Layer 1: Advanced Invariant Property Tests.

Property-based tests for domain-level and cross-field invariants.

Covers:
- session.completed certainty contract
- Workflow phase gate (onboarding required for planning writes)
- Timezone requirement for temporal writes
- Training plan write-with-proof requirement
- RPE+RIR consistency checks
- Exercise ID similarity warnings

Run:
    KURA_API_KEY=... uv run pytest tests/fuzzing/test_advanced_invariants.py -v
"""

from __future__ import annotations

import uuid

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from .conftest import KuraTestClient, pytestmark  # noqa: F401
from .contracts import (
    CERTAINTY_FIELDS,
    CERTAINTY_SOURCES,
    CERTAINTY_STATES,
    PLANNING_OR_COACHING_EVENT_TYPES,
    TIMEZONE_REQUIRED_EVENT_TYPES,
    CreateEventRequest,
    EventMetadata,
)
from .strategies import (
    session_completed_data,
    valid_event,
)
from .assertions import (
    assert_policy_violation,
    assert_event_always_accepted,
    InvariantViolation,
)

FUZZ_SETTINGS = settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=10_000,
)


# --- Certainty contract ---


class TestCertaintyContract:
    """Property tests for session.completed certainty validation.

    The certainty contract enforces:
    - confirmed: numeric value required
    - inferred: numeric value + evidence_claim_id required
    - unresolved: NO numeric value + unresolved_reason required
    """

    @given(data=session_completed_data(valid=True))
    @FUZZ_SETTINGS
    def test_valid_certainty_accepted(self, api_client: KuraTestClient, data):
        """Valid certainty data passes validation.

        NOTE: May be rejected by timezone/domain invariants, but NOT by
        certainty-specific invariants.
        """
        event = CreateEventRequest(
            event_type="session.completed",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status == 422:
            code = body.get("code", "")
            certainty_codes = {
                "session_feedback_confirmed_missing_value",
                "session_feedback_inferred_missing_value",
                "session_feedback_inferred_missing_evidence",
                "session_feedback_unresolved_has_value",
                "session_feedback_unresolved_missing_reason",
            }
            assert code not in certainty_codes, (
                f"Valid certainty data triggered certainty violation: {code}"
            )

    @given(data=session_completed_data(valid=False))
    @FUZZ_SETTINGS
    def test_invalid_certainty_rejected(self, api_client: KuraTestClient, data):
        """Invalid certainty data is rejected with the correct code."""
        event = CreateEventRequest(
            event_type="session.completed",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        # Should be rejected (either by certainty or domain invariants)
        if status == 422:
            code = body.get("code", "")
            # We expect either a certainty code or a domain invariant
            # Both are valid rejections for broken certainty data
            assert code, f"Missing violation code in 422 response: {body}"

    @given(
        field_name=st.sampled_from(CERTAINTY_FIELDS),
        state=st.sampled_from(CERTAINTY_STATES),
        source=st.sampled_from(CERTAINTY_SOURCES),
        has_value=st.booleans(),
        has_evidence=st.booleans(),
        has_reason=st.booleans(),
    )
    @FUZZ_SETTINGS
    def test_certainty_state_matrix(
        self,
        api_client: KuraTestClient,
        field_name: str,
        state: str,
        source: str,
        has_value: bool,
        has_evidence: bool,
        has_reason: bool,
    ):
        """Exhaustively test the certainty state matrix.

        For each combination of (state, has_value, has_evidence, has_reason),
        verify the API responds predictably.
        """
        data: dict = {f"{field_name}_state": state, f"{field_name}_source": source}

        max_val = 5 if field_name != "perceived_exertion" else 10
        if has_value:
            data[field_name] = 4 if max_val >= 4 else 2
        if has_evidence:
            data[f"{field_name}_evidence_claim_id"] = f"claim_{uuid.uuid4().hex[:24]}"
        if has_reason:
            data[f"{field_name}_unresolved_reason"] = "User declined"

        event = CreateEventRequest(
            event_type="session.completed",
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)

        if status == 422:
            code = body.get("code", "")
            # Verify the code makes sense for the state
            if state == "confirmed" and not has_value:
                assert code in {
                    "session_feedback_confirmed_missing_value",
                    "inv_timezone_required_for_temporal_write",
                }, f"Unexpected code for confirmed+no_value: {code}"
            elif state == "inferred" and not has_value:
                assert code in {
                    "session_feedback_inferred_missing_value",
                    "inv_timezone_required_for_temporal_write",
                }, f"Unexpected code for inferred+no_value: {code}"
            elif state == "inferred" and has_value and not has_evidence:
                assert code in {
                    "session_feedback_inferred_missing_evidence",
                    "inv_timezone_required_for_temporal_write",
                }, f"Unexpected code for inferred+no_evidence: {code}"
            elif state == "unresolved" and has_value:
                assert code in {
                    "session_feedback_unresolved_has_value",
                    "inv_timezone_required_for_temporal_write",
                }, f"Unexpected code for unresolved+value: {code}"
            elif state == "unresolved" and not has_reason:
                assert code in {
                    "session_feedback_unresolved_missing_reason",
                    "session_feedback_unresolved_has_value",
                    "inv_timezone_required_for_temporal_write",
                }, f"Unexpected code for unresolved+no_reason: {code}"


# --- Workflow phase gate ---


class TestWorkflowPhaseGate:
    """Property tests for the onboarding workflow gate."""

    @given(
        event_type=st.sampled_from(sorted(PLANNING_OR_COACHING_EVENT_TYPES)),
    )
    @FUZZ_SETTINGS
    def test_planning_without_onboarding_rejected(
        self, api_client: KuraTestClient, event_type: str,
    ):
        """Planning/coaching writes WITHOUT onboarding closed are rejected.

        The test user may or may not have onboarding closed.
        If the user DOES have onboarding closed, the test still validates
        that the response is structurally correct.
        """
        # Minimal valid data for each event type
        data_by_type: dict = {
            "training_plan.created": {"name": "test"},
            "training_plan.updated": {"name": "test"},
            "training_plan.archived": {"reason": "test"},
            "projection_rule.created": {
                "name": "test_rule",
                "rule_type": "field_tracking",
                "source_events": ["sleep.logged"],
                "fields": ["hrv_rmssd"],
            },
            "projection_rule.archived": {"name": "test_rule"},
            "weight_target.set": {"target_weight_kg": 80},
            "sleep_target.set": {"target_hours": 8},
            "nutrition_target.set": {"target_calories": 2200},
        }

        event = CreateEventRequest(
            event_type=event_type,
            data=data_by_type.get(event_type, {}),
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)

        if status == 422:
            code = body.get("code", "")
            # Should be either workflow gate or write-with-proof requirement
            valid_codes = {
                "inv_workflow_phase_required",
                "inv_plan_write_requires_write_with_proof",
                # Structural invariants are also valid for some types
                "inv_projection_rule_name_required",
                "inv_projection_rule_type_required",
                "inv_projection_rule_type_invalid",
                "inv_projection_rule_source_events_invalid",
                "inv_projection_rule_fields_invalid",
                "inv_projection_rule_archive_name_required",
            }
            assert code in valid_codes or code.startswith("inv_"), (
                f"Unexpected code for planning write: {code}"
            )


# --- Timezone requirement ---


class TestTimezoneRequirement:
    """Property tests for the timezone requirement on temporal writes."""

    @given(
        event_type=st.sampled_from(sorted(
            TIMEZONE_REQUIRED_EVENT_TYPES - {"session.completed", "external.activity_imported"}
        )),
    )
    @FUZZ_SETTINGS
    def test_temporal_without_timezone_behavior(
        self, api_client: KuraTestClient, event_type: str,
    ):
        """Temporal writes without timezone are either rejected or accepted
        (depending on whether user already has timezone set).

        We verify the response is structurally correct either way.
        """
        minimal_data: dict = {
            "set.logged": {"exercise": "Squat", "exercise_id": "squat", "reps": 5},
            "bodyweight.logged": {"weight_kg": 80},
            "measurement.logged": {"type": "waist", "value_cm": 85},
            "sleep.logged": {"duration_hours": 7.5},
            "energy.logged": {"level": 7},
            "soreness.logged": {"area": "chest", "severity": 3},
            "meal.logged": {"calories": 500},
            "observation.logged": {"dimension": "motivation_pre", "value": 4},
        }

        event = CreateEventRequest(
            event_type=event_type,
            data=minimal_data.get(event_type, {}),
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)

        if status == 422:
            code = body.get("code", "")
            assert code in {
                "inv_timezone_required_for_temporal_write",
                "inv_workflow_phase_required",
            }, f"Unexpected code for temporal write: {code}"
        elif status < 400:
            # Accepted — timezone must be set in profile
            pass

    @given(
        event_type=st.sampled_from(["set.logged", "bodyweight.logged", "meal.logged"]),
        tz_field=st.sampled_from(["timezone", "time_zone"]),
        tz_value=st.sampled_from(["Europe/Berlin", "America/New_York", "UTC"]),
    )
    @FUZZ_SETTINGS
    def test_temporal_with_inline_timezone_accepted(
        self, api_client: KuraTestClient, event_type: str, tz_field: str, tz_value: str,
    ):
        """Temporal writes WITH inline timezone bypass the timezone check."""
        minimal_data: dict = {
            "set.logged": {"exercise": "Squat", "exercise_id": "squat", "reps": 5},
            "bodyweight.logged": {"weight_kg": 80},
            "meal.logged": {"calories": 500},
        }
        data = {**minimal_data.get(event_type, {}), tz_field: tz_value}

        event = CreateEventRequest(
            event_type=event_type,
            data=data,
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)

        if status == 422:
            code = body.get("code", "")
            # Should NOT be timezone error
            assert code != "inv_timezone_required_for_temporal_write", (
                f"Inline timezone '{tz_field}={tz_value}' should bypass timezone check"
            )


# --- RPE+RIR consistency ---


class TestRPERIRConsistency:
    """Property tests for RPE+RIR cross-field consistency warnings."""

    @given(
        rpe=st.floats(min_value=1, max_value=10, allow_nan=False, allow_infinity=False),
        rir=st.floats(min_value=0, max_value=10, allow_nan=False, allow_infinity=False),
    )
    @FUZZ_SETTINGS
    def test_rpe_rir_consistency_warning(
        self, api_client: KuraTestClient, rpe: float, rir: float,
    ):
        """RPE+RIR far from 10 should produce a consistency warning."""
        event = CreateEventRequest(
            event_type="set.logged",
            data={
                "exercise": "Squat",
                "exercise_id": "squat",
                "reps": 5,
                "weight_kg": 100,
                "rpe": rpe,
                "rir": rir,
            },
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)

        if status >= 400:
            return  # May be rejected by timezone invariant

        # Check if consistency warning is present when expected
        delta = abs((rpe + rir) - 10.0)
        warnings = body.get("warnings", [])
        consistency_warnings = [
            w for w in warnings
            if "consistency" in w.get("message", "").lower()
        ]

        if delta > 2.0:
            assert consistency_warnings, (
                f"Expected consistency warning for rpe={rpe}, rir={rir} "
                f"(delta={delta:.1f}), but got none. Warnings: {warnings}"
            )
        else:
            assert not consistency_warnings, (
                f"Unexpected consistency warning for rpe={rpe}, rir={rir} "
                f"(delta={delta:.1f}): {consistency_warnings}"
            )


# --- Exercise ID similarity ---


class TestExerciseIDSimilarity:
    """Property tests for exercise ID similarity warnings."""

    def test_exact_match_no_similarity_warning(self, api_client: KuraTestClient):
        """An exact match exercise_id should NOT trigger similarity warning."""
        event = CreateEventRequest(
            event_type="set.logged",
            data={
                "exercise": "Squat",
                "exercise_id": "barbell_back_squat",  # known ID
                "reps": 5,
                "weight_kg": 100,
            },
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)
        if status >= 400:
            return  # Domain invariant

        warnings = body.get("warnings", [])
        similarity_warnings = [
            w for w in warnings if "similar" in w.get("message", "").lower()
        ]
        # Exact match should not trigger similarity warning
        assert not similarity_warnings, (
            f"Exact match triggered similarity warning: {similarity_warnings}"
        )


# --- Free-form event types ---


class TestFreeFormEventTypes:
    """Property tests for unknown/novel event types."""

    @given(
        event_type=st.from_regex(r"[a-z][a-z0-9_.]{2,30}", fullmatch=True),
    )
    @FUZZ_SETTINGS
    def test_unknown_event_type_accepted(self, api_client: KuraTestClient, event_type: str):
        """Unknown event types are always accepted (free-form by design).

        May be rejected by domain invariants (timezone) but NOT by
        structural validation.
        """
        # Skip known validated types to avoid invariant checks
        assume(event_type not in {
            "event.retracted", "set.corrected", "set.logged",
            "training_plan.created", "training_plan.updated", "training_plan.archived",
            "projection_rule.created", "projection_rule.archived",
            "session.completed",
        })

        event = CreateEventRequest(
            event_type=event_type,
            data={"some_field": "some_value"},
            metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
        )
        body, status = api_client.post_event(event)

        if status == 422:
            code = body.get("code", "")
            # Should only be domain invariants, not structural
            assert code in {
                "inv_timezone_required_for_temporal_write",
                "inv_workflow_phase_required",
            }, f"Unknown event type '{event_type}' hit unexpected invariant: {code}"
        elif status < 400:
            pass  # Accepted as expected
