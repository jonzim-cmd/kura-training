"""Target event generators — *_target.set events (day 0 onboarding)."""

from __future__ import annotations

from datetime import datetime, time, timezone

from datagen.models import AthleteProfile, AthleteState


def generate_target_events(
    profile: AthleteProfile,
    state: AthleteState,
    day_offset: int,
) -> list[dict]:
    """Generate all target events for onboarding (day 0)."""
    events: list[dict] = []
    base_time = datetime.combine(state.day, time(10, 10), tzinfo=timezone.utc)

    # Weight target
    events.append(
        {
            "event_type": "weight_target.set",
            "data": {
                "target_weight_kg": profile.bodyweight_kg,  # maintain weight
                "strategy": "maintain",
            },
            "occurred_at": base_time.isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-target-weight",
        }
    )

    # Sleep target
    events.append(
        {
            "event_type": "sleep_target.set",
            "data": {
                "target_hours": profile.sleep_avg_hours,
                "target_bed_time": "22:30",
            },
            "occurred_at": base_time.isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-target-sleep",
        }
    )

    # Nutrition target
    events.append(
        {
            "event_type": "nutrition_target.set",
            "data": {
                "target_calories": profile.calorie_target,
                "target_protein_g": profile.protein_target_g,
                "target_carbs_g": int(profile.calorie_target * 0.45 / 4),
                "target_fat_g": int(profile.calorie_target * 0.25 / 9),
            },
            "occurred_at": base_time.isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-target-nutrition",
        }
    )

    return events
