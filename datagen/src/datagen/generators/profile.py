"""Profile and goal event generators — profile.updated, goal.set, injury.reported."""

from __future__ import annotations

from datetime import datetime, time, timezone

from datagen.models import AthleteProfile, AthleteState


def generate_profile_events(
    profile: AthleteProfile,
    state: AthleteState,
    day_offset: int,
) -> list[dict]:
    """Generate profile.updated event (day 0 onboarding)."""
    modality = {
        3: "full_body",
        4: "upper_lower",
        5: "push_pull_legs",
        6: "push_pull_legs",
    }.get(profile.training_days_per_week, "full_body")

    return [
        {
            "event_type": "profile.updated",
            "data": {
                "experience_level": profile.experience_level,
                "training_modality": modality,
                "training_frequency_per_week": profile.training_days_per_week,
                "available_equipment": ["barbell", "dumbbells", "cable_machine", "pull_up_bar"],
                "primary_location": "gym",
            },
            "occurred_at": datetime.combine(
                state.day, time(10, 0), tzinfo=timezone.utc,
            ).isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-profile",
        }
    ]


def generate_goal_event(
    profile: AthleteProfile,
    state: AthleteState,
    day_offset: int,
) -> list[dict]:
    """Generate goal.set event (day 0 onboarding)."""
    # Goal: increase squat 1RM by ~10% in 12 weeks
    target_1rm = round(profile.squat_1rm_kg * 1.10, 1)

    return [
        {
            "event_type": "goal.set",
            "data": {
                "goal_type": "strength",
                "target_exercise": "barbell_back_squat",
                "target_1rm_kg": target_1rm,
                "timeframe_weeks": 12,
            },
            "occurred_at": datetime.combine(
                state.day, time(10, 5), tzinfo=timezone.utc,
            ).isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-goal",
        }
    ]


def generate_injury_event(
    profile: AthleteProfile,
    state: AthleteState,
    day_offset: int,
    affected_area: str = "lower_back",
) -> list[dict]:
    """Generate injury.reported event (triggered by fatigue model)."""
    return [
        {
            "event_type": "injury.reported",
            "data": {
                "description": "Leichte Überlastung nach intensiver Trainingsphase",
                "affected_area": affected_area,
                "severity": "mild",
                "since": state.day.isoformat(),
            },
            "occurred_at": datetime.combine(
                state.day, time(20, 0), tzinfo=timezone.utc,
            ).isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-injury",
        }
    ]
