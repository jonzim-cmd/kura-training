"""Pre-built athlete profiles for quick data generation."""

from __future__ import annotations

from datetime import date

from datagen.models import AthleteProfile

BEGINNER = AthleteProfile(
    name="beginner",
    experience_level="beginner",
    bodyweight_kg=78.0,
    training_days_per_week=3,
    exercises=[
        "barbell_back_squat", "barbell_bench_press", "conventional_deadlift",
        "overhead_press", "barbell_row", "pull_up",
        "romanian_deadlift", "lateral_raise", "barbell_curl", "tricep_pushdown",
    ],
    squat_1rm_kg=60.0,
    bench_1rm_kg=45.0,
    deadlift_1rm_kg=80.0,
    ohp_1rm_kg=30.0,
    sleep_avg_hours=7.0,
    sleep_std_hours=1.0,
    calorie_target=2400,
    protein_target_g=140,
    progression_rate=0.015,  # +1.5%/week
    start_date=date(2025, 10, 1),
    seed=100,
)

INTERMEDIATE = AthleteProfile(
    name="intermediate",
    experience_level="intermediate",
    bodyweight_kg=82.0,
    training_days_per_week=4,
    exercises=[
        "barbell_back_squat", "barbell_bench_press", "conventional_deadlift",
        "overhead_press", "barbell_row", "pull_up", "dip",
        "leg_press", "romanian_deadlift", "lateral_raise",
        "barbell_curl", "tricep_pushdown",
    ],
    squat_1rm_kg=120.0,
    bench_1rm_kg=90.0,
    deadlift_1rm_kg=150.0,
    ohp_1rm_kg=60.0,
    sleep_avg_hours=7.5,
    sleep_std_hours=0.8,
    calorie_target=2800,
    protein_target_g=170,
    progression_rate=0.004,  # +0.4%/week
    start_date=date(2025, 10, 1),
    seed=200,
)

ADVANCED = AthleteProfile(
    name="advanced",
    experience_level="advanced",
    bodyweight_kg=88.0,
    training_days_per_week=5,
    exercises=[
        "barbell_back_squat", "barbell_bench_press", "conventional_deadlift",
        "overhead_press", "barbell_row", "pull_up", "dip",
        "leg_press", "romanian_deadlift", "lateral_raise",
        "barbell_curl", "tricep_pushdown",
    ],
    squat_1rm_kg=180.0,
    bench_1rm_kg=140.0,
    deadlift_1rm_kg=220.0,
    ohp_1rm_kg=90.0,
    sleep_avg_hours=8.0,
    sleep_std_hours=0.5,
    calorie_target=3200,
    protein_target_g=200,
    progression_rate=0.0015,  # +0.15%/week
    start_date=date(2025, 10, 1),
    seed=300,
)

PRESETS: dict[str, AthleteProfile] = {
    "beginner": BEGINNER,
    "intermediate": INTERMEDIATE,
    "advanced": ADVANCED,
}
