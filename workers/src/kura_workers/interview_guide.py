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
        "philosophy": [
            "Follow the conversation, don't interrogate. Let the user's answers guide direction.",
            "Extract multiple data points from one answer. 'Ich squatte 3x/Woche 120kg' = frequency + exercise + load range.",
            "Use structured options for factual info (modality, units), open questions for narrative (goals, history).",
            "Produce events incrementally during the conversation — don't batch at the end.",
            "Respect 'later' or 'not interested'. Mark as deferred, move on. Partial data is still useful.",
            "After covering an area, briefly mention what Kura can now do with it — motivates sharing.",
            "The user is always in control. If they want to talk about something else, follow them.",
        ],
        "phases": {
            "broad_sweep": {
                "goal": "Cover all major areas at surface level. Build a map, not a deep profile.",
                "rules": "1-2 exchanges per area. Use categorical approach where possible. Move on after 3 exchanges max on one area.",
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
