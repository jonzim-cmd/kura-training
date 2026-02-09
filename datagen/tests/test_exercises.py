"""Tests for the exercise library."""

from datagen.exercises import EXERCISES, get_exercise, muscle_groups_for_exercises


def test_exercise_count():
    assert len(EXERCISES) == 12


def test_all_exercises_have_required_fields():
    for ex_id, ex in EXERCISES.items():
        assert ex.exercise_id == ex_id
        assert len(ex.alias_de) > 0
        assert len(ex.muscle_groups) > 0
        assert ex.typical_rep_range[0] < ex.typical_rep_range[1]


def test_compound_exercises():
    compounds = [ex for ex in EXERCISES.values() if ex.is_compound]
    isolations = [ex for ex in EXERCISES.values() if not ex.is_compound]
    assert len(compounds) == 9
    assert len(isolations) == 3


def test_get_exercise():
    squat = get_exercise("barbell_back_squat")
    assert squat.alias_de == "Kniebeuge"
    assert squat.is_compound is True
    assert "legs" in squat.muscle_groups


def test_get_exercise_unknown_raises():
    try:
        get_exercise("unknown_exercise")
        assert False, "Should have raised KeyError"
    except KeyError:
        pass


def test_muscle_groups_for_exercises():
    groups = muscle_groups_for_exercises(["barbell_back_squat", "barbell_bench_press"])
    assert "legs" in groups
    assert "chest" in groups
    assert "triceps" in groups


def test_big_four_have_nonzero_ratio():
    for ex_id in ["barbell_back_squat", "barbell_bench_press", "conventional_deadlift", "overhead_press"]:
        assert EXERCISES[ex_id].rm_ratio > 0


def test_bodyweight_exercises_have_zero_ratio():
    for ex_id in ["pull_up", "dip"]:
        assert EXERCISES[ex_id].rm_ratio == 0.0
