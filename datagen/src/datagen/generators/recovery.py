"""Recovery event generators — sleep.logged, soreness.logged, energy.logged.

Sleep correlates with fatigue model (sleep debt), soreness appears day after
training based on volume and novelty, energy reflects the cross-domain state.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone

from datagen.exercises import EXERCISES, muscle_groups_for_exercises
from datagen.fatigue import compute_effective_energy
from datagen.models import AthleteProfile, AthleteState


def generate_sleep(
    profile: AthleteProfile,
    state: AthleteState,
    rng: random.Random,
    day_offset: int,
    *,
    novel_fields: bool = False,
) -> list[dict]:
    """Generate sleep.logged event (last night's sleep, logged this morning)."""
    # Duration: normal distribution around profile baseline
    duration = rng.gauss(profile.sleep_avg_hours, profile.sleep_std_hours)
    # Fatigue can worsen sleep slightly (restlessness)
    if state.fatigue > 0.6:
        duration -= rng.uniform(0, 0.5)
    duration = max(3.0, min(12.0, duration))
    duration = round(duration, 1)

    # Quality mapping
    if duration < 6.0:
        quality = "poor"
    elif duration < 7.0:
        quality = "fair"
    elif duration < 8.0:
        quality = "good"
    else:
        quality = "excellent"

    # bed_time: consistent baseline ± 30min variance
    bed_hour = 22 + rng.randint(0, 1)
    bed_minute = rng.choice([0, 15, 30, 45])
    bed_time_dt = datetime.combine(
        state.day - timedelta(days=1), time(bed_hour, bed_minute), tzinfo=timezone.utc,
    )

    # wake_time derived from bed_time + duration
    wake_minutes = int(duration * 60)
    wake_time_dt = bed_time_dt + timedelta(minutes=wake_minutes)

    data: dict = {
        "duration_hours": duration,
        "quality": quality,
        "bed_time": bed_time_dt.isoformat(),
        "wake_time": wake_time_dt.isoformat(),
    }

    if novel_fields:
        from datagen.generators.novel_fields import novel_sleep_fields

        extra = novel_sleep_fields(profile, rng, duration)
        if extra:
            data.update(extra)

    return [
        {
            "event_type": "sleep.logged",
            "data": data,
            "occurred_at": datetime.combine(
                state.day, time(7, 30), tzinfo=timezone.utc,
            ).isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-sleep",
        }
    ]


def generate_soreness(
    profile: AthleteProfile,
    state: AthleteState,
    rng: random.Random,
    day_offset: int,
    trained_exercises_yesterday: list[str] | None = None,
) -> list[dict]:
    """Generate soreness.logged events for muscle groups that are sore.

    Soreness appears the day AFTER training (DOMS).
    Severity based on volume + how new the exercise is.
    """
    events: list[dict] = []

    if not trained_exercises_yesterday:
        return events

    # Get muscle groups hit yesterday
    groups = muscle_groups_for_exercises(trained_exercises_yesterday)

    for group in groups:
        # Base severity from volume (more sets = more sore)
        base_severity = rng.uniform(1.5, 3.0)

        # Deload week → less soreness
        if state.mesocycle_week == 4:
            base_severity *= 0.6

        # High fatigue → more soreness
        base_severity += state.fatigue * 0.5

        severity = max(1, min(5, round(base_severity)))

        # Update state soreness for fatigue model
        state.soreness[group] = max(state.soreness.get(group, 0), float(severity))

        events.append(
            {
                "event_type": "soreness.logged",
                "data": {
                    "area": group,
                    "severity": severity,
                },
                "occurred_at": datetime.combine(
                    state.day, time(8, 0), tzinfo=timezone.utc,
                ).isoformat(),
                "idempotency_key": f"datagen-{profile.name}-d{day_offset}-soreness-{group}",
            }
        )

    return events


def generate_energy(
    profile: AthleteProfile,
    state: AthleteState,
    rng: random.Random,
    day_offset: int,
    is_training_day: bool,
    *,
    novel_fields: bool = False,
) -> list[dict]:
    """Generate energy.logged event.

    Energy correlates with sleep debt, fatigue, and varies by time of day.
    Training days log pre-workout energy.
    """
    noise = rng.gauss(0, 0.5)
    energy = compute_effective_energy(state, noise)
    energy_level = round(max(1.0, min(10.0, energy)))

    time_of_day = "pre_workout" if is_training_day else "morning"
    log_hour = 17 if is_training_day else 8
    log_minute = 0

    data: dict = {
        "level": energy_level,
        "time_of_day": time_of_day,
    }

    if novel_fields:
        from datagen.generators.novel_fields import novel_energy_fields

        extra = novel_energy_fields(profile, rng)
        if extra:
            data.update(extra)

    return [
        {
            "event_type": "energy.logged",
            "data": data,
            "occurred_at": datetime.combine(
                state.day, time(log_hour, log_minute), tzinfo=timezone.utc,
            ).isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-energy",
        }
    ]
