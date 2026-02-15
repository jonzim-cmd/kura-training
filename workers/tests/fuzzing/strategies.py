"""Hypothesis strategies for generating Kura API payloads.

Each strategy generates valid or intentionally-broken event data.
The property tests decide which strategies to combine and what to assert.
"""

from __future__ import annotations

import uuid
from typing import Any

from hypothesis import strategies as st

from .contracts import (
    CERTAINTY_FIELDS,
    CERTAINTY_SOURCES,
    CERTAINTY_STATES,
    PLAUSIBILITY_RANGES,
    CreateEventRequest,
    EventMetadata,
)

# --- Atomic value strategies ---

valid_uuid = st.builds(lambda: str(uuid.uuid4()))
invalid_uuid = st.sampled_from(["not-a-uuid", "123", "", "01234567-89ab-cdef-0123-456789abcdeg"])
maybe_uuid = st.one_of(valid_uuid, invalid_uuid)

# Locale-aware decimal strings: "8,5", "7.5", "1.000,5"
locale_decimal = st.one_of(
    st.floats(min_value=0, max_value=20, allow_nan=False, allow_infinity=False).map(
        lambda f: f"{f:.1f}".replace(".", ",")
    ),
    st.floats(min_value=0, max_value=20, allow_nan=False, allow_infinity=False).map(
        lambda f: f"{f:.1f}"
    ),
    st.sampled_from(["8,5", "7.5", "1.000,5", "2,5", "10,0"]),
)

non_numeric_junk = st.sampled_from([
    "abc", "null", "true", "[]", "{}", "NaN", "Infinity", "-Infinity",
    "", " ", "  ", "\t", "\n", "🏋️", "八", "1.2.3", "1,2,3",
])

# Values that could be numbers or could break parsing
numeric_or_junk = st.one_of(
    st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-1000, max_value=1000),
    locale_decimal,
    non_numeric_junk,
)

# Exercise IDs (canonical form)
exercise_ids = st.sampled_from([
    "barbell_back_squat", "barbell_bench_press", "barbell_deadlift",
    "overhead_press", "barbell_row", "pull_up", "dumbbell_curl",
    "leg_press", "lat_pulldown", "cable_fly",
])

# Exercise names (user-facing, multilingual)
exercise_names = st.sampled_from([
    "Kniebeuge", "Bankdrücken", "Kreuzheben", "Squat", "Bench Press",
    "Deadlift", "スクワット", "Sentadilla", "Приседание",
])

# Similar exercise IDs (for Jaro-Winkler similarity tests)
similar_exercise_ids = st.sampled_from([
    ("barbell_back_squat", "barbell_back_sqat"),   # typo
    ("barbell_bench_press", "barbell_benchpress"),   # missing underscore
    ("barbell_deadlift", "barbell_dead_lift"),       # extra underscore
    ("overhead_press", "overhead_pres"),             # truncation
    ("pull_up", "pullup"),                           # compound
])

idempotency_key = st.builds(lambda: str(uuid.uuid4()))
empty_or_whitespace = st.sampled_from(["", " ", "  ", "\t", "\n", "  \t\n  "])

# --- Event data strategies ---


@st.composite
def set_logged_data(draw: st.DrawFn, *, valid: bool = True) -> dict[str, Any]:
    """Generate set.logged event data."""
    data: dict[str, Any] = {
        "exercise": draw(exercise_names),
        "exercise_id": draw(exercise_ids),
        "reps": draw(st.integers(min_value=1, max_value=30)) if valid else draw(numeric_or_junk),
    }
    if draw(st.booleans()):
        data["weight_kg"] = draw(
            st.floats(min_value=20, max_value=300, allow_nan=False, allow_infinity=False)
            if valid else numeric_or_junk
        )
    if draw(st.booleans()):
        data["rpe"] = draw(
            st.floats(min_value=1, max_value=10, allow_nan=False, allow_infinity=False)
            if valid else numeric_or_junk
        )
    if draw(st.booleans()):
        data["rir"] = draw(
            st.floats(min_value=0, max_value=10, allow_nan=False, allow_infinity=False)
            if valid else numeric_or_junk
        )
    if draw(st.booleans()):
        data["set_type"] = draw(st.sampled_from(["warmup", "working", "backoff", "amrap"]))
    return data


