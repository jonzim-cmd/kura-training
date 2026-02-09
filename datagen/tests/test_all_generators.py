"""Tests for recovery, body composition, nutrition, profile, targets, and plan generators."""

import random
from datetime import date

from datagen.generators.body_composition import (
    generate_bodyweight,
    generate_measurements,
    update_bodyweight_trend,
)
from datagen.generators.nutrition import generate_nutrition
from datagen.generators.plan import generate_training_plan
from datagen.generators.profile import (
    generate_goal_event,
    generate_injury_event,
    generate_profile_events,
)
from datagen.generators.recovery import generate_energy, generate_sleep, generate_soreness
from datagen.generators.targets import generate_target_events
from datagen.models import AthleteProfile, AthleteState


def _make_profile(**overrides) -> AthleteProfile:
    defaults = dict(
        name="test",
        experience_level="intermediate",
        bodyweight_kg=82.0,
        training_days_per_week=4,
        exercises=[
            "barbell_back_squat", "barbell_bench_press", "conventional_deadlift",
            "overhead_press", "barbell_row", "pull_up", "leg_press",
            "romanian_deadlift", "lateral_raise", "barbell_curl", "tricep_pushdown",
        ],
        squat_1rm_kg=120.0, bench_1rm_kg=90.0, deadlift_1rm_kg=150.0, ohp_1rm_kg=60.0,
        sleep_avg_hours=7.5, sleep_std_hours=0.8,
        calorie_target=2800, protein_target_g=170, progression_rate=0.004,
        start_date=date(2025, 10, 1), seed=42,
    )
    defaults.update(overrides)
    return AthleteProfile(**defaults)


def _make_state(profile: AthleteProfile) -> AthleteState:
    return AthleteState.from_profile(profile)


