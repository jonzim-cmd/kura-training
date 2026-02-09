"""Body composition event generators — bodyweight.logged, measurement.logged.

Daily bodyweight with realistic variance, periodic body measurements.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timezone

from datagen.models import AthleteProfile, AthleteState


def generate_bodyweight(
    profile: AthleteProfile,
    state: AthleteState,
    rng: random.Random,
    day_offset: int,
) -> list[dict]:
    """Generate bodyweight.logged event (daily morning weigh-in)."""
    # Daily variance: ±0.5kg from current trend
    daily_noise = rng.gauss(0, 0.5)
    weight = round(state.bodyweight_kg + daily_noise, 1)
    # Clamp to plausibility
    weight = max(20.0, min(300.0, weight))

    return [
        {
            "event_type": "bodyweight.logged",
            "data": {
                "weight_kg": weight,
                "time_of_day": "morning",
                "conditions": "fasted",
            },
            "occurred_at": datetime.combine(
                state.day, time(7, 0), tzinfo=timezone.utc,
            ).isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-bodyweight",
        }
    ]


def generate_measurements(
    profile: AthleteProfile,
    state: AthleteState,
    rng: random.Random,
    day_offset: int,
) -> list[dict]:
    """Generate measurement.logged events (every 2-4 weeks).

    Measurements: waist, chest, upper arm (bicep).
    """
    # Only measure every 14-28 days
    if day_offset == 0 or day_offset % rng.randint(14, 28) != 0:
        return []

    events: list[dict] = []
    # Baseline measurements scale with bodyweight
    bw_ratio = state.bodyweight_kg / profile.bodyweight_kg

    measurements = [
        ("waist", 82.0 * bw_ratio, 2.0),
        ("chest", 98.0 * bw_ratio, 1.5),
        ("arm", 35.0 * bw_ratio, 1.0),
    ]

    for idx, (mtype, baseline, noise_std) in enumerate(measurements):
        value = round(baseline + rng.gauss(0, noise_std), 1)
        value = max(1.0, min(300.0, value))

        events.append(
            {
                "event_type": "measurement.logged",
                "data": {
                    "type": mtype,
                    "value_cm": value,
                },
                "occurred_at": datetime.combine(
                    state.day, time(7, 15), tzinfo=timezone.utc,
                ).isoformat(),
                "idempotency_key": f"datagen-{profile.name}-d{day_offset}-measurement-{mtype}",
            }
        )

    return events


def update_bodyweight_trend(
    state: AthleteState,
    calorie_target: int,
    actual_calories: int,
) -> None:
    """Slowly drift bodyweight based on calorie balance.

    ~0.1kg/week per 500kcal surplus/deficit.
    """
    daily_surplus = actual_calories - calorie_target
    # 7700 kcal ≈ 1kg body mass change
    daily_weight_change = daily_surplus / 7700.0
    state.bodyweight_kg += daily_weight_change
    state.bodyweight_kg = round(state.bodyweight_kg, 2)