@st.composite
def retraction_data(draw: st.DrawFn, *, valid: bool = True) -> dict[str, Any]:
    """Generate event.retracted event data."""
    data: dict[str, Any] = {}
    if valid:
        data["retracted_event_id"] = draw(valid_uuid)
        if draw(st.booleans()):
            data["retracted_event_type"] = draw(st.sampled_from([
                "set.logged", "bodyweight.logged", "meal.logged",
            ]))
        if draw(st.booleans()):
            data["reason"] = "Test retraction"
    else:
        # Draw from a mix of valid and broken variants
        variant = draw(st.sampled_from([
            "missing_id", "invalid_uuid", "empty_id",
            "empty_type", "whitespace_type",
        ]))
        if variant == "missing_id":
            pass  # no retracted_event_id at all
        elif variant == "invalid_uuid":
            data["retracted_event_id"] = draw(invalid_uuid)
        elif variant == "empty_id":
            data["retracted_event_id"] = ""
        elif variant == "empty_type":
            data["retracted_event_id"] = draw(valid_uuid)
            data["retracted_event_type"] = ""
        elif variant == "whitespace_type":
            data["retracted_event_id"] = draw(valid_uuid)
            data["retracted_event_type"] = draw(empty_or_whitespace)
    return data


@st.composite
def set_correction_data(draw: st.DrawFn, *, valid: bool = True) -> dict[str, Any]:
    """Generate set.corrected event data."""
    if valid:
        data: dict[str, Any] = {
            "target_event_id": draw(valid_uuid),
            "changed_fields": {
                draw(st.sampled_from(["weight_kg", "reps", "rpe", "rir", "rest_seconds"])):
                    draw(st.integers(min_value=1, max_value=200)),
            },
        }
        if draw(st.booleans()):
            data["reason"] = "Correction"
        return data

    variant = draw(st.sampled_from([
        "missing_target", "invalid_uuid", "missing_changed_fields",
        "changed_fields_not_object", "changed_fields_empty",
        "changed_fields_empty_key",
    ]))
    data = {}
    if variant == "missing_target":
        data["changed_fields"] = {"weight_kg": 100}
    elif variant == "invalid_uuid":
        data["target_event_id"] = draw(invalid_uuid)
        data["changed_fields"] = {"weight_kg": 100}
    elif variant == "missing_changed_fields":
        data["target_event_id"] = draw(valid_uuid)
    elif variant == "changed_fields_not_object":
        data["target_event_id"] = draw(valid_uuid)
        data["changed_fields"] = draw(st.sampled_from([
            "not_an_object", 42, [1, 2, 3], True, None,
        ]))
    elif variant == "changed_fields_empty":
        data["target_event_id"] = draw(valid_uuid)
        data["changed_fields"] = {}
    elif variant == "changed_fields_empty_key":
        data["target_event_id"] = draw(valid_uuid)
        data["changed_fields"] = {"": 100}
    return data


@st.composite
def projection_rule_created_data(draw: st.DrawFn, *, valid: bool = True) -> dict[str, Any]:
    """Generate projection_rule.created event data."""
    if valid:
        rule_type = draw(st.sampled_from(["field_tracking", "categorized_tracking"]))
        fields = draw(st.lists(
            st.sampled_from(["hrv_rmssd", "deep_sleep_pct", "fiber_g", "name", "dose"]),
            min_size=1, max_size=5, unique=True,
        ))
        data: dict[str, Any] = {
            "name": draw(st.from_regex(r"[a-z][a-z0-9_]{2,20}", fullmatch=True)),
            "rule_type": rule_type,
            "source_events": draw(st.lists(
                st.sampled_from(["sleep.logged", "supplement.logged", "meal.logged"]),
                min_size=1, max_size=3, unique=True,
            )),
            "fields": fields,
        }
        if rule_type == "categorized_tracking":
            data["group_by"] = fields[0]
        return data

    variant = draw(st.sampled_from([
        "missing_name", "empty_name", "missing_type", "invalid_type",
        "missing_source_events", "empty_source_events",
        "missing_fields", "empty_fields",
        "source_events_too_large", "fields_too_large",
        "missing_group_by", "group_by_not_in_fields",
    ]))
    data = {}
    base = {
        "name": "test_rule",
        "rule_type": "field_tracking",
        "source_events": ["sleep.logged"],
        "fields": ["hrv_rmssd"],
    }
    if variant == "missing_name":
        data = {k: v for k, v in base.items() if k != "name"}
    elif variant == "empty_name":
        data = {**base, "name": ""}
    elif variant == "missing_type":
        data = {k: v for k, v in base.items() if k != "rule_type"}
    elif variant == "invalid_type":
        data = {**base, "rule_type": draw(st.sampled_from(["invalid", "sum", "count", ""]))}
    elif variant == "missing_source_events":
        data = {k: v for k, v in base.items() if k != "source_events"}
    elif variant == "empty_source_events":
        data = {**base, "source_events": []}
    elif variant == "missing_fields":
        data = {k: v for k, v in base.items() if k != "fields"}
    elif variant == "empty_fields":
        data = {**base, "fields": []}
    elif variant == "source_events_too_large":
        data = {**base, "source_events": [f"event.type_{i}" for i in range(33)]}
    elif variant == "fields_too_large":
        data = {**base, "fields": [f"field_{i}" for i in range(65)]}
    elif variant == "missing_group_by":
        data = {**base, "rule_type": "categorized_tracking"}
    elif variant == "group_by_not_in_fields":
        data = {
            **base,
            "rule_type": "categorized_tracking",
            "group_by": "not_a_field",
        }
    return data


