"""Adversarial scenario templates for LLM-driven fuzzing.

Each scenario describes an edge case category that an LLM can expand into
concrete event payloads. The LLM generates creative variations that
property-based tests can't easily reach (e.g., multilingual ambiguity,
semantic contradictions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdversarialScenario:
    """A single adversarial test scenario."""
    id: str
    category: str
    description: str
    events: list[dict[str, Any]]
    expected_behavior: str  # "accepted", "rejected", "warning"
    expected_codes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    seed: str | None = None  # LLM generation seed for reproducibility


@dataclass
class ScenarioResult:
    """Result of executing a scenario against the API."""
    scenario_id: str
    passed: bool
    actual_status: int
    actual_code: str | None
    actual_warnings: list[dict[str, Any]]
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "actual_status": self.actual_status,
            "actual_code": self.actual_code,
            "actual_warnings": self.actual_warnings,
            "error_message": self.error_message,
        }


# --- Built-in scenario templates ---
# These are hand-crafted edge cases that LLM generation will expand upon.

BUILTIN_SCENARIOS: list[AdversarialScenario] = [
    # --- Locale and encoding edge cases ---
    AdversarialScenario(
        id="locale_comma_decimal_rpe",
        category="locale",
        description="RPE with comma decimal (German/European format)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Kniebeuge",
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "weight_kg": 100,
                "rpe": "8,5",
            },
        }],
        expected_behavior="accepted",
        tags=["locale", "decimal", "rpe"],
    ),
    AdversarialScenario(
        id="locale_thousand_separator_weight",
        category="locale",
        description="Weight with thousand separator (1.000,5 kg — European notation)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Deadlift",
                "exercise_id": "barbell_deadlift",
                "reps": 1,
                "weight_kg": "1.000,5",
                "rpe": 10,
            },
        }],
        expected_behavior="accepted",
        tags=["locale", "thousand_separator"],
    ),
    AdversarialScenario(
        id="unicode_exercise_name",
        category="encoding",
        description="Exercise name with Unicode characters (Japanese, emoji)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "スクワット 🏋️",
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "weight_kg": 100,
            },
        }],
        expected_behavior="accepted",
        tags=["unicode", "encoding"],
    ),

    # --- Boundary values ---
    AdversarialScenario(
        id="rpe_exact_boundary_1",
        category="boundary",
        description="RPE exactly at lower boundary (1.0)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Squat", "exercise_id": "squat",
                "reps": 5, "rpe": 1.0,
            },
        }],
        expected_behavior="accepted",
        tags=["boundary", "rpe"],
    ),
    AdversarialScenario(
        id="rpe_exact_boundary_10",
        category="boundary",
        description="RPE exactly at upper boundary (10.0)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Squat", "exercise_id": "squat",
                "reps": 5, "rpe": 10.0,
            },
        }],
        expected_behavior="accepted",
        tags=["boundary", "rpe"],
    ),
    AdversarialScenario(
        id="rpe_just_below_1",
        category="boundary",
        description="RPE just below minimum (0.999)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Squat", "exercise_id": "squat",
                "reps": 5, "rpe": 0.999,
            },
        }],
        expected_behavior="rejected",
        expected_codes=["inv_set_rpe_out_of_range"],
        tags=["boundary", "rpe"],
    ),
    AdversarialScenario(
        id="rpe_just_above_10",
        category="boundary",
        description="RPE just above maximum (10.001)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Squat", "exercise_id": "squat",
                "reps": 5, "rpe": 10.001,
            },
        }],
        expected_behavior="rejected",
        expected_codes=["inv_set_rpe_out_of_range"],
        tags=["boundary", "rpe"],
    ),
    AdversarialScenario(
        id="rir_exact_zero",
        category="boundary",
        description="RIR exactly zero (valid: training to failure)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Squat", "exercise_id": "squat",
                "reps": 5, "rir": 0,
            },
        }],
        expected_behavior="accepted",
        tags=["boundary", "rir"],
    ),
    AdversarialScenario(
        id="rir_negative",
        category="boundary",
        description="RIR negative (-1, invalid)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Squat", "exercise_id": "squat",
                "reps": 5, "rir": -1,
            },
        }],
        expected_behavior="rejected",
        expected_codes=["inv_set_rir_out_of_range"],
        tags=["boundary", "rir"],
    ),

    # --- Type confusion ---
    AdversarialScenario(
        id="rpe_as_boolean",
        category="type_confusion",
        description="RPE as boolean true (should fail type check)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Squat", "exercise_id": "squat",
                "reps": 5, "rpe": True,
            },
        }],
        expected_behavior="rejected",
        expected_codes=["inv_set_rpe_invalid_type"],
        tags=["type_confusion"],
    ),
    AdversarialScenario(
        id="rpe_as_array",
        category="type_confusion",
        description="RPE as array [8, 5] instead of 8.5",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Squat", "exercise_id": "squat",
                "reps": 5, "rpe": [8, 5],
            },
        }],
        expected_behavior="rejected",
        expected_codes=["inv_set_rpe_invalid_type"],
        tags=["type_confusion"],
    ),
    AdversarialScenario(
        id="rpe_as_null",
        category="type_confusion",
        description="RPE as null (should be treated as absent, accepted)",
        events=[{
            "event_type": "set.logged",
            "data": {
                "exercise": "Squat", "exercise_id": "squat",
                "reps": 5, "rpe": None,
            },
        }],
        expected_behavior="accepted",
        tags=["type_confusion", "null"],
    ),

    # --- Retraction edge cases ---
    AdversarialScenario(
        id="retraction_uuid_with_spaces",
        category="retraction",
        description="Retracted event ID with leading/trailing spaces",
        events=[{
            "event_type": "event.retracted",
            "data": {
                "retracted_event_id": "  01956abc-def0-7000-8000-000000000001  ",
            },
        }],
        expected_behavior="accepted",
        tags=["retraction", "whitespace"],
    ),
    AdversarialScenario(
        id="retraction_nil_uuid",
        category="retraction",
        description="Retracted event ID as nil UUID (all zeros)",
        events=[{
            "event_type": "event.retracted",
            "data": {
                "retracted_event_id": "00000000-0000-0000-0000-000000000000",
            },
        }],
        expected_behavior="accepted",
        tags=["retraction", "nil_uuid"],
    ),

    # --- Certainty contract edge cases ---
    AdversarialScenario(
        id="certainty_mixed_states",
        category="certainty",
        description="Different certainty states for different fields in same event",
        events=[{
            "event_type": "session.completed",
            "data": {
                "enjoyment": 4,
                "enjoyment_state": "confirmed",
                "enjoyment_source": "explicit",
                "perceived_quality_state": "unresolved",
                "perceived_quality_unresolved_reason": "User didn't comment on quality",
                "perceived_exertion": 7,
                "perceived_exertion_state": "inferred",
                "perceived_exertion_source": "inferred",
                "perceived_exertion_evidence_claim_id": "claim_abc123def456789012345678",
            },
        }],
        expected_behavior="accepted",
        tags=["certainty", "mixed_states"],
    ),
    AdversarialScenario(
        id="certainty_all_unresolved",
        category="certainty",
        description="All certainty fields unresolved (user gave no feedback at all)",
        events=[{
            "event_type": "session.completed",
            "data": {
                "enjoyment_state": "unresolved",
                "enjoyment_unresolved_reason": "User declined all feedback",
                "perceived_quality_state": "unresolved",
                "perceived_quality_unresolved_reason": "User declined all feedback",
                "perceived_exertion_state": "unresolved",
                "perceived_exertion_unresolved_reason": "User declined all feedback",
            },
        }],
        expected_behavior="accepted",
        tags=["certainty", "all_unresolved"],
    ),

    # --- Correction patterns ---
    AdversarialScenario(
        id="correction_many_fields",
        category="correction",
        description="Correction with many changed fields simultaneously",
        events=[{
            "event_type": "set.corrected",
            "data": {
                "target_event_id": "01956abc-def0-7000-8000-000000000001",
                "changed_fields": {
                    "weight_kg": 100,
                    "reps": 5,
                    "rpe": 8,
                    "rir": 2,
                    "rest_seconds": 180,
                },
                "reason": "Multiple corrections after review",
            },
        }],
        expected_behavior="accepted",
        tags=["correction", "multi_field"],
    ),
    AdversarialScenario(
        id="correction_nested_changed_fields",
        category="correction",
        description="Correction with nested repair_provenance in changed_fields",
        events=[{
            "event_type": "set.corrected",
            "data": {
                "target_event_id": "01956abc-def0-7000-8000-000000000001",
                "changed_fields": {
                    "rest_seconds": {
                        "value": 90,
                        "repair_provenance": {
                            "source_type": "explicit",
                            "confidence": 1.0,
                        },
                    },
                },
            },
        }],
        expected_behavior="accepted",
        tags=["correction", "nested"],
    ),

    # --- Projection rule edge cases ---
    AdversarialScenario(
        id="rule_max_source_events",
        category="projection_rule",
        description="Projection rule at max source_events limit (32)",
        events=[{
            "event_type": "projection_rule.created",
            "data": {
                "name": "edge_case_rule",
                "rule_type": "field_tracking",
                "source_events": [f"event.type_{i}" for i in range(32)],
                "fields": ["value"],
            },
        }],
        expected_behavior="accepted",
        tags=["projection_rule", "boundary"],
    ),
    AdversarialScenario(
        id="rule_over_source_events_limit",
        category="projection_rule",
        description="Projection rule over source_events limit (33)",
        events=[{
            "event_type": "projection_rule.created",
            "data": {
                "name": "edge_case_rule",
                "rule_type": "field_tracking",
                "source_events": [f"event.type_{i}" for i in range(33)],
                "fields": ["value"],
            },
        }],
        expected_behavior="rejected",
        expected_codes=["inv_projection_rule_source_events_too_large"],
        tags=["projection_rule", "boundary"],
    ),

    # --- Batch edge cases ---
    AdversarialScenario(
        id="batch_duplicate_idempotency_keys",
        category="batch",
        description="Batch with duplicate idempotency keys within batch",
        events=[
            {
                "event_type": "set.logged",
                "data": {"exercise": "Squat", "exercise_id": "squat", "reps": 5},
                "metadata": {"idempotency_key": "same-key-123"},
            },
            {
                "event_type": "set.logged",
                "data": {"exercise": "Bench", "exercise_id": "bench", "reps": 5},
                "metadata": {"idempotency_key": "same-key-123"},
            },
        ],
        expected_behavior="rejected",
        tags=["batch", "idempotency"],
    ),
]


def get_scenarios_by_category(category: str) -> list[AdversarialScenario]:
    """Get all built-in scenarios in a category."""
    return [s for s in BUILTIN_SCENARIOS if s.category == category]


def get_scenarios_by_tag(tag: str) -> list[AdversarialScenario]:
    """Get all built-in scenarios with a specific tag."""
    return [s for s in BUILTIN_SCENARIOS if tag in s.tags]


def get_all_scenario_ids() -> list[str]:
    """Get all scenario IDs for parametrize."""
    return [s.id for s in BUILTIN_SCENARIOS]
