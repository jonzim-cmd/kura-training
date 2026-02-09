"""Static interview guide for agent-conducted onboarding.

Describes the information landscape — what makes dimensions valuable,
how to conduct an adaptive interview, and what events to produce.
Any general LLM reads this and conducts a natural conversation.

See Design Decision 8 (docs/design/008-onboarding-interview.md).
"""

from typing import Any


def get_interview_guide() -> dict[str, Any]:
    """Return the static interview guide for the system layer.

    This is identical for all users. The agent personalizes the
    conversation using the user layer (what's known) and agenda
    layer (what to do).
    """
    return {
        "introduction": {
            "purpose": (
                "Briefly explain what this is about: you have access to Kura, "
                "a system that tracks training data and helps make better decisions over time. "
                "To do that well, you'd like to get to know the user — their training, "
                "goals, and situation. This makes everything more relevant and personal."
            ),
            "tone": (
                "Natural and brief. Not a role announcement — you are the same agent "
                "the user already knows. Kura gives you context, not a new identity. "
                "Don't say 'I am your training agent'. Just explain what Kura does "
                "and why getting to know them helps."
            ),
            "orientation": (
                "Give the user a sense of what to expect: how long (a few minutes), "
                "what you'll ask about (training, goals, situation, preferences), "
                "and why (so Kura can track patterns and give better feedback over time). "
                "This helps the user feel oriented and more willing to share openly."
            ),
            "example": (
                "Ich habe Zugriff auf Kura — ein System, das dein Training trackt "
                "und über die Zeit Muster erkennen kann. Damit das wirklich nützlich wird, "
                "würde ich dich gerne kurz kennenlernen: Was du trainierst, was dich antreibt, "
                "wo es gerade hakt. Dauert nur ein paar Minuten — ich frage nach deinem "
                "Training, deinen Zielen und deiner Situation, damit Kura dir wirklich "
                "relevantes Feedback geben kann. Du kannst jederzeit abbrechen oder "
                "das Thema wechseln."
            ),
        },
        "philosophy": [
            "Follow the conversation, don't interrogate. Let the user's answers guide direction.",
            "ONE question per message. Always. Reflect what you heard first, then ask one thing.",
            "Never stack multiple questions ('Wie oft? Feste Tage? Kg oder Lbs?'). This overwhelms.",
            "Extract multiple data points from one answer. 'Ich squatte 3x/Woche 120kg' = frequency + exercise + load range.",
            "If the user goes deep on something, follow them. Don't rush the checklist.",
            "Use structured options for factual info (modality, units), open questions for narrative (goals, history).",
            "Produce events incrementally during the conversation — don't batch at the end.",
            "Respect 'later' or 'not interested'. Mark as deferred, move on. Partial data is still useful.",
            "After covering an area, briefly mention what Kura can now do with it — motivates sharing.",
            "The user is always in control. If they want to talk about something else, follow them.",
        ],
        "phases": {
            "broad_sweep": {
                "goal": "Cover all major areas at surface level. Build a map, not a deep profile.",
                "rules": "One question at a time. Reflect before asking. Move on naturally, don't rush.",
                "when": "User is new or most coverage areas are uncovered.",
            },
            "targeted_depth": {
                "goal": "Go deeper on areas where the user showed interest or where gaps remain.",
                "rules": "Use context_seeds from dimension metadata to guide depth. Focus on areas marked needs_depth in coverage.",
                "when": "Broad sweep complete. Coverage map shows specific gaps.",
            },
            "wrap_up": {
                "goal": "Summarize what was learned. Confirm key items. Show what Kura can now do.",
                "rules": "Review coverage. Highlight dimensions that are now active. Mention what improves with more data.",
                "when": "Most areas covered or user signals they're done.",
            },
        },
        "coverage_areas": [
            {
                "area": "training_background",
                "description": "Experience level, training modality, years of training",
                "approach": "categorical",
                "produces": ["profile.updated"],
                "examples": ["Kraft/Ausdauer/Hybrid?", "Wie lange trainierst du schon?"],
            },
            {
                "area": "goals",
                "description": "What the user wants to achieve — strength, hypertrophy, weight loss, health",
                "approach": "narrative",
                "produces": ["goal.set"],
                "examples": ["Was willst du erreichen?", "Hast du konkrete Ziele?"],
            },
            {
                "area": "exercise_vocabulary",
                "description": "What exercises the user does and what they call them",
                "approach": "conversational",
                "produces": ["exercise.alias_created"],
                "examples": ["Emerges naturally when discussing training. Map user terms to canonical IDs."],
            },
            {
                "area": "unit_preferences",
                "description": "Measurement system (kg/lbs, km/miles)",
                "approach": "categorical",
                "produces": ["preference.set"],
                "examples": ["Kg oder Lbs?"],
            },
            {
                "area": "injuries",
                "description": "Current injuries, limitations, areas to be careful with",
                "approach": "categorical_then_narrative",
                "produces": ["injury.reported", "profile.updated"],
                "examples": ["Hast du aktuell Verletzungen oder Einschränkungen?", "→ If yes, details"],
            },
            {
                "area": "equipment",
                "description": "Available training equipment and location",
                "approach": "categorical",
                "produces": ["profile.updated"],
                "examples": ["Wo trainierst du? Was hast du an Equipment?"],
            },
            {
                "area": "schedule",
                "description": "Training frequency and typical schedule",
                "approach": "categorical",
                "produces": ["profile.updated"],
                "examples": ["Wie oft trainierst du pro Woche?"],
            },
            {
                "area": "nutrition_interest",
                "description": "Whether user wants to track nutrition",
                "approach": "categorical",
                "produces": ["preference.set"],
                "examples": ["Willst du Ernährung tracken, oder erstmal nur Training?"],
            },
            {
                "area": "current_program",
                "description": "Current training program or approach",
                "approach": "narrative",
                "produces": ["profile.updated", "program.started"],
                "examples": ["Folgst du einem Programm? Welchem?"],
            },
        ],
        "event_conventions": {
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
            },
            "preference.set": {
                "description": "User preference (latest per key wins)",
                "fields": {"key": "string (required)", "value": "any (required)"},
                "example": {"key": "unit_system", "value": "metric"},
                "common_keys": ["unit_system", "language", "nutrition_tracking"],
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
            "profile.updated": {
                "description": "User attributes (delta merge, latest per field wins)",
                "fields": {
                    "experience_level": "string (optional: beginner, intermediate, advanced)",
                    "training_modality": "string (optional: strength, endurance, hybrid, crossfit)",
                    "training_frequency_per_week": "number (optional)",
                    "available_equipment": "list[string] (optional)",
                    "primary_location": "string (optional: commercial_gym, home_gym, outdoor)",
                    "current_program": "string (optional)",
                },
                "example": {
                    "experience_level": "intermediate",
                    "training_modality": "strength",
                    "training_frequency_per_week": 4,
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
            # --- Tracking event conventions (ongoing, post-onboarding) ---
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
            # --- Target events (Soll-Werte) ---
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
        },
    }


# Coverage area names — used for validation and coverage computation
COVERAGE_AREAS = [
    "training_background",
    "goals",
    "exercise_vocabulary",
    "unit_preferences",
    "injuries",
    "equipment",
    "schedule",
    "nutrition_interest",
    "current_program",
]
