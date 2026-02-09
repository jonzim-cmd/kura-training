"""Exercise library — 12 canonical exercises with metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Exercise:
    exercise_id: str
    alias_de: str  # German alias (first-use generates exercise.alias_created)
    muscle_groups: tuple[str, ...]
    is_compound: bool
    typical_rep_range: tuple[int, int]  # (min, max) for working sets
    rm_ratio: float  # ratio relative to squat 1RM for estimating starting 1RM


EXERCISES: dict[str, Exercise] = {
    "barbell_back_squat": Exercise(
        exercise_id="barbell_back_squat",
        alias_de="Kniebeuge",
        muscle_groups=("legs", "glutes"),
        is_compound=True,
        typical_rep_range=(3, 8),
        rm_ratio=1.0,
    ),
    "barbell_bench_press": Exercise(
        exercise_id="barbell_bench_press",
        alias_de="Bankdrücken",
        muscle_groups=("chest", "triceps", "shoulders"),
        is_compound=True,
        typical_rep_range=(3, 8),
        rm_ratio=0.75,
    ),
    "conventional_deadlift": Exercise(
        exercise_id="conventional_deadlift",
        alias_de="Kreuzheben",
        muscle_groups=("back", "legs", "glutes"),
        is_compound=True,
        typical_rep_range=(1, 6),
        rm_ratio=1.25,
    ),
    "overhead_press": Exercise(
        exercise_id="overhead_press",
        alias_de="Schulterdrücken",
        muscle_groups=("shoulders", "triceps"),
        is_compound=True,
        typical_rep_range=(5, 10),
        rm_ratio=0.5,
    ),
    "barbell_row": Exercise(
        exercise_id="barbell_row",
        alias_de="Rudern",
        muscle_groups=("back", "biceps"),
        is_compound=True,
        typical_rep_range=(5, 10),
        rm_ratio=0.6,
    ),
    "pull_up": Exercise(
        exercise_id="pull_up",
        alias_de="Klimmzüge",
        muscle_groups=("back", "biceps"),
        is_compound=True,
        typical_rep_range=(5, 12),
        rm_ratio=0.0,  # bodyweight — not derived from squat
    ),
    "dip": Exercise(
        exercise_id="dip",
        alias_de="Dips",
        muscle_groups=("chest", "triceps"),
        is_compound=True,
        typical_rep_range=(5, 12),
        rm_ratio=0.0,  # bodyweight
    ),
    "leg_press": Exercise(
        exercise_id="leg_press",
        alias_de="Beinpresse",
        muscle_groups=("legs", "glutes"),
        is_compound=True,
        typical_rep_range=(8, 15),
        rm_ratio=1.5,
    ),
    "romanian_deadlift": Exercise(
        exercise_id="romanian_deadlift",
        alias_de="Rumänisches Kreuzheben",
        muscle_groups=("hamstrings", "glutes", "back"),
        is_compound=True,
        typical_rep_range=(6, 12),
        rm_ratio=0.7,
    ),
    "lateral_raise": Exercise(
        exercise_id="lateral_raise",
        alias_de="Seitheben",
        muscle_groups=("shoulders",),
        is_compound=False,
        typical_rep_range=(10, 20),
        rm_ratio=0.1,
    ),
    "barbell_curl": Exercise(
        exercise_id="barbell_curl",
        alias_de="Langhantelcurl",
        muscle_groups=("biceps",),
        is_compound=False,
        typical_rep_range=(8, 15),
        rm_ratio=0.3,
    ),
    "tricep_pushdown": Exercise(
        exercise_id="tricep_pushdown",
        alias_de="Trizepsdrücken",
        muscle_groups=("triceps",),
        is_compound=False,
        typical_rep_range=(10, 15),
        rm_ratio=0.25,
    ),
}


def get_exercise(exercise_id: str) -> Exercise:
    """Get exercise by ID, raises KeyError if not found."""
    return EXERCISES[exercise_id]


def muscle_groups_for_exercises(exercise_ids: list[str]) -> set[str]:
    """Return all muscle groups trained by the given exercises."""
    groups: set[str] = set()
    for ex_id in exercise_ids:
        ex = EXERCISES.get(ex_id)
        if ex:
            groups.update(ex.muscle_groups)
    return groups