@st.composite
def projection_rule_archived_data(draw: st.DrawFn, *, valid: bool = True) -> dict[str, Any]:
    """Generate projection_rule.archived event data."""
    if valid:
        return {"name": draw(st.from_regex(r"[a-z][a-z0-9_]{2,20}", fullmatch=True))}
    variant = draw(st.sampled_from(["missing_name", "empty_name", "whitespace_name"]))
    if variant == "missing_name":
        return {}
    elif variant == "empty_name":
        return {"name": ""}
    return {"name": draw(empty_or_whitespace)}


@st.composite
def session_completed_data(draw: st.DrawFn, *, valid: bool = True) -> dict[str, Any]:
    """Generate session.completed event data with certainty fields."""
    data: dict[str, Any] = {}

    if valid:
        for field_name in CERTAINTY_FIELDS:
            if not draw(st.booleans()):
                continue  # skip this field entirely
            state = draw(st.sampled_from(CERTAINTY_STATES))
            data[f"{field_name}_state"] = state
            data[f"{field_name}_source"] = draw(st.sampled_from(CERTAINTY_SOURCES))

            if state == "confirmed":
                data[field_name] = draw(st.integers(min_value=1, max_value=10))
            elif state == "inferred":
                data[field_name] = draw(st.integers(min_value=1, max_value=10))
                data[f"{field_name}_evidence_claim_id"] = f"claim_{uuid.uuid4().hex[:24]}"
            elif state == "unresolved":
                data[f"{field_name}_unresolved_reason"] = "User declined to rate"
    else:
        # Generate specifically broken certainty data
        variant = draw(st.sampled_from([
            "confirmed_no_value",
            "inferred_no_value",
            "inferred_no_evidence",
            "unresolved_has_value",
            "unresolved_no_reason",
        ]))
        field_name = draw(st.sampled_from(CERTAINTY_FIELDS))

        if variant == "confirmed_no_value":
            data[f"{field_name}_state"] = "confirmed"
            data[f"{field_name}_source"] = "explicit"
            # deliberately missing numeric value
        elif variant == "inferred_no_value":
            data[f"{field_name}_state"] = "inferred"
            data[f"{field_name}_source"] = "inferred"
            data[f"{field_name}_evidence_claim_id"] = f"claim_{uuid.uuid4().hex[:24]}"
            # deliberately missing numeric value
        elif variant == "inferred_no_evidence":
            data[f"{field_name}_state"] = "inferred"
            data[f"{field_name}_source"] = "inferred"
            data[field_name] = 4
            # deliberately missing evidence_claim_id
        elif variant == "unresolved_has_value":
            data[f"{field_name}_state"] = "unresolved"
            data[field_name] = 3  # should NOT have value
            data[f"{field_name}_unresolved_reason"] = "some reason"
        elif variant == "unresolved_no_reason":
            data[f"{field_name}_state"] = "unresolved"
            # deliberately missing unresolved_reason

    return data


@st.composite
def training_plan_data(draw: st.DrawFn, *, valid: bool = True) -> dict[str, Any]:
    """Generate training_plan.created/updated event data."""
    if valid:
        sessions = []
        for _ in range(draw(st.integers(min_value=1, max_value=4))):
            exercises = []
            for _ in range(draw(st.integers(min_value=1, max_value=5))):
                ex: dict[str, Any] = {"exercise_id": draw(exercise_ids)}
                if draw(st.booleans()):
                    ex["target_rpe"] = draw(st.floats(
                        min_value=1, max_value=10, allow_nan=False, allow_infinity=False,
                    ))
                if draw(st.booleans()):
                    ex["target_rir"] = draw(st.floats(
                        min_value=0, max_value=10, allow_nan=False, allow_infinity=False,
                    ))
                exercises.append(ex)
            sessions.append({
                "day": draw(st.sampled_from(["monday", "tuesday", "wednesday", "thursday", "friday"])),
                "name": f"Session {draw(st.integers(min_value=1, max_value=10))}",
                "exercises": exercises,
            })
        return {"name": "Test Plan", "sessions": sessions}

    variant = draw(st.sampled_from([
        "sessions_not_array", "exercises_not_array",
        "rpe_invalid_type", "rpe_out_of_range",
        "rir_invalid_type", "rir_out_of_range",
    ]))
    if variant == "sessions_not_array":
        return {"sessions": "not an array"}
    elif variant == "exercises_not_array":
        return {"sessions": [{"day": "monday", "exercises": "not an array"}]}
    elif variant == "rpe_invalid_type":
        return {"sessions": [{"exercises": [{"target_rpe": draw(non_numeric_junk)}]}]}
    elif variant == "rpe_out_of_range":
        return {"sessions": [{"exercises": [{"target_rpe": draw(
            st.floats(min_value=11, max_value=100, allow_nan=False, allow_infinity=False)
        )}]}]}
    elif variant == "rir_invalid_type":
        return {"sessions": [{"exercises": [{"target_rir": draw(non_numeric_junk)}]}]}
    elif variant == "rir_out_of_range":
        return {"sessions": [{"exercises": [{"target_rir": draw(
            st.one_of(
                st.floats(min_value=-100, max_value=-0.1, allow_nan=False, allow_infinity=False),
                st.floats(min_value=10.1, max_value=100, allow_nan=False, allow_infinity=False),
            )
        )}]}]}
    return {}


