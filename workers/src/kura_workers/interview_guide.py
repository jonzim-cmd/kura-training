"""Static interview guide for agent-conducted onboarding.

Describes the information landscape — how to conduct an adaptive interview,
what areas to cover, and how to approach each one. This is purely about
the conversational strategy for getting to know a new user.

Event schemas (what fields each event type expects) live in event_conventions.py.
The interview guide references event types via coverage_areas.produces but does
not define their structure.

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
