"""Tests for core data models."""

from datetime import date

from datagen.models import AthleteProfile, AthleteState


def _make_profile(**overrides) -> AthleteProfile:
    defaults = dict(
        name="test",
        experience_level="intermediate",
        bodyweight_kg=82.0,
        training_days_per_week=4,
        exercises=[
            "barbell_back_squat",
            "barbell_bench_press",
            "conventional_deadlift",
            "overhead_press",
            "barbell_row",
            "pull_up",
        ],
        squat_1rm_kg=120.0,
        bench_1rm_kg=90.0,
        deadlift_1rm_kg=150.0,
        ohp_1rm_kg=60.0,
        sleep_avg_hours=7.5,
        sleep_std_hours=0.8,
        calorie_target=2800,
        protein_target_g=170,
        progression_rate=0.004,
        start_date=date(2025, 10, 1),
        seed=42,
    )
    defaults.update(overrides)
    return AthleteProfile(**defaults)


def test_profile_is_frozen():
    p = _make_profile()
    try:
        p.name = "other"
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_starting_1rms_big_four():
    p = _make_profile()
    rms = p.starting_1rms
    assert rms["barbell_back_squat"] == 120.0
    assert rms["barbell_bench_press"] == 90.0
    assert rms["conventional_deadlift"] == 150.0
    assert rms["overhead_press"] == 60.0


def test_starting_1rms_derived():
    p = _make_profile(exercises=["barbell_back_squat", "barbell_row"])
    rms = p.starting_1rms
    # barbell_row has rm_ratio=0.6, so derived from squat: 120 * 0.6 = 72
    assert rms["barbell_row"] == 72.0


def test_state_from_profile():
    p = _make_profile()
    s = AthleteState.from_profile(p)
    assert s.day == date(2025, 10, 1)
    assert s.fatigue == 0.0
    assert s.bodyweight_kg == 82.0
    assert s.mesocycle_week == 1
    assert len(s.current_1rms) == len(p.exercises)
    assert s.current_1rms["pull_up"] == 0.0  # bodyweight exercise, ratio 0
    assert "barbell_back_squat" in s.current_1rms


def test_advance_day_basic():
    p = _make_profile(start_date=date(2025, 10, 1))  # Wednesday
    s = AthleteState.from_profile(p)
    s.advance_day()
    assert s.day == date(2025, 10, 2)
    assert s.total_days == 1


def test_advance_day_mesocycle_wraps():
    p = _make_profile()
    s = AthleteState.from_profile(p)
    for _ in range(28):
        s.advance_day()
    assert s.mesocycle_day == 0
    assert s.mesocycle_week == 1


def test_advance_day_soreness_decays():
    p = _make_profile()
    s = AthleteState.from_profile(p)
    s.soreness = {"legs": 3.0, "chest": 1.0}
    s.advance_day()
    assert s.soreness["legs"] < 3.0
    # chest at 1.0 * 0.6 = 0.6, still >= 0.5
    assert "chest" in s.soreness
    # After several days, small values disappear
    for _ in range(5):
        s.advance_day()
    assert "chest" not in s.soreness


def test_weekly_volume_resets_on_monday():
    p = _make_profile(start_date=date(2025, 9, 29))  # Monday
    s = AthleteState.from_profile(p)
    s.weekly_volume = {"barbell_back_squat": 15}
    # Advance through the week to next Monday
    for _ in range(7):
        s.advance_day()
    assert s.weekly_volume == {}