@st.composite
def plausibility_data(draw: st.DrawFn, event_type: str, *, within_range: bool = True) -> dict[str, Any]:
    """Generate event data that may or may not trigger plausibility warnings."""
    ranges = PLAUSIBILITY_RANGES.get(event_type, {})
    data: dict[str, Any] = {}

    for field_name, (min_v, max_v) in ranges.items():
        if within_range:
            data[field_name] = draw(st.floats(
                min_value=min_v, max_value=max_v,
                allow_nan=False, allow_infinity=False,
            ))
        else:
            # Generate out-of-range values
            data[field_name] = draw(st.one_of(
                st.floats(min_value=max_v + 0.1, max_value=max_v * 10,
                          allow_nan=False, allow_infinity=False),
                st.floats(min_value=min_v * 10 if min_v < 0 else -100,
                          max_value=min_v - 0.1 if min_v > 0 else min_v - 1,
                          allow_nan=False, allow_infinity=False),
            ))

    # Add required fields for the event type
    if event_type == "set.logged":
        data.setdefault("exercise", "Bench Press")
        data.setdefault("exercise_id", "barbell_bench_press")
        data.setdefault("reps", draw(st.integers(min_value=1, max_value=20)))
    elif event_type == "bodyweight.logged":
        pass  # weight_kg already in data
    elif event_type == "soreness.logged":
        data.setdefault("area", "chest")
    elif event_type == "energy.logged":
        pass  # level already in data
    elif event_type == "measurement.logged":
        data.setdefault("type", "waist")
    elif event_type == "sleep.logged":
        pass  # duration_hours already in data
    elif event_type == "meal.logged":
        pass  # optional fields already in data

    return data


# --- Composite event strategies ---

@st.composite
def valid_event(draw: st.DrawFn, event_type: str | None = None) -> CreateEventRequest:
    """Generate a valid event of any or specified type."""
    if event_type is None:
        event_type = draw(st.sampled_from([
            "set.logged", "bodyweight.logged", "meal.logged",
            "sleep.logged", "soreness.logged", "energy.logged",
            "measurement.logged", "profile.updated", "preference.set",
        ]))

    generators: dict[str, Any] = {
        "set.logged": set_logged_data(valid=True),
        "bodyweight.logged": st.fixed_dictionaries({"weight_kg": st.floats(
            min_value=30, max_value=200, allow_nan=False, allow_infinity=False,
        )}),
        "meal.logged": st.fixed_dictionaries({
            "calories": st.integers(min_value=100, max_value=3000),
            "protein_g": st.integers(min_value=5, max_value=200),
        }),
        "sleep.logged": st.fixed_dictionaries({
            "duration_hours": st.floats(min_value=3, max_value=12, allow_nan=False, allow_infinity=False),
        }),
        "soreness.logged": st.fixed_dictionaries({
            "area": st.sampled_from(["chest", "back", "shoulders", "legs"]),
            "severity": st.integers(min_value=0, max_value=10),
        }),
        "energy.logged": st.fixed_dictionaries({
            "level": st.integers(min_value=1, max_value=10),
        }),
        "measurement.logged": st.fixed_dictionaries({
            "type": st.sampled_from(["waist", "chest", "arm", "thigh"]),
            "value_cm": st.floats(min_value=20, max_value=200, allow_nan=False, allow_infinity=False),
        }),
        "profile.updated": st.fixed_dictionaries({
            "experience_level": st.sampled_from(["beginner", "intermediate", "advanced"]),
        }),
        "preference.set": st.fixed_dictionaries({
            "key": st.sampled_from(["timezone", "unit_system", "language"]),
            "value": st.sampled_from(["Europe/Berlin", "metric", "de"]),
        }),
    }

    data = draw(generators.get(event_type, st.just({})))
    return CreateEventRequest(
        event_type=event_type,
        data=data,
        metadata=EventMetadata(idempotency_key=str(uuid.uuid4())),
    )