class TestSleep:
    def test_generates_one_event(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_sleep(p, s, random.Random(42), 0)
        assert len(events) == 1
        assert events[0]["event_type"] == "sleep.logged"

    def test_sleep_data_fields(self):
        p = _make_profile()
        s = _make_state(p)
        event = generate_sleep(p, s, random.Random(42), 0)[0]
        data = event["data"]
        assert "duration_hours" in data
        assert "quality" in data
        assert "bed_time" in data
        assert "wake_time" in data
        assert 0 < data["duration_hours"] <= 20

    def test_sleep_quality_mapping(self):
        p = _make_profile(sleep_avg_hours=5.5, sleep_std_hours=0.1)
        s = _make_state(p)
        event = generate_sleep(p, s, random.Random(1), 0)[0]
        assert event["data"]["quality"] in ("poor", "fair", "good", "excellent")

    def test_sleep_duration_plausible(self):
        p = _make_profile()
        s = _make_state(p)
        for seed in range(50):
            event = generate_sleep(p, s, random.Random(seed), 0)[0]
            assert 0 < event["data"]["duration_hours"] <= 20


class TestSoreness:
    def test_no_soreness_without_training(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_soreness(p, s, random.Random(42), 1, trained_exercises_yesterday=None)
        assert events == []

    def test_soreness_after_training(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_soreness(
            p, s, random.Random(42), 1,
            trained_exercises_yesterday=["barbell_back_squat", "barbell_bench_press"],
        )
        assert len(events) > 0
        for event in events:
            assert event["event_type"] == "soreness.logged"
            assert 1 <= event["data"]["severity"] <= 5

    def test_soreness_updates_state(self):
        p = _make_profile()
        s = _make_state(p)
        generate_soreness(p, s, random.Random(42), 1, trained_exercises_yesterday=["barbell_back_squat"])
        assert len(s.soreness) > 0


class TestEnergy:
    def test_generates_one_event(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_energy(p, s, random.Random(42), 0, is_training_day=False)
        assert len(events) == 1
        assert events[0]["event_type"] == "energy.logged"

    def test_energy_level_range(self):
        p = _make_profile()
        s = _make_state(p)
        for seed in range(50):
            event = generate_energy(p, s, random.Random(seed), 0, is_training_day=False)[0]
            assert 1 <= event["data"]["level"] <= 10

    def test_training_day_time_of_day(self):
        p = _make_profile()
        s = _make_state(p)
        event = generate_energy(p, s, random.Random(42), 0, is_training_day=True)[0]
        assert event["data"]["time_of_day"] == "pre_workout"


class TestBodyweight:
    def test_generates_one_event(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_bodyweight(p, s, random.Random(42), 0)
        assert len(events) == 1
        assert events[0]["event_type"] == "bodyweight.logged"

    def test_weight_plausible(self):
        p = _make_profile()
        s = _make_state(p)
        for seed in range(50):
            event = generate_bodyweight(p, s, random.Random(seed), 0)[0]
            assert 20 <= event["data"]["weight_kg"] <= 300

    def test_bodyweight_trend(self):
        s = AthleteState(
            day=date(2025, 10, 1), fatigue=0.0, current_1rms={},
            bodyweight_kg=82.0, sleep_debt=0.0, energy_baseline=7.0,
            soreness={}, mesocycle_week=1, mesocycle_day=0,
            weekly_volume={}, training_day_index=0, aliases_created=set(), total_days=0,
        )
        # 500kcal surplus daily for 7 days ≈ +0.45kg
        for _ in range(7):
            update_bodyweight_trend(s, calorie_target=2800, actual_calories=3300)
        assert s.bodyweight_kg > 82.0
        assert s.bodyweight_kg < 83.0


class TestMeasurements:
    def test_no_measurement_most_days(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_measurements(p, s, random.Random(42), day_offset=3)
        assert events == []

    def test_measurement_format(self):
        p = _make_profile()
        s = _make_state(p)
        # day_offset=0 should generate
        events = generate_measurements(p, s, random.Random(42), day_offset=0)
        # day_offset=0 is a special case in the code — let's check day 14-28 range
        # We need to find a day_offset that triggers
        found = False
        for d in range(100):
            events = generate_measurements(p, s, random.Random(42), day_offset=d)
            if events:
                found = True
                for event in events:
                    assert event["event_type"] == "measurement.logged"
                    assert 1 <= event["data"]["value_cm"] <= 300
                break
        assert found, "Should generate measurements at some point"


class TestNutrition:
    def test_generates_meals(self):
        p = _make_profile()
        s = _make_state(p)
        events, total_cals = generate_nutrition(p, s, random.Random(42), 0, is_training_day=False)
        assert 3 <= len(events) <= 4

    def test_meal_data_fields(self):
        p = _make_profile()
        s = _make_state(p)
        events, _ = generate_nutrition(p, s, random.Random(42), 0, is_training_day=False)
        for event in events:
            data = event["data"]
            assert "calories" in data
            assert "protein_g" in data
            assert "carbs_g" in data
            assert "fat_g" in data
            assert "meal_type" in data
            assert 0 <= data["calories"] <= 5000
            assert 0 <= data["protein_g"] <= 500
            assert 0 <= data["carbs_g"] <= 500
            assert 0 <= data["fat_g"] <= 500

    def test_training_day_more_calories(self):
        p = _make_profile()
        s = _make_state(p)
        rng_rest = random.Random(42)
        rng_train = random.Random(42)
        _, rest_cals = generate_nutrition(p, s, rng_rest, 0, is_training_day=False)
        _, train_cals = generate_nutrition(p, s, rng_train, 0, is_training_day=True)
        # Training days should generally be higher
        assert train_cals > rest_cals


class TestProfile:
    def test_profile_event(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_profile_events(p, s, 0)
        assert len(events) == 1
        assert events[0]["event_type"] == "profile.updated"
        assert events[0]["data"]["experience_level"] == "intermediate"

    def test_goal_event(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_goal_event(p, s, 0)
        assert len(events) == 1
        assert events[0]["event_type"] == "goal.set"
        assert events[0]["data"]["target_1rm_kg"] == round(120.0 * 1.10, 1)

    def test_injury_event(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_injury_event(p, s, 50)
        assert len(events) == 1
        assert events[0]["event_type"] == "injury.reported"
        assert events[0]["data"]["severity"] == "mild"


class TestTargets:
    def test_generates_three_targets(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_target_events(p, s, 0)
        assert len(events) == 3
        types = {e["event_type"] for e in events}
        assert types == {"weight_target.set", "sleep_target.set", "nutrition_target.set"}


class TestPlan:
    def test_generates_plan(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_training_plan(p, s, 0)
        assert len(events) == 1
        assert events[0]["event_type"] == "training_plan.created"
        sessions = events[0]["data"]["sessions"]
        assert len(sessions) == p.training_days_per_week

    def test_plan_sessions_have_exercises(self):
        p = _make_profile()
        s = _make_state(p)
        events = generate_training_plan(p, s, 0)
        for session in events[0]["data"]["sessions"]:
            assert "day" in session
            assert "name" in session
            assert len(session["exercises"]) > 0
            for ex in session["exercises"]:
                assert "exercise_id" in ex
                assert "exercise" in ex
