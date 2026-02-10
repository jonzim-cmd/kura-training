"""Novel field generators for Phase 2 pattern detection testing.

Profile-specific extra fields that handlers don't know about.
Different athletes track different things — this is realistic.

Beginner: occasional rest_seconds
Intermediate: Wearable data (HRV, deep_sleep), tempo on isolation
Advanced: everything + bar_speed, fiber, caffeine

Also generates orphaned event types (supplement.logged, cardio.logged)
that no handler processes.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timezone

from datagen.exercises import Exercise
from datagen.models import AthleteProfile, AthleteState


# ---------------------------------------------------------------------------
# Novel fields on existing event types
# ---------------------------------------------------------------------------


def novel_set_fields(
    profile: AthleteProfile,
    exercise: Exercise,
    set_type: str,
    rng: random.Random,
) -> dict:
    """Generate novel fields for a set.logged event based on profile."""
    fields: dict = {}
    level = profile.experience_level

    # rest_seconds: all levels, working sets only
    if set_type == "working":
        if level == "beginner" and rng.random() < 0.3:
            fields["rest_seconds"] = rng.choice([60, 90, 120, 150, 180])
        elif level == "intermediate" and rng.random() < 0.6:
            fields["rest_seconds"] = rng.choice([60, 90, 120, 150, 180, 240])
        elif level == "advanced" and rng.random() < 0.8:
            fields["rest_seconds"] = rng.choice([90, 120, 150, 180, 240, 300])

    # tempo: isolation exercises mainly (intermediate + advanced)
    if level in ("intermediate", "advanced"):
        is_isolation = not exercise.is_compound
        if is_isolation:
            prob = 0.8 if level == "intermediate" else 0.9
        elif level == "advanced":
            prob = 0.3  # advanced sometimes tracks tempo on compounds too
        else:
            prob = 0.0

        if prob > 0 and rng.random() < prob:
            fields["tempo"] = rng.choice(["3010", "2010", "4010", "3110", "2011"])

    # bar_speed: compounds only, advanced only, working sets
    if level == "advanced" and exercise.is_compound and set_type == "working":
        if rng.random() < 0.4:
            fields["bar_speed"] = round(rng.uniform(0.3, 1.2), 2)  # m/s

    return fields


def novel_sleep_fields(
    profile: AthleteProfile,
    rng: random.Random,
    duration: float,
) -> dict:
    """Generate novel fields for sleep.logged based on profile (wearable data)."""
    fields: dict = {}
    level = profile.experience_level

    if level in ("intermediate", "advanced"):
        # HRV from wearable — always present when wearable exists
        fields["hrv_rmssd"] = round(rng.gauss(55, 15), 1)
        # Deep sleep percentage
        fields["deep_sleep_pct"] = round(rng.uniform(15, 30), 1)

    if level == "advanced":
        fields["awakenings"] = max(0, round(rng.gauss(1.5, 1.0)))

    return fields


def novel_energy_fields(
    profile: AthleteProfile,
    rng: random.Random,
) -> dict:
    """Generate novel fields for energy.logged based on profile."""
    fields: dict = {}
    level = profile.experience_level

    if level == "intermediate" and rng.random() < 0.5:
        fields["stress_level"] = max(1, min(10, round(rng.gauss(4, 2))))

    elif level == "advanced":
        if rng.random() < 0.8:
            fields["stress_level"] = max(1, min(10, round(rng.gauss(4, 2))))
        if rng.random() < 0.6:
            fields["caffeine_mg"] = rng.choice([0, 0, 100, 200, 200, 300, 400])

    return fields


def novel_meal_fields(
    profile: AthleteProfile,
    rng: random.Random,
    calories: int,
) -> dict:
    """Generate novel fields for meal.logged based on profile."""
    fields: dict = {}

    if profile.experience_level == "advanced" and rng.random() < 0.7:
        # Fiber roughly 14g per 1000 cal, with variance
        fields["fiber_g"] = round(calories / 1000 * 14 * rng.uniform(0.5, 1.5), 1)

    return fields


# ---------------------------------------------------------------------------
# Orphaned event types (no handler exists)
# ---------------------------------------------------------------------------

SUPPLEMENT_CATALOG: list[tuple[str, int, list[str]]] = [
    ("creatine", 5000, ["morning"]),
    ("vitamin_d", 2000, ["morning"]),
    ("omega_3", 1000, ["morning", "evening"]),
    ("magnesium", 400, ["evening"]),
    ("caffeine", 200, ["pre_workout"]),
    ("zinc", 15, ["evening"]),
]

_TIMING_HOURS = {
    "morning": 7,
    "evening": 21,
    "pre_workout": 16,
    "post_workout": 19,
}


def generate_supplements(
    profile: AthleteProfile,
    state: AthleteState,
    rng: random.Random,
    day_offset: int,
) -> list[dict]:
    """Generate supplement.logged events (orphaned — no handler)."""
    if profile.experience_level == "beginner":
        return []

    # Intermediate: 2 supplements, Advanced: 4
    n_supps = 2 if profile.experience_level == "intermediate" else 4
    supplements = SUPPLEMENT_CATALOG[:n_supps]

    events: list[dict] = []
    for name, dose, timings in supplements:
        timing = rng.choice(timings)
        hour = _TIMING_HOURS.get(timing, 8)

        events.append({
            "event_type": "supplement.logged",
            "data": {
                "name": name,
                "dose_mg": dose,
                "timing": timing,
            },
            "occurred_at": datetime.combine(
                state.day, time(hour, rng.choice([0, 15, 30])), tzinfo=timezone.utc,
            ).isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-supp-{name}",
        })

    return events


def generate_cardio(
    profile: AthleteProfile,
    state: AthleteState,
    rng: random.Random,
    day_offset: int,
) -> list[dict]:
    """Generate cardio.logged events on rest days (advanced only, orphaned)."""
    if profile.experience_level != "advanced":
        return []

    # 40% chance of cardio on rest days
    if rng.random() > 0.4:
        return []

    cardio_type = rng.choice(["running", "cycling", "rowing"])
    durations = {"running": (20, 45), "cycling": (30, 60), "rowing": (15, 30)}
    lo, hi = durations[cardio_type]
    duration = rng.randint(lo, hi)
    avg_hr = rng.randint(130, 165)

    return [{
        "event_type": "cardio.logged",
        "data": {
            "type": cardio_type,
            "duration_minutes": duration,
            "avg_heart_rate": avg_hr,
        },
        "occurred_at": datetime.combine(
            state.day, time(rng.randint(8, 10), 0), tzinfo=timezone.utc,
        ).isoformat(),
        "idempotency_key": f"datagen-{profile.name}-d{day_offset}-cardio",
    }]
