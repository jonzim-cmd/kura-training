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
            "description": (
                "User attributes (delta merge, latest per field wins). Includes "
                "baseline-profile tri-state markers for known/unknown/deferred "
                "(Decision 13 INV-006)."
            ),
            "fields": {
                "experience_level": "string (optional: beginner, intermediate, advanced)",
                "training_modality": "string (optional: strength, endurance, hybrid, crossfit)",
                "training_frequency_per_week": "number (optional)",
                "available_equipment": "list[string] (optional)",
                "primary_location": "string (optional: commercial_gym, home_gym, outdoor)",
                "current_program": "string (optional)",
                "communication_style": "string (optional, free text — how the user wants to be addressed)",
                "age": "number (optional, baseline in years)",
                "date_of_birth": "string (optional, ISO date; alternative to age baseline)",
                "age_deferred": "boolean (optional, explicit deferred marker when age/date_of_birth is postponed)",
                "date_of_birth_deferred": "boolean (optional, explicit deferred marker when DOB is postponed)",
                "bodyweight_kg": "number (optional, baseline bodyweight snapshot)",
                "bodyweight_deferred": "boolean (optional, explicit deferred marker when bodyweight is postponed)",
                "sex": "string (optional, free text or categorical e.g. female/male/intersex/non_binary)",
                "sex_deferred": "boolean (optional, explicit deferred marker for sex)",
                "body_fat_pct": "number (optional, body composition context)",
                "body_fat_pct_deferred": "boolean (optional, explicit deferred marker for body fat context)",
                "body_composition_deferred": "boolean (optional, explicit deferred marker for optional body composition context)",
            },
            "example": {
                "experience_level": "intermediate",
                "training_modality": "strength",
                "training_frequency_per_week": 4,
                "age_deferred": True,
                "bodyweight_deferred": True,
            },
            "tri_state_semantics": (
                "For baseline fields, use one of: known value, explicit deferred marker, "
                "or leave unknown. Required baseline slots (age/date_of_birth and bodyweight) "
                "should not stay silently unknown once mentioned in onboarding."
            ),
            "null_semantics": (
                "Set any field to null to clear it. The field remains in the "
                "profile with null value, indicating 'no longer set'. "
                "Example: {\"date_of_birth\": null} clears the birth date."
            ),
        },
        "preference.set": {
            "description": "User preference (latest per key wins)",
            "fields": {
                "key": "string (required)",
                "value": "any (required)",
                "repair_provenance": {
                    "source_type": "string (optional: explicit|inferred|estimated|user_confirmed)",
                    "confidence": "number (optional, 0..1)",
                    "confidence_band": "string (optional: low|medium|high)",
                    "applies_scope": "string (optional: single_set|exercise_session|session)",
                    "reason": "string (optional)",
                },
            },
            "example": {"key": "unit_system", "value": "metric"},
            "common_keys": [
                "unit_system",
                "language",
                "timezone",
                "time_zone",
                "nutrition_tracking",
                "population_priors_opt_in",
                "challenge_mode",
                "challenge_mode_intro_seen",
                "autonomy_scope",
                "verbosity",
                "confirmation_strictness",
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
                "rpe": (
                    "number|string (optional, 1..10). "
                    "Locale decimal commas are accepted (e.g. '8,5')."
                ),
                "rir": (
                    "number|string (optional, 0..10 reps in reserve). "
                    "Locale decimal commas are accepted (e.g. '2,5')."
                ),
                "rest_seconds": (
                    "number (optional by default, mention-bound: required to persist when pause/rest is mentioned)"
                ),
                "tempo": (
                    "string (optional by default, mention-bound: required to persist when tempo is mentioned)"
                ),
                "set_type": "string (optional: warmup, working, backoff, amrap)",
                "load_context": {
                    "implements_type": (
                        "string (optional: free_weight|barbell|dumbbell|kettlebell|"
                        "machine|selectorized_machine|plate_loaded_machine|cable_machine|bodyweight)"
                    ),
                    "equipment_profile": "string (optional, e.g. smith_machine, trap_bar, rings)",
                    "comparability_group": (
                        "string (optional, explicit semantic boundary key for progression comparability)"
                    ),
                    "location_context": "string (optional, e.g. commercial_gym, home_gym, outdoor)",
                },
            },
            "example": {
                "exercise": "Kniebeuge",
                "exercise_id": "barbell_back_squat",
                "weight_kg": 100,
                "reps": 5,
                "rpe": 8,
                "rir": 2,
                "rest_seconds": 120,
                "tempo": "3-1-1-0",
                "load_context": {
                    "implements_type": "free_weight",
                    "equipment_profile": "barbell",
                    "comparability_group": "free_weight",
                    "location_context": "commercial_gym",
                },
            },
            "normalization": (
                "ALWAYS set exercise_id when you recognize the exercise. "
                "If this is the first time the user uses a term, also create "
                "exercise.alias_created in the same batch. Check user.aliases first."
            ),
            "intensity_semantics": {
                "rpe_range": "[1, 10]",
                "rir_range": "[0, 10]",
                "consistency_guideline": "rpe + rir should usually be near 10; larger gaps trigger warnings",
                "precedence": (
                    "If both are explicitly present, keep both explicit values. "
                    "Inference only applies when exactly one signal is present."
                ),
            },
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
        "session.logged": {
            "description": (
                "Unified modality-neutral session event with block-based structure. "
                "Supports strength, endurance, sprint, plyometrics, and hybrid sessions."
            ),
            "schema_version": "session.logged.v1",
            "fields": {
                "contract_version": "string (required, must be 'session.logged.v1')",
                "session_meta": {
                    "sport": "string (optional, e.g. strength|running|cycling|hybrid)",
                    "started_at": "string (optional, ISO timestamp)",
                    "ended_at": "string (optional, ISO timestamp, >= started_at)",
                    "timezone": "string (optional, IANA timezone)",
                    "session_id": "string (optional, stable session grouping key)",
                    "notes": "string (optional)",
                },
                "blocks": [
                    {
                        "block_type": (
                            "string (required: strength_set|explosive_power|plyometric_reactive|"
                            "sprint_accel_maxv|speed_endurance|interval_endurance|"
                            "continuous_endurance|tempo_threshold|circuit_hybrid|"
                            "technique_coordination|recovery_session)"
                        ),
                        "capability_target": "string (optional, e.g. max_strength|vo2max|max_velocity)",
                        "dose": {
                            "work": (
                                "object (required; at least one of duration_seconds|distance_meters|reps|contacts)"
                            ),
                            "recovery": (
                                "object (optional; same shape as work when rest/recovery is defined)"
                            ),
                            "repeats": "number (optional, >=1)",
                        },
                        "intensity_anchors_status": (
                            "string (optional: provided|not_applicable). "
                            "For performance blocks, provide at least one intensity anchor "
                            "or set not_applicable explicitly."
                        ),
                        "intensity_anchors": [
                            {
                                "measurement_state": (
                                    "string (required: measured|estimated|inferred|"
                                    "not_measured|not_applicable)"
                                ),
                                "value": "any (optional, required when state is measured|estimated|inferred unless reference is set)",
                                "unit": "string (optional, e.g. min_per_km|watt|bpm|rpe|borg_cr10|pct_reference)",
                                "reference": "string (optional, anchor reference context)",
                            }
                        ],
                        "metrics": (
                            "object (optional map). Each metric entry requires measurement_state and "
                            "can carry value/unit/reference."
                        ),
                        "subjective_response": (
                            "object (optional map). Same measurement contract for perception signals."
                        ),
                        "provenance": {
                            "source_type": "string (optional: manual|imported|inferred|corrected)",
                            "source_ref": "string (optional)",
                            "confidence": "number (optional, 0..1)",
                        },
                    }
                ],
                "subjective_response": (
                    "object (optional map at session level). "
                    "Use this for session-level RPE/Borg, pain, freshness, etc."
                ),
                "provenance": {
                    "source_type": "string (optional: manual|imported|inferred|corrected)",
                    "source_ref": "string (optional)",
                    "confidence": "number (optional, 0..1)",
                },
            },
            "example": {
                "contract_version": "session.logged.v1",
                "session_meta": {
                    "sport": "running",
                    "timezone": "Europe/Berlin",
                    "session_id": "2026-02-14-track-1",
                },
                "blocks": [
                    {
                        "block_type": "interval_endurance",
                        "dose": {
                            "work": {"duration_seconds": 120},
                            "recovery": {"duration_seconds": 60},
                            "repeats": 8,
                        },
                        "intensity_anchors": [
                            {
                                "measurement_state": "measured",
                                "unit": "min_per_km",
                                "value": 4.0,
                            },
                            {
                                "measurement_state": "measured",
                                "unit": "borg_cr10",
                                "value": 7,
                            },
                        ],
                        "metrics": {
                            "heart_rate_avg": {
                                "measurement_state": "not_measured",
                            }
                        },
                        "provenance": {"source_type": "manual"},
                    }
                ],
                "provenance": {"source_type": "manual"},
            },
            "completeness_policy": (
                "No global HR/Power/GPS requirement. "
                "Completeness is block-specific: log_valid requires reconstructable dose and "
                "anchor policy per block; missing optional sensor data must be explicit as "
                "measurement_state=not_measured or not_applicable."
            ),
        },
        "exercise.alias_created": {
            "description": "Maps user term to canonical exercise ID",
            "fields": {
                "alias": "string (required, what the user says)",
                "exercise_id": "string (required, canonical ID)",
                "confidence": "string (confirmed or inferred)",
                "repair_provenance": {
                    "source_type": "string (optional: explicit|inferred|estimated|user_confirmed)",
                    "confidence": "number (optional, 0..1)",
                    "confidence_band": "string (optional: low|medium|high)",
                    "applies_scope": "string (optional: single_set|exercise_session|session)",
                    "reason": "string (optional)",
                },
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
                "sessions": (
                    "list[{day, name, exercises}] (optional). "
                    "exercise entries may include target_rir (0..10) and/or target_rpe (1..10). "
                    "Locale decimal commas are accepted for intensity fields."
                ),
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
                "sessions": (
                    "list[{day, name, exercises}] (optional). "
                    "exercise entries may include target_rir (0..10) and/or target_rpe (1..10). "
                    "Locale decimal commas are accepted for intensity fields."
                ),
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
        "session.completed": {
            "description": (
                "Post-session subjective feedback. Canonical event for enjoyment, "
                "session quality, exertion summary, pain/discomfort, and context."
            ),
            "fields": {
                "enjoyment": "number (optional, 1..5)",
                "enjoyment_state": "string (optional: confirmed|inferred|unresolved)",
                "enjoyment_source": "string (optional: explicit|user_confirmed|estimated|inferred)",
                "enjoyment_evidence_claim_id": "string (required when enjoyment_state/source is inferred)",
                "enjoyment_unresolved_reason": "string (required when enjoyment_state=unresolved)",
                "perceived_quality": "number (optional, 1..5)",
                "perceived_quality_state": "string (optional: confirmed|inferred|unresolved)",
                "perceived_quality_source": "string (optional: explicit|user_confirmed|estimated|inferred)",
                "perceived_quality_evidence_claim_id": "string (required when perceived_quality_state/source is inferred)",
                "perceived_quality_unresolved_reason": "string (required when perceived_quality_state=unresolved)",
                "perceived_exertion": "number (optional, 1..10, session summary)",
                "perceived_exertion_state": "string (optional: confirmed|inferred|unresolved)",
                "perceived_exertion_source": "string (optional: explicit|user_confirmed|estimated|inferred)",
                "perceived_exertion_evidence_claim_id": "string (required when perceived_exertion_state/source is inferred)",
                "perceived_exertion_unresolved_reason": "string (required when perceived_exertion_state=unresolved)",
                "pain_discomfort": "number (optional, 0..10)",
                "pain_signal": "boolean|string (optional)",
                "context": "string (optional, free text session reflection)",
                "notes": "string (optional, alias of context)",
                "summary": "string (optional, alias of context)",
            },
            "metadata_fields": {
                "session_id": (
                    "string (recommended). Must match the training session grouping "
                    "used by set.logged for load-to-feedback alignment."
                ),
            },
            "example": {
                "enjoyment": 4,
                "enjoyment_state": "confirmed",
                "perceived_quality": 4,
                "perceived_quality_state": "confirmed",
                "perceived_exertion": 7,
                "perceived_exertion_state": "confirmed",
                "pain_discomfort": 1,
                "pain_signal": False,
                "context": "Session felt good and focused; squat top set moved well.",
            },
            "backward_compatibility": (
                "Legacy session.completed payloads with free-text fields (notes/summary/feeling) "
                "remain supported; normalization maps them without data loss where feasible."
            ),
            "certainty_contract": (
                "If *_state is inferred (or *_source=inferred), the matching "
                "*_evidence_claim_id is mandatory. If *_state is unresolved, omit "
                "the numeric value and include *_unresolved_reason."
            ),
        },
        "observation.logged": {
            "description": (
                "Open-world observation contract for session facts that do not fit "
                "fixed schemas. Validation tier is inferred from dimension naming."
            ),
            "fields": {
                "dimension": (
                    "string (required). Canonical key for what is observed; "
                    "known dimensions use stable names, provisional dimensions should "
                    "start with x_/custom./provisional."
                ),
                "value": "any (required)",
                "unit": "string (optional)",
                "scale": "object|string|number (optional)",
                "context_text": "string (optional, raw evidence-preserving snippet)",
                "tags": "list[string] (optional)",
                "confidence": "number (optional, 0..1; defaults to 0.5)",
                "provenance": {
                    "source_type": (
                        "string (recommended: explicit|inferred|estimated|user_confirmed)"
                    ),
                    "source_event_id": "string (optional, UUID)",
                    "source_claim_id": "string (optional, evidence.claim.logged claim_id)",
                },
                "scope": {
                    "level": "string (optional: session|exercise|set; defaults to session)",
                    "session_id": "string (optional)",
                    "exercise_id": "string (optional)",
                },
            },
            "example": {
                "dimension": "motivation_pre",
                "value": 4,
                "scale": {"min": 1, "max": 5},
                "context_text": "Motivation ist heute bei 4 von 5.",
                "tags": ["pre_session", "self_report"],
                "confidence": 0.95,
                "provenance": {
                    "source_type": "explicit",
                    "source_claim_id": "claim_87c9156a21f5b2014f431ba3",
                },
                "scope": {"level": "session", "session_id": "session-2026-02-12-a"},
            },
            "known_dimensions": [
                "motivation_pre",
                "discomfort_signal",
                "jump_baseline",
            ],
            "validation_tiers": {
                "known": "dimension is in known registry and gets typed normalization",
                "provisional": "dimension starts with x_/custom./provisional.",
                "unknown": "dimension is stored safely with quality flags",
            },
        },
        "external.activity_imported": {
            "description": (
                "Canonical external activity import artifact produced by the "
                "file/connector ingestion pipeline."
            ),
            "fields": {
                "contract_version": "string (required, external_activity.v1)",
                "source": {
                    "provider": "string (required)",
                    "provider_user_id": "string (required)",
                    "external_activity_id": "string (required)",
                    "external_event_version": "string (optional)",
                    "ingestion_method": "string (required: file_import|connector_api|manual_backfill)",
                },
                "workout": "object (required, canonical workout slice)",
                "session": "object (required, canonical session slice)",
                "sets": "list[object] (optional, canonical set slice entries)",
                "provenance": "object (required, mapping + field provenance)",
            },
            "example": {
                "contract_version": "external_activity.v1",
                "source": {
                    "provider": "garmin",
                    "provider_user_id": "athlete-123",
                    "external_activity_id": "activity-98765",
                    "external_event_version": "8",
                    "ingestion_method": "file_import",
                },
                "workout": {"workout_type": "run", "duration_seconds": 1800, "distance_meters": 5000},
                "session": {"started_at": "2026-02-12T06:30:00+00:00"},
                "provenance": {
                    "mapping_version": "garmin-v1",
                    "mapped_at": "2026-02-12T09:15:00+00:00",
                },
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
        "workflow.onboarding.closed": {
            "description": (
                "Explicit transition marker: onboarding phase is closed and planning/coaching "
                "actions are now allowed by workflow gate."
            ),
            "fields": {
                "reason": "string (optional)",
                "closed_by": "string (optional: user_confirmed|agent_confirmed|system_auto)",
                "missing_requirements_at_close": "list[string] (optional, usually empty)",
            },
            "example": {
                "reason": "User confirmed onboarding summary.",
                "closed_by": "user_confirmed",
                "missing_requirements_at_close": [],
            },
        },
        "workflow.onboarding.override_granted": {
            "description": (
                "Explicit user override marker that temporarily allows planning/coaching "
                "before onboarding closure."
            ),
            "fields": {
                "reason": "string (required, explicit user intent)",
                "confirmed_by": "string (optional: user)",
                "scope": "string (optional: current_topic|current_session|manual)",
            },
            "example": {
                "reason": "User asked to create a plan now despite open onboarding.",
                "confirmed_by": "user",
                "scope": "current_session",
            },
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
                    "clarification_requested, workflow_violation, "
                    "workflow_phase_transition_closed, workflow_override_used, "
                    "viz_shown, viz_skipped, viz_source_bound, viz_fallback_used, "
                    "viz_confusion_signal"
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
        "evidence.claim.logged": {
            "description": (
                "Lineage evidence for deterministic free-text extraction. "
                "Links a parsed claim to the persisted target event."
            ),
            "fields": {
                "claim_id": "string (required, deterministic claim identifier)",
                "claim_type": "string (required, e.g. set_context.rest_seconds)",
                "value": "any (required, normalized claim value)",
                "unit": "string (optional)",
                "scope": {
                    "level": "string (required: session|set|exercise)",
                    "event_type": "string (required)",
                    "session_id": "string (optional)",
                    "exercise_id": "string (optional)",
                },
                "confidence": "number (required, 0..1)",
                "provenance": {
                    "source_field": "string (required: notes|context_text|utterance)",
                    "source_text": "string (required, raw utterance fragment)",
                    "source_text_span": {
                        "start": "number (required, 0-based char index)",
                        "end": "number (required, exclusive char index)",
                        "text": "string (required, matched text)",
                    },
                    "parser_version": "string (required)",
                },
                "lineage": {
                    "event_id": "string (required, UUID target event)",
                    "event_type": "string (required)",
                    "lineage_type": "string (required: supports|corrects|supersedes)",
                },
            },
            "example": {
                "claim_id": "claim_87c9156a21f5b2014f431ba3",
                "claim_type": "set_context.rest_seconds",
                "value": 90,
                "unit": "seconds",
                "scope": {
                    "level": "set",
                    "event_type": "set.logged",
                    "session_id": "session-2026-02-12",
                    "exercise_id": "barbell_back_squat",
                },
                "confidence": 0.95,
                "provenance": {
                    "source_field": "utterance",
                    "source_text": "3x5 squat, rest 90 sec, rir 2, tempo 3-1-x-0",
                    "source_text_span": {"start": 11, "end": 22, "text": "rest 90 sec"},
                    "parser_version": "mention_parser.v1",
                },
                "lineage": {
                    "event_id": "01956abc-def0-7000-8000-000000000001",
                    "event_type": "set.logged",
                    "lineage_type": "supports",
                },
            },
        },
        "set.corrected": {
            "description": (
                "Patch-style correction for a previously logged set without "
                "retract-and-relog. Keeps immutable history and applies overlay "
                "in projections."
            ),
            "fields": {
                "target_event_id": "string (required, UUID of target set.logged event)",
                "changed_fields": (
                    "object (required). Each key is a field to patch. "
                    "Value can be scalar or {value, repair_provenance}."
                ),
                "reason": "string (optional)",
                "repair_provenance": {
                    "source_type": "string (optional: explicit|inferred|estimated|user_confirmed)",
                    "confidence": "number (optional, 0..1)",
                    "confidence_band": "string (optional: low|medium|high)",
                    "applies_scope": "string (optional: single_set|exercise_session|session)",
                    "reason": "string (optional)",
                },
            },
            "metadata_fields": {
                "idempotency_key": (
                    "string (required in metadata). Use stable key to ensure duplicate "
                    "correction submissions are idempotent."
                ),
            },
            "example": {
                "target_event_id": "01956abc-def0-7000-8000-000000000001",
                "changed_fields": {
                    "rest_seconds": {
                        "value": 90,
                        "repair_provenance": {
                            "source_type": "explicit",
                            "confidence": 1.0,
                            "confidence_band": "high",
                            "applies_scope": "single_set",
                            "reason": "User clarified rest between work sets.",
                        },
                    }
                },
                "reason": "Add missing rest_seconds from explicit mention.",
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
                "repair_provenance": {
                    "source_type": "string (optional: explicit|inferred|estimated|user_confirmed)",
                    "confidence": "number (optional, 0..1)",
                    "confidence_band": "string (optional: low|medium|high)",
                    "applies_scope": "string (optional: single_set|exercise_session|session)",
                    "reason": "string (optional)",
                },
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
        "quality.consistency.review.decided": {
            "description": (
                "Records the user's decision on a proactive consistency finding "
                "surfaced in the chat. The agent must log this event after the "
                "user approves, declines, or snoozes a proposed fix."
            ),
            "category": "system_quality",
            "fields": {
                "required": {
                    "item_ids": "string[] (IDs of consistency_inbox items decided on)",
                    "decision": "string (approve|decline|snooze)",
                    "decision_source": "string (chat_explicit)",
                },
                "optional": {
                    "snooze_until": "ISO 8601 timestamp (required when decision=snooze)",
                    "agent_summary": "string (what was proposed to the user)",
                },
            },
            "example": {
                "item_ids": ["ci-bench-press-weight-drift-2026w06"],
                "decision": "approve",
                "decision_source": "chat_explicit",
            },
            "usage": (
                "Log after user responds to a consistency finding surfaced by "
                "the agent. The decision controls whether a fix is executed "
                "(approve), deferred (snooze), or dismissed (decline). "
                "No fix may be executed without a prior approve decision."
            ),
        },
    }
