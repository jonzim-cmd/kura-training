"""Training plan event generator — training_plan.created."""

from __future__ import annotations

from datetime import datetime, time, timezone

from datagen.exercises import EXERCISES
from datagen.generators.training import SPLIT_TEMPLATES
from datagen.models import AthleteProfile, AthleteState


def generate_training_plan(
    profile: AthleteProfile,
    state: AthleteState,
    day_offset: int,
) -> list[dict]:
    """Generate training_plan.created event (day 0)."""
    freq = profile.training_days_per_week
    template = SPLIT_TEMPLATES.get(freq, SPLIT_TEMPLATES[4])

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    # Distribute training days across the week
    if freq == 3:
        training_days = [0, 2, 4]  # Mon, Wed, Fri
    elif freq == 4:
        training_days = [0, 1, 3, 4]  # Mon, Tue, Thu, Fri
    elif freq == 5:
        training_days = [0, 1, 2, 3, 4]  # Mon-Fri
    elif freq == 6:
        training_days = [0, 1, 2, 3, 4, 5]  # Mon-Sat
    else:
        training_days = [0, 2, 4]

    session_names = {
        3: ["Full Body A", "Full Body B", "Full Body C"],
        4: ["Upper A", "Lower A", "Upper B", "Lower B"],
        5: ["Push", "Pull", "Legs", "Upper", "Lower"],
        6: ["Push A", "Pull A", "Legs A", "Push B", "Pull B", "Legs B"],
    }

    sessions = []
    names = session_names.get(freq, session_names[4])
    for i, day_idx in enumerate(training_days):
        exercise_ids = [ex for ex in template[i] if ex in profile.exercises]
        sessions.append(
            {
                "day": day_names[day_idx],
                "name": names[i] if i < len(names) else f"Session {i + 1}",
                "exercises": [
                    {
                        "exercise_id": ex_id,
                        "exercise": EXERCISES[ex_id].alias_de,
                    }
                    for ex_id in exercise_ids
                ],
            }
        )

    return [
        {
            "event_type": "training_plan.created",
            "data": {
                "sessions": sessions,
            },
            "occurred_at": datetime.combine(
                state.day, time(10, 15), tzinfo=timezone.utc,
            ).isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-plan",
        }
    ]
