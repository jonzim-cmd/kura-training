"""Nutrition event generator — meal.logged events.

3-4 meals per day with realistic macro splits, training-day calorie bumps,
and weekend variance.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timezone

from datagen.models import AthleteProfile, AthleteState

# Meal templates: (meal_type, calorie_fraction, hour)
MEAL_TEMPLATES = [
    ("breakfast", 0.25, 8),
    ("lunch", 0.35, 12),
    ("dinner", 0.30, 19),
    ("snack", 0.10, 15),
]


def generate_nutrition(
    profile: AthleteProfile,
    state: AthleteState,
    rng: random.Random,
    day_offset: int,
    is_training_day: bool,
) -> tuple[list[dict], int]:
    """Generate meal.logged events for the day.

    Returns (events, total_calories) — total_calories used for bodyweight trend.
    """
    # Daily calorie target with variance
    variance = rng.gauss(0, 0.15)  # ±15%
    # Weekend: larger variance
    if state.day.weekday() >= 5:
        variance = rng.gauss(0.05, 0.18)  # slightly higher on weekends
    # Training days: +200-300kcal
    training_bump = rng.randint(200, 300) if is_training_day else 0

    daily_calories = int(profile.calorie_target * (1 + variance) + training_bump)
    daily_calories = max(1200, min(5000, daily_calories))

    # Protein target for the day
    daily_protein = profile.protein_target_g * (1 + rng.gauss(0, 0.10))
    daily_protein = max(50, min(400, daily_protein))

    # Decide number of meals (3-4)
    n_meals = 4 if rng.random() < 0.6 else 3
    templates = MEAL_TEMPLATES[:n_meals]

    # Redistribute fractions if 3 meals
    if n_meals == 3:
        templates = [
            ("breakfast", 0.28, 8),
            ("lunch", 0.38, 12),
            ("dinner", 0.34, 19),
        ]

    events: list[dict] = []
    total_cals = 0

    for idx, (meal_type, cal_frac, hour) in enumerate(templates):
        # Calories for this meal
        meal_cals = int(daily_calories * cal_frac * rng.uniform(0.9, 1.1))
        meal_cals = max(100, min(3000, meal_cals))
        total_cals += meal_cals

        # Macros
        # Protein: roughly evenly split across meals
        protein = round(daily_protein / n_meals * rng.uniform(0.8, 1.2), 1)
        protein = max(5, min(150, protein))

        # Carbs and fat from remaining calories
        protein_cals = protein * 4
        remaining_cals = max(0, meal_cals - protein_cals)

        # Carb/fat split: ~55/45 by calories, more carbs around training
        carb_ratio = 0.55
        if is_training_day and meal_type in ("lunch", "dinner"):
            carb_ratio = 0.65

        carbs = round(remaining_cals * carb_ratio / 4, 1)
        fat = round(remaining_cals * (1 - carb_ratio) / 9, 1)

        carbs = max(5, min(400, carbs))
        fat = max(2, min(200, fat))

        minute = rng.choice([0, 15, 30, 45])

        events.append(
            {
                "event_type": "meal.logged",
                "data": {
                    "calories": meal_cals,
                    "protein_g": protein,
                    "carbs_g": carbs,
                    "fat_g": fat,
                    "meal_type": meal_type,
                },
                "occurred_at": datetime.combine(
                    state.day, time(hour, minute), tzinfo=timezone.utc,
                ).isoformat(),
                "idempotency_key": f"datagen-{profile.name}-d{day_offset}-meal-{idx}",
            }
        )

    return events, total_cals
