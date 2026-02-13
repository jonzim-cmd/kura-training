"""API contracts — typed representations of Kura API request/response shapes.

These are the Python-side contracts that mirror the Rust API types.
They are NOT validation logic — they are just data shapes for test generation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class EventMetadata:
    idempotency_key: str = ""
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"idempotency_key": self.idempotency_key}
        if self.session_id is not None:
            d["session_id"] = self.session_id
        return d


@dataclass
class CreateEventRequest:
    event_type: str
    data: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: EventMetadata = field(default_factory=lambda: EventMetadata(
        idempotency_key=str(uuid.uuid4())
    ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "data": self.data,
            "metadata": self.metadata.to_dict(),
        }


@dataclass
class BatchCreateEventsRequest:
    events: list[CreateEventRequest]

    def to_dict(self) -> dict[str, Any]:
        return {"events": [e.to_dict() for e in self.events]}


@dataclass
class EventWarning:
    field: str
    message: str
    severity: str = "warning"


@dataclass
class PolicyViolation:
    """Expected structure of a 422 policy violation response."""
    code: str
    message: str
    field: str | None = None
    received: Any = None
    docs_hint: str | None = None


# --- Invariant code catalog ---
# Every invariant code the API can return, organized by category.

INVARIANT_CODES = {
    # set.logged intensity
    "inv_set_rpe_invalid_type",
    "inv_set_rpe_out_of_range",
    "inv_set_rir_invalid_type",
    "inv_set_rir_out_of_range",

    # training_plan intensity
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

    # event.retracted
    "inv_retraction_target_required",
    "inv_retraction_target_invalid_uuid",
    "inv_retraction_type_invalid",

    # set.corrected
    "inv_set_correction_target_required",
    "inv_set_correction_target_invalid_uuid",
    "inv_set_correction_changed_fields_required",
    "inv_set_correction_changed_fields_invalid",
    "inv_set_correction_changed_fields_empty",
    "inv_set_correction_changed_fields_key_invalid",

    # projection_rule.created
    "inv_projection_rule_name_required",
    "inv_projection_rule_type_required",
    "inv_projection_rule_type_invalid",
    "inv_projection_rule_source_events_invalid",
    "inv_projection_rule_fields_invalid",
    "inv_projection_rule_source_events_too_large",
    "inv_projection_rule_fields_too_large",
    "inv_projection_rule_group_by_required",
    "inv_projection_rule_group_by_not_in_fields",

    # projection_rule.archived
    "inv_projection_rule_archive_name_required",

    # legacy domain invariants
    "inv_workflow_phase_required",
    "inv_plan_write_requires_write_with_proof",
    "inv_timezone_required_for_temporal_write",

    # session.completed certainty
    "session_feedback_confirmed_missing_value",
    "session_feedback_inferred_missing_value",
    "session_feedback_inferred_missing_evidence",
    "session_feedback_unresolved_has_value",
    "session_feedback_unresolved_missing_reason",
}

# Event types that have critical invariant validation
VALIDATED_EVENT_TYPES = {
    "set.logged",
    "event.retracted",
    "set.corrected",
    "training_plan.created",
    "training_plan.updated",
    "projection_rule.created",
    "projection_rule.archived",
    "session.completed",
}

# Event types that need timezone
TIMEZONE_REQUIRED_EVENT_TYPES = {
    "set.logged",
    "session.completed",
    "bodyweight.logged",
    "measurement.logged",
    "sleep.logged",
    "energy.logged",
    "soreness.logged",
    "meal.logged",
    "observation.logged",
    "external.activity_imported",
}

# Event types that need onboarding closed/overridden
PLANNING_OR_COACHING_EVENT_TYPES = {
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    "projection_rule.created",
    "projection_rule.archived",
    "weight_target.set",
    "sleep_target.set",
    "nutrition_target.set",
}

# Plan writes that require write-with-proof
PLAN_EVENT_TYPES = {
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
}

# Plausibility check ranges (event_type -> field -> (min, max))
PLAUSIBILITY_RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "set.logged": {
        "weight_kg": (0.0, 500.0),
        "reps": (0, 100),
        "rpe": (1.0, 10.0),
        "rir": (0.0, 10.0),
    },
    "bodyweight.logged": {
        "weight_kg": (20.0, 300.0),
    },
    "meal.logged": {
        "calories": (0.0, 5000.0),
        "protein_g": (0.0, 500.0),
        "carbs_g": (0.0, 500.0),
        "fat_g": (0.0, 500.0),
    },
    "sleep.logged": {
        "duration_hours": (0.0, 20.0),
    },
    "soreness.logged": {
        "severity": (1, 5),
    },
    "energy.logged": {
        "level": (1.0, 10.0),
    },
    "measurement.logged": {
        "value_cm": (1.0, 300.0),
    },
}

# Certainty contract fields
CERTAINTY_FIELDS = ["enjoyment", "perceived_quality", "perceived_exertion"]
CERTAINTY_STATES = ["confirmed", "inferred", "unresolved"]
CERTAINTY_SOURCES = ["explicit", "user_confirmed", "estimated", "inferred"]
