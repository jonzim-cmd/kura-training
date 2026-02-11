"""Event conventions — the complete catalog of event types and their schemas.

System-level concern: tells the agent WHAT events exist, what fields each
expects, and how to use them correctly. This is the single source of truth
for event structure documentation exposed via the system layer.

Every handler reads specific fields from event data. This module documents
those fields so agents can produce correct events without reading handler code.
"""

from typing import Any


def get_event_conventions() -> dict[str, dict[str, Any]]:
    """Return the complete event type catalog.

    Organized by lifecycle stage for readability, but all entries are
    equal citizens — no hierarchy between onboarding and tracking events.
    """
    return {
        # --- Identity & preferences ---
        "profile.updated": {
            "description": "User attributes (delta merge, latest per field wins)",
            "fields": {
                "experience_level": "string (optional: beginner, intermediate, advanced)",
                "training_modality": "string (optional: strength, endurance, hybrid, crossfit)",
                "training_frequency_per_week": "number (optional)",
                "available_equipment": "list[string] (optional)",
                "primary_location": "string (optional: commercial_gym, home_gym, outdoor)",
                "current_program": "string (optional)",
                "communication_style": "string (optional, free text — how the user wants to be addressed)",
            },
            "example": {
                "experience_level": "intermediate",
                "training_modality": "strength",
                "training_frequency_per_week": 4,
            },
            "null_semantics": (
                "Set any field to null to clear it. The field remains in the "
                "profile with null value, indicating 'no longer set'. "
                "Example: {\"date_of_birth\": null} clears the birth date."
            ),
        },
        "preference.set": {
            "description": "User preference (latest per key wins)",
            "fields": {"key": "string (required)", "value": "any (required)"},
            "example": {"key": "unit_system", "value": "metric"},
            "common_keys": [
                "unit_system",
                "language",
                "nutrition_tracking",
                "population_priors_opt_in",
            ],
        },
        "goal.set": {
            "description": "Training or health goal",
            "fields": {
                "goal_type": "string (strength, hypertrophy, endurance, weight_loss, health)",
                "target_exercise": "string (optional, canonical exercise_id)",
                "target_1rm_kg": "number (optional)",
                "timeframe_weeks": "number (optional)",
                "description": "string (optional, free text)",
            },
            "example": {
                "goal_type": "strength",
                "target_exercise": "barbell_back_squat",
                "target_1rm_kg": 140,
                "timeframe_weeks": 12,
            },
        },
        "injury.reported": {
            "description": "Current injury or limitation",
            "fields": {
                "description": "string (required, free text)",
                "affected_area": "string (optional: knee, shoulder, back, etc.)",
                "severity": "string (optional: mild, moderate, severe)",
                "since": "string (optional, ISO date)",
            },
            "example": {
                "description": "Leichtes Ziehen im linken Knie bei tiefen Squats",
                "affected_area": "knee",
                "severity": "mild",
            },
        },
        # --- Training ---
        "set.logged": {
            "description": "A single training set (the core training event)",
            "fields": {
                "exercise": "string (required, what the user says)",
                "exercise_id": "string (required when recognized, canonical ID)",
                "weight_kg": "number (required for weighted exercises)",
                "reps": "number (required)",
                "rpe": "number (optional, 1-10)",
                "set_type": "string (optional: warmup, working, backoff, amrap)",
            },
            "example": {
                "exercise": "Kniebeuge",
                "exercise_id": "barbell_back_squat",
                "weight_kg": 100,
                "reps": 5,
                "rpe": 8,
            },
            "normalization": (
                "ALWAYS set exercise_id when you recognize the exercise. "
                "If this is the first time the user uses a term, also create "
                "exercise.alias_created in the same batch. Check user.aliases first."
            ),
            "metadata_fields": {
                "session_id": (
                    "string (recommended). Groups sets into a logical training session. "
                    "Format is free — e.g. '2026-02-09-upper-a', a UUID, or any string. "
                    "When multiple sessions happen on the same day, session_id is the "
                    "only way to separate them. If omitted, the system falls back to "
                    "grouping by date (one session per day)."
                ),
            },
        },
        "exercise.alias_created": {
            "description": "Maps user term to canonical exercise ID",
            "fields": {
                "alias": "string (required, what the user says)",
                "exercise_id": "string (required, canonical ID)",
                "confidence": "string (confirmed or inferred)",
            },
            "example": {
                "alias": "Kniebeuge",
                "exercise_id": "barbell_back_squat",
                "confidence": "confirmed",
            },
        },
        "training_plan.created": {
            "description": "Create a new training plan",
            "fields": {
                "name": "string (optional, plan name)",
                "plan_id": "string (optional, defaults to 'default')",
                "sessions": "list[{day, name, exercises}] (optional)",
                "cycle_weeks": "number (optional)",
                "notes": "string (optional)",
            },
            "example": {
                "name": "Upper/Lower Split",
                "sessions": [
                    {
                        "day": "monday",
                        "name": "Upper Body A",
                        "exercises": ["bench_press", "overhead_press"],
                    },
                ],
            },
        },
        "training_plan.updated": {
            "description": "Update an existing training plan (delta merge)",
            "fields": {
                "plan_id": "string (optional, defaults to 'default')",
                "name": "string (optional)",
                "sessions": "list[{day, name, exercises}] (optional)",
                "cycle_weeks": "number (optional)",
                "notes": "string (optional)",
            },
            "example": {"plan_id": "default", "name": "Updated Plan Name"},
        },
        "training_plan.archived": {
            "description": "Archive a training plan",
            "fields": {
                "plan_id": "string (optional, defaults to 'default')",
                "reason": "string (optional)",
            },
            "example": {"plan_id": "default", "reason": "Switching to new program"},
        },
        "program.started": {
            "description": "Marks that the user started a named program",
            "fields": {
                "name": "string (required, program name)",
                "program_id": "string (optional, external or internal identifier)",
                "phase": "string (optional, e.g. week_1, accumulation)",
                "notes": "string (optional)",
            },
            "example": {
                "name": "5/3/1",
                "program_id": "531-boring-but-big",
                "phase": "week_1",
            },
        },
        # --- Body composition ---
        "bodyweight.logged": {
            "description": "Body weight measurement",
            "fields": {
                "weight_kg": "number (required)",
                "time_of_day": "string (optional: morning, evening)",
                "conditions": "string (optional: fasted, post-meal)",
            },
            "example": {"weight_kg": 82.5, "time_of_day": "morning"},
        },
        "measurement.logged": {
            "description": "Body measurement (e.g. waist, chest, arm)",
            "fields": {
                "type": "string (required: waist, chest, arm, thigh, etc.)",
                "value_cm": "number (required)",
                "side": "string (optional: left, right)",
            },
            "example": {"type": "waist", "value_cm": 85.0},
        },
        # --- Recovery ---
        "sleep.logged": {
            "description": "Sleep entry for one night",
            "fields": {
                "duration_hours": "number (required)",
                "quality": "string (optional: poor, fair, good, excellent)",
                "bed_time": "string (optional, HH:MM)",
                "wake_time": "string (optional, HH:MM)",
            },
            "example": {
                "duration_hours": 7.5,
                "quality": "good",
                "bed_time": "23:00",
                "wake_time": "06:30",
            },
        },
        "soreness.logged": {
            "description": "Muscle soreness report",
            "fields": {
                "area": "string (required: chest, back, shoulders, legs, etc.)",
                "severity": "number (required, 1-5 scale)",
                "notes": "string (optional)",
            },
            "example": {"area": "chest", "severity": 3},
        },
        "energy.logged": {
            "description": "Subjective energy level",
            "fields": {
                "level": "number (required, 1-10 scale)",
                "time_of_day": "string (optional: morning, pre-workout, evening)",
            },
            "example": {"level": 7, "time_of_day": "pre-workout"},
        },
        # --- Nutrition ---
        "meal.logged": {
            "description": "Nutrition entry for a single meal",
            "fields": {
                "calories": "number (optional)",
                "protein_g": "number (optional)",
                "carbs_g": "number (optional)",
                "fat_g": "number (optional)",
                "meal_type": "string (optional: breakfast, lunch, dinner, snack)",
                "description": "string (optional, free text)",
            },
            "example": {
                "calories": 750,
                "protein_g": 45,
                "carbs_g": 80,
                "fat_g": 25,
                "meal_type": "lunch",
            },
        },
        # --- Targets (Soll-Werte) ---
        "weight_target.set": {
            "description": "Set body weight goal",
            "fields": {
                "target_weight_kg": "number (required)",
                "target_date": "string (optional, ISO date)",
                "strategy": "string (optional: slow_cut, aggressive_cut, lean_bulk, maintain)",
            },
            "example": {
                "target_weight_kg": 80,
                "target_date": "2026-06-01",
                "strategy": "slow_cut",
            },
        },
        "sleep_target.set": {
            "description": "Set sleep goal",
            "fields": {
                "target_hours": "number (required)",
                "target_bed_time": "string (optional, HH:MM)",
            },
            "example": {"target_hours": 8, "target_bed_time": "22:30"},
        },
        "nutrition_target.set": {
            "description": "Set daily nutrition targets",
            "fields": {
                "target_calories": "number (optional)",
                "target_protein_g": "number (optional)",
                "target_carbs_g": "number (optional)",
                "target_fat_g": "number (optional)",
            },
            "example": {
                "target_calories": 2200,
                "target_protein_g": 160,
                "target_carbs_g": 220,
                "target_fat_g": 70,
            },
        },
        # --- Adaptive Projections (Phase 3, Decision 10) ---
        "projection_rule.created": {
            "description": (
                "Agent creates a custom projection rule. The system builds "
                "a pre-computed projection from matching events. Rules are "
                "declarative — the agent says WHAT to track, the system does the work."
            ),
            "fields": {
                "name": "string (required) — unique rule identifier per user",
                "type": (
                    "string (required: field_tracking, categorized_tracking) — "
                    "field_tracking extracts numeric fields as time series, "
                    "categorized_tracking groups events by a category field"
                ),
                "source_events": "list[string] (required) — event types to process",
                "fields": "list[string] (required) — data fields to extract/aggregate",
                "group_by": (
                    "string (required for categorized_tracking) — "
                    "field to group events by (must be in fields list)"
                ),
            },
            "example": {
                "name": "hrv_tracking",
                "type": "field_tracking",
                "source_events": ["sleep.logged"],
                "fields": ["hrv_rmssd", "deep_sleep_pct"],
            },
        },
        "projection_rule.archived": {
            "description": (
                "Deactivate a custom projection rule. The corresponding "
                "custom projection will be deleted."
            ),
            "fields": {
                "name": "string (required) — rule name to archive",
            },
            "example": {"name": "hrv_tracking"},
        },
        # --- Data corrections ---
        "learning.signal.logged": {
            "description": (
                "Canonical implicit-learning telemetry signal for cross-session "
                "quality/friction/outcome/correction analysis."
            ),
            "fields": {
                "schema_version": "number (required, telemetry schema version)",
                "signal_type": (
                    "string (required) — one of: quality_issue_detected, "
                    "repair_proposed, repair_simulated_safe, repair_simulated_risky, "
                    "repair_auto_applied, repair_auto_rejected, repair_verified_closed, "
                    "save_handshake_verified, save_handshake_pending, "
                    "save_claim_mismatch_attempt, correction_applied, correction_undone, "
                    "clarification_requested"
                ),
                "category": "string (required: quality_signal|friction_signal|outcome_signal|correction_signal)",
                "captured_at": "string (required, ISO datetime)",
                "user_ref": {
                    "pseudonymized_user_id": "string (required, salted deterministic pseudonym)"
                },
                "signature": {
                    "issue_type": "string (required, 'none' if not applicable)",
                    "invariant_id": "string (required, 'none' if not applicable)",
                    "agent_version": "string (required)",
                    "workflow_phase": "string (required)",
                    "modality": "string (required)",
                    "confidence_band": "string (required: low|medium|high)",
                },
                "cluster_signature": "string (required, stable hash for clustering)",
                "attributes": "object (optional, signal-specific details)",
            },
            "example": {
                "schema_version": 1,
                "signal_type": "save_claim_mismatch_attempt",
                "category": "friction_signal",
                "captured_at": "2026-02-11T12:12:00Z",
                "user_ref": {"pseudonymized_user_id": "u_7ac3f5be2ab8d93e55f1f8c3"},
                "signature": {
                    "issue_type": "save_claim_mismatch_attempt",
                    "invariant_id": "INV-002",
                    "agent_version": "api_agent_v1",
                    "workflow_phase": "agent_write_with_proof",
                    "modality": "chat",
                    "confidence_band": "medium",
                },
                "cluster_signature": "ls_40a2cb4d2f5e6f2443e0",
                "attributes": {
                    "requested_event_count": 2,
                    "receipt_count": 2,
                    "verification_status": "pending",
                },
            },
        },
        "event.retracted": {
            "description": (
                "Retracts a previously logged event. The retracted event "
                "will be excluded from all projection computations. "
                "This is the universal correction mechanism — works for any event type."
            ),
            "fields": {
                "retracted_event_id": "string (required, UUID of the event being retracted)",
                "retracted_event_type": (
                    "string (recommended, event_type of the retracted event "
                    "— enables efficient processing without DB lookup)"
                ),
                "reason": "string (optional, why the retraction is being made)",
            },
            "example": {
                "retracted_event_id": "01956abc-def0-7000-8000-000000000001",
                "retracted_event_type": "bodyweight.logged",
                "reason": "Typo: entered 150kg instead of 85kg",
            },
            "usage": (
                "To correct a wrong event: retract it and log the correct "
                "replacement event in the same batch. This is the standard "
                "correction pattern. Never try to 'update' an existing event."
            ),
        },
    }
